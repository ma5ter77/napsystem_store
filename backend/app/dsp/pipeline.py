"""Conversion pipeline (DB-independent). Stages: validating -> decoding -> rendering -> quality_check ->
packaging. Processes in blocks; never loads a whole long file into memory."""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .engine import (ENGINE_VERSION, FS, BinauralRenderer, LoudnessMeter, Profile, TruePeakMeter,
                     analyze_layer, curve_checkpoints, db_to_lin, lin_to_db)
from .media import Decoder, MediaError, free_bytes, probe, run_ffmpeg, tool_versions

BLOCK = 65_536
TP_CEILING_DBTP = -1.0
TP_MARGIN_DB = 0.2  # documented measurement tolerance of the 4x oversampled meter
INT24_MAX = 8_388_607
QA_LAYER_SECONDS = 60
QA_ANALYSIS_SECONDS = 10


class Cancelled(Exception):
    pass


@dataclass
class Limits:
    max_bytes: int
    max_seconds: int
    cpu_seconds: int = 4 * 3600
    memory_bytes: int = 2 << 30
    disk_reserve: int = 256 << 20
    timeout: int = 3 * 3600
    preview_seconds: int = 30


@dataclass
class Outputs:
    master: str
    delivery: str
    preview: str
    layer: str
    report_json: str
    params_json: str
    report: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


Progress = Callable[[str, float], None]
Check = Callable[[], None]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _wav_header(frames: int, channels: int = 2, bits: int = 24, fs: int = FS, fmt: int = 1) -> bytes:
    bps = bits // 8
    data = frames * channels * bps
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, fmt, channels, fs, fs * channels * bps, channels * bps, bits) +
            b"data" + struct.pack("<I", data))


def _to_int24_bytes(x: np.ndarray) -> tuple[bytes, int]:
    q = np.rint(x * INT24_MAX)
    clipped = int(np.count_nonzero((q > INT24_MAX) | (q < -INT24_MAX - 1)))
    q = np.clip(q, -INT24_MAX - 1, INT24_MAX).astype("<i4").reshape(-1)
    b = q.view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes(), clipped


def _iter_decoded(path: str, channels: int, total: int):
    """Yield stereo float64 blocks from the decoded float32 spool; mono is duplicated to both channels."""
    with open(path, "rb") as fh:
        done = 0
        while done < total:
            n = min(BLOCK, total - done)
            raw = np.frombuffer(fh.read(n * channels * 4), dtype="<f4")
            blk = raw.reshape(-1, channels).astype(np.float64)
            if channels == 1:
                blk = np.repeat(blk, 2, axis=1)
            done += blk.shape[0]
            yield blk


def convert(src_path: str, workdir: str, profile_dict: dict, limits: Limits,
            progress: Progress = lambda s, f: None, check: Check = lambda: None) -> Outputs:
    os.makedirs(workdir, exist_ok=True)
    profile = Profile.from_dict(profile_dict)

    # ---------------------------------------------------------------- validating
    progress("validating", 0.0)
    size = os.path.getsize(src_path)
    if size == 0:
        raise MediaError("empty_file")
    if size > limits.max_bytes:
        raise MediaError("file_too_large")
    pr = probe(src_path)
    if pr.channels > 2:
        raise MediaError("too_many_channels", str(pr.channels))
    if pr.duration <= 0:
        raise MediaError("empty_audio")
    if pr.duration > limits.max_seconds:
        raise MediaError("too_long")
    est_frames = int(math.ceil(pr.duration * FS * 1.01)) + FS
    need = est_frames * (pr.channels * 4 + 6 + 4) + QA_LAYER_SECONDS * FS * 8 + limits.disk_reserve
    if free_bytes(workdir) < need:
        raise MediaError("insufficient_disk", f"need {need}", retryable=True)
    progress("validating", 1.0)
    check()

    # ---------------------------------------------------------------- decoding (spool float32)
    progress("decoding", 0.0)
    spool = os.path.join(workdir, "decoded.f32")
    dec = Decoder(src_path, pr.channels, limits.cpu_seconds, limits.memory_bytes)
    frames = 0
    bad = 0
    try:
        with open(spool, "wb") as out:
            frame_bytes = pr.channels * 4
            pending = b""
            while True:
                chunk = dec.proc.stdout.read(BLOCK * frame_bytes)
                if not chunk:
                    break
                pending += chunk
                usable = len(pending) - len(pending) % frame_bytes
                arr = np.frombuffer(pending[:usable], dtype="<f4")
                bad += int(np.count_nonzero(~np.isfinite(arr)))
                out.write(pending[:usable])
                pending = pending[usable:]
                frames += usable // frame_bytes
                progress("decoding", min(0.99, frames / max(1, est_frames)))
                check()
        dec.finish()
    except (Cancelled, Exception):
        dec.kill()
        raise
    if frames == 0:
        raise MediaError("empty_audio")
    if bad:
        raise MediaError("decode_failed", "non-finite samples in decoded audio")
    expected = pr.duration * FS
    if frames < expected * 0.98 - FS * 0.05:
        raise MediaError("truncated_audio", f"decoded {frames} frames, expected ~{int(expected)}")
    progress("decoding", 1.0)

    # ---------------------------------------------------------------- rendering (pass 1: measure)
    progress("rendering", 0.0)
    rend = BinauralRenderer(profile, frames)
    tp_pre = TruePeakMeter()
    lufs_out = LoudnessMeter()
    lufs_src = LoudnessMeter()
    layer_frames = min(frames, QA_LAYER_SECONDS * FS)
    layer_buf = np.zeros((layer_frames, 2), dtype=np.float64)
    done = 0
    for blk in _iter_decoded(spool, pr.channels, frames):
        mix, lay = rend.process(blk)
        if not np.all(np.isfinite(mix)):
            raise MediaError("render_non_finite")
        tp_pre.feed(mix)
        lufs_out.feed(mix)
        lufs_src.feed(blk)
        if done < layer_frames:
            k = min(layer_frames - done, blk.shape[0])
            layer_buf[done:done + k] = lay[:k]
        done += blk.shape[0]
        progress("rendering", done / frames)
        check()

    # ---------------------------------------------------------------- quality_check
    progress("quality_check", 0.0)
    fi, fo = rend.fi, rend.fo
    steady_start = fi
    # 10 s of steady layer give 0.0125 Hz FFT bins before interpolation; longer segments only cost memory
    steady_end = min(layer_frames, frames - fo, steady_start + QA_ANALYSIS_SECONDS * FS)
    layer_analysis: dict | str
    if profile.layer_enabled and steady_end - steady_start >= FS // 2:
        layer_analysis = analyze_layer(layer_buf[steady_start:steady_end], profile, start_frame=steady_start)
        if profile.beat_curve:  # NAP-021: the transition is checked along the whole curve
            pts = curve_checkpoints(profile, frames)
            layer_analysis["curve"] = {"points": pts, "max_error_hz": max((p["error_hz"] for p in pts), default=None),
                                       "method": "4 s windows rendered with the same phase-integrating renderer"}
    elif profile.layer_enabled:
        layer_analysis = "not_measurable"
    else:
        layer_analysis = "layer_disabled"
    pre_db = tp_pre.dbtp
    target = TP_CEILING_DBTP - TP_MARGIN_DB
    common_gain_db = 0.0 if pre_db <= target else target - pre_db
    common_gain = db_to_lin(common_gain_db)
    _write_float_wav(os.path.join(workdir, "layer_qa.wav"), layer_buf)
    progress("quality_check", 1.0)
    check()

    # ---------------------------------------------------------------- packaging (pass 2: write)
    progress("packaging", 0.0)
    master = os.path.join(workdir, "master.wav")
    rend2 = BinauralRenderer(profile, frames)
    tp_post = TruePeakMeter()
    lufs_post = LoudnessMeter()
    pcm_hash = hashlib.sha256()
    clipped = 0
    done = 0
    with open(master, "wb") as fh:
        fh.write(_wav_header(frames))
        for blk in _iter_decoded(spool, pr.channels, frames):
            mix, _ = rend2.process(blk)
            mix *= common_gain
            data, c = _to_int24_bytes(mix)
            clipped += c
            q = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
            back = (q[:, 0].astype(np.int32) | (q[:, 1].astype(np.int32) << 8) |
                    (q[:, 2].astype(np.int8).astype(np.int32) << 16)).reshape(-1, 2) / INT24_MAX
            tp_post.feed(back)
            lufs_post.feed(back)
            pcm_hash.update(data)
            fh.write(data)
            done += blk.shape[0]
            progress("packaging", 0.7 * done / frames)
            check()
    os.remove(spool)
    if clipped:
        raise MediaError("clipping_detected", str(clipped))
    if tp_post.dbtp > TP_CEILING_DBTP:
        raise MediaError("true_peak_exceeded", f"{tp_post.dbtp:.3f}")

    delivery = os.path.join(workdir, "delivery.flac")
    preview = os.path.join(workdir, "preview.flac")
    lim = (limits.timeout, limits.cpu_seconds, limits.memory_bytes)
    run_ffmpeg(["-i", "file:" + master, "-c:a", "flac", "-compression_level", "5", "-sample_fmt", "s32",
                "file:" + delivery], *lim)
    check()
    run_ffmpeg(["-i", "file:" + master, "-t", str(limits.preview_seconds), "-c:a", "flac", "-sample_fmt", "s32",
                "file:" + preview], *lim)
    progress("packaging", 0.85)
    # delivery must decode to the identical PCM as the master (lossless, confirmed player format)
    if _decoded_pcm_sha(delivery) != pcm_hash.hexdigest():
        raise MediaError("delivery_mismatch")
    check()

    lufs_in = lufs_src.integrated()
    lufs_o = lufs_post.integrated()
    params = {
        "engine_version": ENGINE_VERSION,
        "profile": profile.to_dict(),
        "tools": tool_versions(),
        "input": {"sha256": sha256_file(src_path), "size_bytes": size, "probe": pr.to_dict()},
        "output": {"sample_rate": FS, "channels": 2, "bits": 24, "frames": frames,
                   "duration_seconds": round(frames / FS, 6), "pcm_sha256": pcm_hash.hexdigest(),
                   "master_sha256": sha256_file(master), "delivery_sha256": sha256_file(delivery),
                   "preview_sha256": sha256_file(preview)},
        "processing": {"resampler": "soxr precision=28 (ffmpeg aresample)", "block_frames": BLOCK,
                       "mono_handling": "duplicated to L and R" if pr.channels == 1 else "stereo preserved",
                       "fade_in_frames": fi, "fade_out_frames": fo,
                       "common_gain_db": round(common_gain_db, 4), "dither": "none (deterministic rounding)"},
    }
    report = {
        "status": "passed",
        "levels": {
            "source_integrated_lufs": round(lufs_in, 2) if lufs_in is not None else "not_measurable",
            "output_integrated_lufs": round(lufs_o, 2) if lufs_o is not None else "not_measurable",
            "true_peak_before_gain_dbtp": round(pre_db, 3) if math.isfinite(pre_db) else "not_measurable",
            "true_peak_master_dbtp": round(tp_post.dbtp, 3) if math.isfinite(tp_post.dbtp) else "not_measurable",
            "sample_peak_master_dbfs": round(lin_to_db(tp_post.sample_peak), 3)
            if tp_post.sample_peak > 0 else "not_measurable",
            "true_peak_ceiling_dbtp": TP_CEILING_DBTP, "true_peak_tolerance_db": TP_MARGIN_DB,
            "common_gain_db": round(common_gain_db, 4),
            "note": "dBFS of a digital file is not loudness at the ear; calibration belongs to the hardware path.",
        },
        "checks": {"frames_ok": True, "non_finite_samples": 0, "clipped_samples": clipped,
                   "channels": 2, "sample_rate": FS, "delivery_bit_exact": True},
        "layer": layer_analysis,
        "disclaimer": "Technical verification of signal parameters only. It does not verify or guarantee any "
                      "brain state or therapeutic effect.",
    }
    rp = os.path.join(workdir, "qa_report.json")
    pp = os.path.join(workdir, "params.json")
    with open(rp, "w") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    with open(pp, "w") as fh:
        json.dump(params, fh, indent=2, ensure_ascii=False)
    progress("packaging", 1.0)
    return Outputs(master, delivery, preview, os.path.join(workdir, "layer_qa.wav"), rp, pp, report, params)


def _write_float_wav(path: str, data: np.ndarray) -> None:
    with open(path, "wb") as fh:
        fh.write(_wav_header(data.shape[0], 2, 32, FS, fmt=3))
        fh.write(data.astype("<f4").tobytes())


def _decoded_pcm_sha(path: str) -> str:
    import subprocess
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                          "-i", "file:" + path, "-f", "s24le", "-acodec", "pcm_s24le", "pipe:1"],
                         stdout=subprocess.PIPE)
    h = hashlib.sha256()
    for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
        h.update(chunk)
    if p.wait() != 0:
        raise MediaError("delivery_decode_failed")
    return h.hexdigest()


def read_wav_pcm(path: str) -> np.ndarray:
    """Test helper: decode any file to float64 stereo at its own rate via ffmpeg."""
    import subprocess
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "f64le", "-acodec",
                          "pcm_f64le", "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<f8").reshape(-1, 2)
