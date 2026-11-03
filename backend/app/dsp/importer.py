"""NAP-110: a finished audio-studio export becomes an app track as it is.

The studio mix is final (generators, buses and routing are rendered there), so nothing is re-shifted or re-mixed.
The import only: keeps every channel in its place (no ffmpeg re-mapping), brings the sample rate to 48 kHz,
applies one common gain if the true peak would exceed the store ceiling, writes the 24-bit master with the
layout's channel mask and the bit-exact FLAC delivery, and reports every channel.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np

from .engine import FS, LoudnessMeter, TruePeakMeter, db_to_lin, lin_to_db
from .media import Decoder, MediaError, free_bytes, probe, run_ffmpeg, tool_versions
from .modes import FFMPEG_LAYOUT, LAYOUTS
from .pipeline import (BLOCK, INT24_MAX, TP_CEILING_DBTP, TP_MARGIN_DB, Limits, Outputs, _decoded_pcm_sha,
                       _to_int24_bytes, sha256_file)
from .variants import BS1770_WEIGHTS, _r, _weighted_lufs, channel_mask, wav_header

IMPORT_VERSION = "nap-studio-import/1.0.0"
# studio channel layouts -> store layouts (channel orders are identical)
STUDIO_LAYOUT = {"stereo": "stereo_2_0", "3.0": "acoustic_3_0", "5.1(side)": "surround_5_1", "7.1": "surround_7_1"}


def _blocks(path: str, nch: int, limits: Limits, check):
    dec = Decoder(path, nch, limits.cpu_seconds, limits.memory_bytes, remix=False)
    fb = nch * 4
    pending = b""
    try:
        while True:
            chunk = dec.proc.stdout.read(BLOCK * fb)
            if not chunk:
                break
            buf = pending + chunk
            n = len(buf) // fb
            pending = buf[n * fb:]
            if n:
                y = np.frombuffer(buf[:n * fb], dtype="<f4").reshape(n, nch).astype(np.float64)
                if not np.all(np.isfinite(y)):
                    raise MediaError("render_non_finite")
                check()
                yield y
        dec.finish()
    except BaseException:
        dec.kill()
        raise


def import_render(src_path: str, workdir: str, limits: Limits, mode: str, studio: dict,
                  progress=lambda s, f: None, check=lambda: None) -> Outputs:
    """mode: store layout (stereo_2_0 | acoustic_3_0 | surround_5_1 | surround_7_1)."""
    if mode not in LAYOUTS:
        raise MediaError("unknown_mode", mode)
    names = LAYOUTS[mode]
    nch = len(names)
    os.makedirs(workdir, exist_ok=True)
    progress("validating", 0.0)
    if os.path.getsize(src_path) > limits.max_bytes:
        raise MediaError("file_too_large")
    pr = probe(src_path)
    if pr.channels != nch:
        raise MediaError("channel_count_mismatch", f"{pr.channels} != {nch}")
    if pr.duration <= 0:
        raise MediaError("empty_audio")
    if pr.duration > limits.max_seconds:
        raise MediaError("too_long")
    est = int(math.ceil(pr.duration * FS * 1.01)) + FS
    if free_bytes(workdir) < est * nch * 3 * 2 + limits.disk_reserve:
        raise MediaError("insufficient_disk", retryable=True)
    progress("validating", 1.0)

    # pass 1: measure (decoding + rendering stages, so the author's timeline reads the same as a conversion)
    progress("decoding", 0.0)
    tp_pre = TruePeakMeter(nch)
    frames = 0
    for y in _blocks(src_path, nch, limits, check):
        tp_pre.feed(y)
        frames += len(y)
        progress("rendering", min(0.99, frames / max(1, est)))
    progress("decoding", 1.0)
    progress("rendering", 1.0)
    if frames == 0:
        raise MediaError("empty_audio")
    pre_db = tp_pre.dbtp
    target = TP_CEILING_DBTP - TP_MARGIN_DB
    common_gain_db = 0.0 if pre_db <= target else target - pre_db
    gain = db_to_lin(common_gain_db)
    progress("quality_check", 1.0)

    progress("packaging", 0.0)
    master = os.path.join(workdir, "master.wav")
    tp_ch = [TruePeakMeter(1) for _ in range(nch)]
    sq = np.zeros(nch)
    lufs = LoudnessMeter(nch)
    pcm_hash = hashlib.sha256()
    clipped = done = 0
    with open(master, "wb") as fh:
        fh.write(wav_header(frames, nch, names=names))
        for y in _blocks(src_path, nch, limits, check):
            data, c = _to_int24_bytes(y * gain)
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
    if done != frames:
        raise MediaError("decode_unstable", f"{done} != {frames}")
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

    loud = _weighted_lufs(lufs, BS1770_WEIGHTS[mode])
    channels = []
    for i, name in enumerate(names):
        rms = math.sqrt(sq[i] / frames)
        channels.append({"channel": name, "source": "studio", "uploaded": False,
                         "true_peak_dbtp": _r(tp_ch[i].dbtp), "sample_peak_dbfs": _r(lin_to_db(tp_ch[i].sample_peak)),
                         "rms_dbfs": _r(lin_to_db(rms))})
    params = {
        "engine_version": IMPORT_VERSION, "mode": mode, "tools": tool_versions(), "studio": studio,
        "input": {"sha256": sha256_file(src_path), "size_bytes": os.path.getsize(src_path), "probe": pr.to_dict()},
        "output": {"sample_rate": FS, "channels": nch, "channel_order": names, "bits": 24, "frames": frames,
                   "duration_seconds": round(frames / FS, 6), "pcm_sha256": pcm_hash.hexdigest(),
                   "master_sha256": sha256_file(master), "delivery_sha256": sha256_file(delivery),
                   "preview_sha256": sha256_file(preview), "channel_mask": channel_mask(names),
                   "delivery_format": f"FLAC 24-bit 48 kHz {FFMPEG_LAYOUT[mode]} ({' '.join(names)})"},
        "processing": {"resampled": pr.sample_rate != FS, "common_gain_db": round(common_gain_db, 4),
                       "channel_mapping": "identity (studio channel order = store channel order)",
                       "dither": "none (deterministic rounding)"},
    }
    report = {
        "status": "passed", "mode": mode, "origin": "studio",
        "levels": {"output_integrated_lufs": _r(loud) if loud is not None else "not_measurable",
                   "lufs_weights": BS1770_WEIGHTS[mode], "true_peak_before_gain_dbtp": _r(pre_db),
                   "true_peak_master_dbtp": _r(tp_master), "true_peak_ceiling_dbtp": TP_CEILING_DBTP,
                   "true_peak_tolerance_db": TP_MARGIN_DB, "common_gain_db": round(common_gain_db, 4)},
        "channels": channels,
        # the mix itself was verified when the studio exported it (bit-exact render, channel order, clipping)
        "verification": {"passed": bool(studio.get("export_verified")), "method": "studio export verification"},
        "checks": {"frames_ok": True, "clipped_samples": 0, "channels": nch, "sample_rate": FS,
                   "delivery_bit_exact": True},
        "disclaimer": "Technical verification of signal parameters only. It does not verify or guarantee any "
                      "brain state or therapeutic effect.",
    }
    rp, pp = os.path.join(workdir, "qa_report.json"), os.path.join(workdir, "params.json")
    with open(rp, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(pp, "w") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    progress("packaging", 1.0)
    return Outputs(master, delivery, preview, "", rp, pp, report, params)
