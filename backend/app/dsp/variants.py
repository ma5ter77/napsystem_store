"""NAP-063 / NAP-108: conversion pipeline for the equipment layouts — headphones 2.0, speakers 3.0,
NAP chair 5.1 and 7.1.

Same stages and guarantees as pipeline.convert (block processing, two passes, common gain only, true peak
<= -1 dBTP, 24-bit master, bit-exact FLAC delivery), plus: an optional file of the author's own for any channel
(otherwise the channel is derived from the main track), per-channel QA (own file or derived, true peak, RMS)
and pilot-tone verification of shift and channel placement.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct

import numpy as np

from .engine import FS, BinauralRenderer, LoudnessMeter, Profile, TruePeakMeter, analyze_layer, db_to_lin, lin_to_db
from .media import Decoder, MediaError, free_bytes, probe, run_ffmpeg, tool_versions
from .modes import FFMPEG_LAYOUT, LAYOUTS, SHIFT_ENGINE_VERSION, WAVE_MASK, renderer_for, verify_mode
from .pipeline import (BLOCK, INT24_MAX, TP_CEILING_DBTP, TP_MARGIN_DB, Limits, Outputs, _decoded_pcm_sha,
                       _to_int24_bytes, sha256_file)

MODES = tuple(LAYOUTS)
# ITU-R BS.1770 channel weights: LFE excluded, surround channels +1.5 dB
_W = {"FL": 1.0, "FR": 1.0, "FC": 1.0, "LFE": 0.0, "SL": 1.41, "SR": 1.41, "BL": 1.41, "BR": 1.41}
BS1770_WEIGHTS = {m: [_W[c] for c in ch] for m, ch in LAYOUTS.items()}
KSDATAFORMAT_SUBTYPE_PCM = bytes.fromhex("0100000000001000800000aa00389b71")


def channel_mask(names: list[str]) -> int:
    m = 0
    for c in names:
        m |= WAVE_MASK[c]
    return m


def wav_header(frames: int, channels: int, bits: int = 24, fs: int = FS, names: list[str] | None = None) -> bytes:
    """PCM WAV; more than two channels use WAVE_FORMAT_EXTENSIBLE with the layout's speaker mask."""
    bps = bits // 8
    data = frames * channels * bps
    if channels <= 2:
        fmt = struct.pack("<HHIIHH", 1, channels, fs, fs * channels * bps, channels * bps, bits)
    else:
        if not names or len(names) != channels:
            raise ValueError("channel names are required above stereo")
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, fs, fs * channels * bps, channels * bps, bits,
                          22, bits, channel_mask(names)) + KSDATAFORMAT_SUBTYPE_PCM
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + data) + b"WAVE" +
            b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", data))


def _decode(path, workdir, name, channels_out, limits, check):
    """Decode to a float32 spool with `channels_out` channels (ffmpeg downmix/upmix). Returns (spool, frames, probe)."""
    size = os.path.getsize(path)
    if size == 0:
        raise MediaError("empty_file", name)
    if size > limits.max_bytes:
        raise MediaError("file_too_large", name)
    pr = probe(path)
    if pr.channels > 2:
        raise MediaError("too_many_channels", f"{name}: {pr.channels}")
    if pr.duration <= 0:
        raise MediaError("empty_audio", name)
    if pr.duration > limits.max_seconds:
        raise MediaError("too_long", name)
    spool = os.path.join(workdir, f"decoded-{name}.f32")
    dec = Decoder(path, channels_out, limits.cpu_seconds, limits.memory_bytes)
    frames, bad, fb, pending = 0, 0, channels_out * 4, b""
    try:
        with open(spool, "wb") as out:
            while True:
                chunk = dec.proc.stdout.read(BLOCK * fb)
                if not chunk:
                    break
                pending += chunk
                usable = len(pending) - len(pending) % fb
                arr = np.frombuffer(pending[:usable], dtype="<f4")
                bad += int(np.count_nonzero(~np.isfinite(arr)))
                out.write(pending[:usable])
                pending = pending[usable:]
                frames += usable // fb
                check()
        dec.finish()
    except BaseException:
        dec.kill()
        raise
    if frames == 0:
        raise MediaError("empty_audio", name)
    if bad:
        raise MediaError("decode_failed", f"{name}: non-finite samples")
    if frames < pr.duration * FS * 0.98 - FS * 0.05:
        raise MediaError("truncated_audio", name)
    return spool, frames, pr


class _Reader:
    """Block reader over a spool: zero-padded past its end, truncated at `total`."""

    def __init__(self, path, channels, frames):
        self.fh = open(path, "rb")
        self.ch = channels
        self.left = frames

    def read(self, n):
        k = min(n, self.left)
        out = np.zeros((n, self.ch))
        if k > 0:
            out[:k] = np.frombuffer(self.fh.read(k * self.ch * 4), dtype="<f4").reshape(-1, self.ch)
            self.left -= k
        return out

    def close(self):
        self.fh.close()


def _render(mode, profile, spools, frames, stems_present, add_layer=False):
    """Yields aligned output blocks (n, C) totalling exactly `frames` (renderer latency removed).
    NAP-117: with add_layer the carrier layer (left f_c - b/2, right f_c + b/2, with fades) is added to FL / FR on
    top of the shifted track, and the track is lowered by the profile's source gain to make room for it."""
    r = renderer_for(mode, profile, stems_present)
    lay = BinauralRenderer(profile, frames) if add_layer else None
    src_gain = db_to_lin(profile.source_gain_db) if add_layer else 1.0
    readers = {k: _Reader(*v) for k, v in spools.items()}
    skip = r.latency
    fed = 0
    try:
        while fed < frames + r.latency:
            n = min(BLOCK, frames + r.latency - fed)
            main = readers["main"].read(n) if fed < frames else np.zeros((n, 2))
            if fed + n > frames:  # zero the padding past the end of the main track (incl. stems)
                main[max(0, frames - fed):] = 0.0
            stems = {}
            for k in stems_present:
                s = readers[k].read(n)
                if fed + n > frames:
                    s[max(0, frames - fed):] = 0.0
                stems[k] = s
            y = r.process(main, stems)
            fed += n
            if skip:
                drop = min(skip, len(y))
                y = y[drop:]
                skip -= drop
            if len(y):
                if lay is not None:
                    y = y * src_gain
                    y[:, 0:2] += lay.layer(len(y))
                yield y
    finally:
        for rd in readers.values():
            rd.close()


def convert_variant(src_path: str, workdir: str, profile_dict: dict, limits: Limits, mode: str,
                    stem_paths: dict[str, str] | None = None, progress=lambda s, f: None,
                    check=lambda: None, add_layer: bool = False) -> Outputs:
    if mode not in MODES:
        raise MediaError("unknown_mode", mode)
    stem_paths = {k: v for k, v in (stem_paths or {}).items() if v}
    names = LAYOUTS[mode]
    if set(stem_paths) - set(names):
        raise MediaError("unknown_stem", ",".join(sorted(set(stem_paths) - set(names))))
    os.makedirs(workdir, exist_ok=True)
    profile = Profile.from_dict(profile_dict)
    if add_layer and not profile.layer_enabled:
        raise MediaError("layer_needs_level", "the profile has no carrier level")
    nch = len(names)

    progress("validating", 0.0)
    total_in = os.path.getsize(src_path) + sum(os.path.getsize(p) for p in stem_paths.values())
    pr0 = probe(src_path)
    est = int(math.ceil(pr0.duration * FS * 1.01)) + FS
    need = est * (2 * 4 + nch * 3 * 2 + len(stem_paths) * 4) + limits.disk_reserve
    if free_bytes(workdir) < need:
        raise MediaError("insufficient_disk", f"need {need}", retryable=True)
    progress("validating", 1.0)

    progress("decoding", 0.0)
    main_spool, frames, pr = _decode(src_path, workdir, "main", 2, limits, check)
    spools = {"main": (main_spool, 2, frames)}
    stem_info = {}
    for i, (k, p) in enumerate(sorted(stem_paths.items())):
        sp, sf, spr = _decode(p, workdir, k, 1, limits, check)   # one channel's file: mono (stereo is downmixed)
        spools[k] = (sp, 1, sf)
        stem_info[k] = {"sha256": sha256_file(p), "frames": sf, "probe": spr.to_dict(),
                        "fit": "padded" if sf < frames else ("truncated" if sf > frames else "exact")}
        progress("decoding", (i + 1) / (len(stem_paths) + 1))
    progress("decoding", 1.0)
    check()

    # pass 1: measure
    progress("rendering", 0.0)
    tp_pre = TruePeakMeter(nch)
    done = 0
    for y in _render(mode, profile, spools, frames, set(stem_paths), add_layer):
        if not np.all(np.isfinite(y)):
            raise MediaError("render_non_finite")
        tp_pre.feed(y)
        done += len(y)
        progress("rendering", done / frames)
        check()

    progress("quality_check", 0.0)
    verification = verify_mode(mode, profile, set(stem_paths))
    if not verification["passed"]:
        raise MediaError("mode_verification_failed", json.dumps(verification["checks"])[:400])
    pre_db = tp_pre.dbtp
    target = TP_CEILING_DBTP - TP_MARGIN_DB
    common_gain_db = 0.0 if pre_db <= target else target - pre_db
    gain = db_to_lin(common_gain_db)
    progress("quality_check", 1.0)

    # pass 2: write master + per-channel QA on the quantised signal
    progress("packaging", 0.0)
    master = os.path.join(workdir, "master.wav")
    tp_ch = [TruePeakMeter(1) for _ in range(nch)]
    sq = np.zeros(nch)
    lufs = LoudnessMeter(nch)
    pcm_hash = hashlib.sha256()
    clipped = 0
    done = 0
    with open(master, "wb") as fh:
        fh.write(wav_header(frames, nch, names=names))
        for y in _render(mode, profile, spools, frames, set(stem_paths), add_layer):
            y = y * gain
            data, c = _to_int24_bytes(y)
            clipped += c
            q = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
            back = (q[:, 0].astype(np.int32) | (q[:, 1].astype(np.int32) << 8) |
                    (q[:, 2].astype(np.int8).astype(np.int32) << 16)).reshape(-1, nch) / INT24_MAX
            for i in range(nch):
                tp_ch[i].feed(back[:, i:i + 1])
            sq += (back * back).sum(axis=0)
            lufs.feed(back)
            pcm_hash.update(data)
            fh.write(data)
            done += len(y)
            progress("packaging", 0.7 * done / frames)
            check()
    for sp, _, _ in spools.values():
        os.remove(sp)
    if clipped:
        raise MediaError("clipping_detected", str(clipped))
    tp_master = max(m.dbtp for m in tp_ch)
    if tp_master > TP_CEILING_DBTP:
        raise MediaError("true_peak_exceeded", f"{tp_master:.3f}")

    delivery = os.path.join(workdir, "delivery.flac")
    preview = os.path.join(workdir, "preview.flac")
    lim = (limits.timeout, limits.cpu_seconds, limits.memory_bytes)
    layout = ["-channel_layout", FFMPEG_LAYOUT[mode]]
    run_ffmpeg(["-i", "file:" + master, "-c:a", "flac", "-compression_level", "5", "-sample_fmt", "s32",
                *layout, "file:" + delivery], *lim)
    run_ffmpeg(["-i", "file:" + master, "-t", str(limits.preview_seconds), "-c:a", "flac", "-sample_fmt", "s32",
                *layout, "file:" + preview], *lim)
    progress("packaging", 0.85)
    if _decoded_pcm_sha(delivery) != pcm_hash.hexdigest():
        raise MediaError("delivery_mismatch")
    check()

    layer_qa: dict | str = "not_added"
    if add_layer:   # the layer is independent of the programme: verify a steady 10 s window of it
        br = BinauralRenderer(profile, frames)
        start = min(br.fi, max(0, frames - 10 * FS))
        br.advance(start)
        win = br.layer(min(10 * FS, frames - start))
        layer_qa = analyze_layer(win, profile, start_frame=start) if len(win) >= FS // 2 else "not_measurable"
    r = renderer_for(mode, profile, set(stem_paths))
    sources = r.sources() if hasattr(r, "sources") else {"FL": "binaural_shift(main L)", "FR": "binaural_shift(main R)"}
    loud = _weighted_lufs(lufs, BS1770_WEIGHTS[mode])
    channels = []
    for i, name in enumerate(names):
        rms = math.sqrt(sq[i] / frames) if frames else 0.0
        channels.append({"channel": name, "source": sources[name],
                         "uploaded": sources[name] == "uploaded",
                         "true_peak_dbtp": _r(tp_ch[i].dbtp), "sample_peak_dbfs": _r(lin_to_db(tp_ch[i].sample_peak)),
                         "rms_dbfs": _r(lin_to_db(rms))})
    params = {
        "engine_version": SHIFT_ENGINE_VERSION, "mode": mode, "profile": profile.to_dict(), "tools": tool_versions(),
        "input": {"sha256": sha256_file(src_path), "size_bytes": os.path.getsize(src_path), "probe": pr.to_dict()},
        "stems": stem_info,
        "output": {"sample_rate": FS, "channels": nch, "channel_order": names, "bits": 24, "frames": frames,
                   "duration_seconds": round(frames / FS, 6), "pcm_sha256": pcm_hash.hexdigest(),
                   "master_sha256": sha256_file(master), "delivery_sha256": sha256_file(delivery),
                   "preview_sha256": sha256_file(preview),
                   "delivery_format": f"FLAC 24-bit 48 kHz {FFMPEG_LAYOUT[mode]} ({' '.join(names)})",
                   "channel_mask": channel_mask(names)},
        "processing": {"block_frames": BLOCK, "common_gain_db": round(common_gain_db, 4),
                       "beat_method": "shift_layer" if add_layer else "shift",
                       "source_gain_db": profile.source_gain_db if add_layer else 0.0,
                       "hilbert": "Kaiser-windowed type III FIR, 8191 taps, latency compensated",
                       "input_bytes_total": total_in, "dither": "none (deterministic rounding)"},
    }
    report = {
        "status": "passed", "mode": mode,
        "levels": {"output_integrated_lufs": _r(loud) if loud is not None else "not_measurable",
                   "lufs_weights": BS1770_WEIGHTS[mode], "true_peak_before_gain_dbtp": _r(pre_db),
                   "true_peak_master_dbtp": _r(tp_master), "true_peak_ceiling_dbtp": TP_CEILING_DBTP,
                   "true_peak_tolerance_db": TP_MARGIN_DB, "common_gain_db": round(common_gain_db, 4)},
        "channels": channels,
        "verification": verification,
        # NAP-117: how the beat is made — the whole track shifted (always), plus the carrier layer when chosen
        "beat_method": "shift_layer" if add_layer else "shift",
        "layer": layer_qa,
        "checks": {"frames_ok": True, "non_finite_samples": 0, "clipped_samples": clipped, "channels": nch,
                   "sample_rate": FS, "delivery_bit_exact": True,
                   "binaural_only_in_front": all(c["passed"] for c in verification["checks"])},
        "disclaimer": "Technical verification of signal parameters only. It does not verify or guarantee any "
                      "brain state or therapeutic effect. Binaural difference on loudspeakers requires confirmed "
                      "channel separation at the ears (spec §11, §14).",
    }
    rp, pp = os.path.join(workdir, "qa_report.json"), os.path.join(workdir, "params.json")
    with open(rp, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(pp, "w") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    progress("packaging", 1.0)
    return Outputs(master, delivery, preview, "", rp, pp, report, params)


def _weighted_lufs(meter: LoudnessMeter, weights: list[float]) -> float | None:
    if len(meter.subblocks) < 4:
        return None
    sb = np.array(meter.subblocks) * np.array(weights)
    blocks = np.array([sb[j:j + 4].mean(axis=0).sum() for j in range(len(sb) - 3)])
    with np.errstate(divide="ignore"):
        lk = -0.691 + 10 * np.log10(blocks)
    gated = blocks[lk > -70.0]
    if gated.size == 0:
        return None
    rel = -0.691 + 10 * np.log10(gated.mean()) - 10.0
    with np.errstate(divide="ignore"):
        final = gated[-0.691 + 10 * np.log10(gated) > rel]
    return None if final.size == 0 else float(-0.691 + 10 * np.log10(final.mean()))


def _r(x: float):
    return round(x, 3) if math.isfinite(x) else "not_measurable"
