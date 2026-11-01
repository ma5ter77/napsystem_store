"""Deterministic offline render of a project revision (spec v2.1 §2, §6).

Sources are decoded once to float64 at the project rate (ffmpeg; soxr only when the source rate differs — recorded
in the report). Clips are summed per logical bus in float64 blocks, buses are routed to output channels by the
profile matrix, and PCM is written with an explicit WAVE_FORMAT_EXTENSIBLE channel mask.
No implicit processing: 0 dB gain is not multiplied, fades/gains exist only where the project says so, no
normalisation, no limiter. Overload is an error unless the project explicitly chose `on_clip: hard_clip`.
The same function renders previews (a window) and full exports, so preview == export for that window.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import wave
from dataclasses import dataclass, field

import numpy as np

from ..dsp.engine import TruePeakMeter, lin_to_db
from ..dsp.media import MediaError, probe
from .generators import binaural, isochronic
from .schema import PROFILES, ProjectState, channel_mask

BLOCK = 65_536
WAV_LIMIT = 0xFFFFFFFF - 1024          # RIFF size field; RF64 is M2
SUBTYPE_PCM = bytes.fromhex("0100000000001000800000aa00389b71")
SUBTYPE_FLOAT = bytes.fromhex("0300000000001000800000aa00389b71")


class RenderError(Exception):
    def __init__(self, code: str, detail: str = "", data: dict | None = None):
        super().__init__(code)
        self.code, self.detail, self.data = code, detail, data or {}


@dataclass
class Source:
    path: str            # float64 interleaved spool at the project rate
    channels: int
    frames: int
    meta: dict = field(default_factory=dict)

    def read(self, ch: int, start: int, n: int) -> np.ndarray:
        out = np.zeros(n)
        if ch >= self.channels or start >= self.frames or n <= 0:
            return out
        k = min(n, self.frames - start)
        mm = np.memmap(self.path, dtype="<f8", mode="r", shape=(self.frames, self.channels))
        out[:k] = mm[start:start + k, ch]
        del mm
        return out


def decode_source(src_path: str, cache_dir: str, key: str, fs: int) -> Source:
    """Decode (and, only if needed, resample) to a float64 spool; cached by asset key + rate."""
    os.makedirs(cache_dir, exist_ok=True)
    spool = os.path.join(cache_dir, f"{key}-{fs}.f64")
    meta_path = spool + ".json"
    if os.path.exists(spool) and os.path.exists(meta_path):
        meta = json.load(open(meta_path))
        return Source(spool, meta["channels"], meta["frames"], meta)
    pr = probe(src_path)
    ch = pr.channels
    if ch > 8:
        raise RenderError("too_many_channels", str(ch))
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-protocol_whitelist", "file,pipe",
           "-i", "file:" + src_path, "-map", "0:a:0", "-vn"]
    src = None
    if pr.sample_rate != fs:
        cmd += ["-af", f"aresample={fs}:resampler=soxr:precision=28:dither_method=0"]
        src = {"from_hz": pr.sample_rate, "to_hz": fs, "resampler": "soxr precision=28 (ffmpeg aresample)"}
    tmp = spool + ".part"
    cmd += ["-f", "f64le", "-acodec", "pcm_f64le", "pipe:1"]
    with open(tmp, "wb") as out:
        r = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=3600)
    if r.returncode != 0:
        os.remove(tmp)
        raise RenderError("decode_failed", r.stderr.decode(errors="replace")[-300:])
    frames = os.path.getsize(tmp) // (8 * ch)
    os.replace(tmp, spool)
    meta = {"channels": ch, "frames": frames, "source_rate": pr.sample_rate, "codec": pr.codec, "src": src}
    json.dump(meta, open(meta_path, "w"))
    return Source(spool, ch, frames, meta)


def wav_header(frames: int, channels: int, fs: int, bits: int, mask: int) -> bytes:
    flt = bits == 32
    bps = bits // 8
    data = frames * channels * bps
    if data + 80 > WAV_LIMIT:
        raise RenderError("too_large_for_wav", "RF64 output arrives in M2; shorten the project or use fewer channels")
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, fs, fs * channels * bps, channels * bps, bits, 22, bits, mask) \
        + (SUBTYPE_FLOAT if flt else SUBTYPE_PCM)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + data) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt))
            + fmt + b"data" + struct.pack("<I", data))


def quantize(y: np.ndarray, bits: int, hard_clip: bool) -> tuple[bytes, int]:
    """float64 → PCM. Integer scale is 2^(bits-1), the same convention ffmpeg uses when decoding, so an untouched
    integer source round-trips bit-exactly. Returns (bytes, number of samples beyond full scale)."""
    if bits == 32:
        over = int(np.count_nonzero(np.abs(y) > 1.0))
        z = np.clip(y, -1.0, 1.0) if hard_clip else y
        return z.astype("<f4").tobytes(), over
    scale = float(1 << (bits - 1))
    q = np.rint(y * scale)
    lo, hi = -scale, scale - 1
    over = int(np.count_nonzero((q > hi) | (q < lo)))
    q = np.clip(q, lo, hi).astype("<i4")
    if bits == 16:
        return q.astype("<i2").tobytes(), over
    b = q.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes(), over


@dataclass
class RenderResult:
    path: str
    frames: int
    channels: int
    report: dict


def render(state_dict: dict, *, fs: int, bits: int, profile_key: str, sources: dict[str, Source], out_path: str,
           start: int = 0, frames: int | None = None, progress=lambda f: None, check=lambda: None) -> RenderResult:
    state = ProjectState.model_validate(state_dict)
    profile = PROFILES[profile_key]
    mask = channel_mask(profile)
    out_ch = len(profile["channels"])
    total = state.duration_frames() if frames is None else frames
    if total <= 0:
        raise RenderError("empty_project", "add at least one clip")
    gens = {g.id: (g.type, g.parsed()) for g in state.generators}
    solo = any(t.solo for t in state.tracks)
    tracks = [t for t in state.tracks if not t.mute and (t.solo or not solo)]
    for t in tracks:
        for c in t.clips:
            if c.asset_id and c.asset_id not in sources:
                raise RenderError("missing_source", c.asset_id)
    hard = state.output.on_clip == "hard_clip"
    meters = [TruePeakMeter(1, fs) if fs in (44_100, 48_000) else None for _ in range(out_ch)]
    sq = np.zeros(out_ch)
    peak = np.zeros(out_ch)
    over = np.zeros(out_ch, dtype=np.int64)
    first_over: dict[int, int] = {}
    sha = hashlib.sha256()
    done = 0
    with open(out_path, "wb") as fh:
        head = wav_header(total, out_ch, fs, bits, mask)
        fh.write(head)
        sha.update(head)
        while done < total:
            check()
            n = min(BLOCK, total - done)
            b0 = start + done
            buses: dict[str, np.ndarray] = {}
            for t in tracks:
                for c in t.clips:
                    if c.mute:
                        continue
                    s0, s1 = max(b0, c.start_frame), min(b0 + n, c.start_frame + c.duration_frames)
                    if s1 <= s0:
                        continue
                    rel = s0 - c.start_frame       # clip-relative first frame
                    m = s1 - s0
                    if c.asset_id:
                        x = sources[c.asset_id].read(c.source_channel, c.source_offset_frame + rel, m)
                    else:
                        gtype, gp = gens[c.generator_id]
                        if gtype == "binaural":
                            left, right = binaural(gp, fs, rel, m, c.duration_frames)
                            x = left if c.generator_output in ("left", "mono") else right
                        else:
                            x = isochronic(gp, fs, rel, m, c.duration_frames)
                    if c.gain_db != 0.0:
                        x = x * (10.0 ** (c.gain_db / 20.0))
                    if c.fade_in_frames or c.fade_out_frames:
                        k = np.arange(rel, rel + m, dtype=np.float64)
                        env = np.ones(m)
                        if c.fade_in_frames:
                            a = k < c.fade_in_frames
                            env[a] = np.sin(0.5 * math.pi * k[a] / c.fade_in_frames) ** 2
                        if c.fade_out_frames:
                            last = c.duration_frames - 1
                            b = k > last - c.fade_out_frames
                            env[b] = np.minimum(env[b], np.sin(0.5 * math.pi * (last - k[b]) / c.fade_out_frames) ** 2)
                        x = x * env
                    buf = buses.setdefault(t.bus, np.zeros(n))
                    buf[s0 - b0:s1 - b0] += x
            y = np.zeros((n, out_ch))
            for i, row in enumerate(profile["matrix"]):
                for bus, g in row.items():
                    if bus in buses:
                        y[:, i] += buses[bus] if g == 1.0 else g * buses[bus]
            if not np.all(np.isfinite(y)):
                raise RenderError("non_finite", "numerical error in the mix")
            data, _ = quantize(y, bits, True)
            if bits == 32:
                back = np.frombuffer(data, dtype="<f4").reshape(n, out_ch).astype(np.float64)
                ov = np.abs(y) > 1.0
            else:
                scale = float(1 << (bits - 1))
                q = np.rint(y * scale)
                ov = (q > scale - 1) | (q < -scale)
                back = np.clip(q, -scale, scale - 1) / scale
            for i in range(out_ch):
                cnt = int(ov[:, i].sum())
                if cnt:
                    over[i] += cnt
                    first_over.setdefault(i, b0 + int(np.argmax(ov[:, i])))
                if meters[i] is not None:
                    meters[i].feed(back[:, i:i + 1])
                peak[i] = max(peak[i], float(np.max(np.abs(back[:, i]))))
                sq[i] += float(np.dot(back[:, i], back[:, i]))
            if over.sum() and not hard:
                fh.close()
                os.remove(out_path)
                raise RenderError("clipping", "the mix exceeds full scale; lower clip gains or allow hard clipping "
                                  "explicitly in the project output settings",
                                  {"channels": {profile["channels"][i]: {"samples": int(over[i]),
                                                                         "first_frame": first_over[i]}
                                                for i in first_over}})
            fh.write(data)
            sha.update(data)
            done += n
            progress(done / total)
    channels = []
    for i, name in enumerate(profile["channels"]):
        tp = meters[i].dbtp if meters[i] is not None else None
        channels.append({"channel": name, "sample_peak_dbfs": _r(lin_to_db(peak[i])),
                         "true_peak_dbtp": _r(tp) if tp is not None else "not_measured_at_this_rate",
                         "rms_dbfs": _r(lin_to_db(math.sqrt(sq[i] / total))), "silent": bool(peak[i] == 0.0),
                         "clipped_samples": int(over[i])})
    report = {
        "profile": profile_key, "profile_note": profile["note"], "layout": profile["layout"],
        "channel_order": profile["channels"], "channel_mask": hex(mask), "sample_rate": fs, "bit_depth": bits,
        "format": "WAV IEEE float" if bits == 32 else "WAV PCM", "frames": total, "start_frame": start,
        "duration_seconds": round(total / fs, 6), "sha256": sha.hexdigest(), "channels": channels,
        "on_clip": state.output.on_clip, "hard_clipped_samples": int(over.sum()),
        "processing": {"implicit": "none — no normalisation, EQ, limiter or dither",
                       "src": {k: s.meta.get("src") for k, s in sources.items() if s.meta.get("src")}},
    }
    return RenderResult(out_path, total, out_ch, _plain(report))


def verify(path: str, expect: RenderResult) -> dict:
    """Independent reader: stdlib `wave` (PCM) or the RIFF header (float) + ffprobe for the channel layout."""
    rep = {}
    if expect.report["bit_depth"] != 32:
        with wave.open(path, "rb") as w:
            rep = {"frames": w.getnframes(), "channels": w.getnchannels(), "sample_rate": w.getframerate(),
                   "bits": w.getsampwidth() * 8}
    else:                                              # stdlib wave cannot read float: walk the RIFF chunks
        ch = fs = bits = data = None
        with open(path, "rb") as fh:
            if fh.read(12)[8:12] != b"WAVE":
                raise RenderError("verify_failed", "not a WAVE file")
            while True:
                hd = fh.read(8)
                if len(hd) < 8:
                    break
                cid, size = hd[:4], struct.unpack("<I", hd[4:])[0]
                if cid == b"fmt ":
                    body = fh.read(size)
                    ch, fs = struct.unpack("<HI", body[2:8])
                    bits = struct.unpack("<H", body[14:16])[0]
                    if size & 1:
                        fh.seek(1, 1)
                elif cid == b"data":
                    data = size
                    break
                else:
                    fh.seek(size + (size & 1), 1)
        rep = {"frames": (data or 0) // ((ch or 1) * 4), "channels": ch, "sample_rate": fs, "bits": bits}
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path], capture_output=True, text=True)
    st = (json.loads(pr.stdout or "{}").get("streams") or [{}])[0]
    rep["ffprobe_layout"] = st.get("channel_layout")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    rep["sha256"] = h.hexdigest()
    ok = (rep["frames"] == expect.frames and rep["channels"] == expect.channels
          and rep["sample_rate"] == expect.report["sample_rate"] and rep["bits"] == expect.report["bit_depth"]
          and rep["sha256"] == expect.report["sha256"])
    rep["passed"] = bool(ok)
    return rep


def _plain(o):
    """Report → plain JSON types (numpy scalars would break JSONB storage)."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


def _r(x):
    return round(x, 3) if x is not None and math.isfinite(x) else "-inf"


def source_meta_from_probe(path: str) -> dict:
    """Upload-time metadata (spec §2.1): codec, channels, rate, bit depth, layout, duration."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-select_streams", "a:0", "-of",
                        "json", path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise MediaError("unsupported_or_corrupt", r.stderr[-300:])
    info = json.loads(r.stdout or "{}")
    st = (info.get("streams") or [{}])[0]
    fmt = info.get("format") or {}
    bits = st.get("bits_per_raw_sample") or st.get("bits_per_sample")
    return {"codec": st.get("codec_name"), "channels": int(st.get("channels") or 0),
            "sample_rate": int(st.get("sample_rate") or 0), "bit_depth": int(bits) if bits and str(bits) != "0" else None,
            "channel_layout": st.get("channel_layout"), "duration": float(fmt.get("duration") or st.get("duration") or 0),
            "format": fmt.get("format_name"), "lossy": st.get("codec_name") in ("mp3", "aac", "vorbis", "opus")}
