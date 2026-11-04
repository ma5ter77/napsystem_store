"""NAP-117: listen before converting — a short fragment of the uploaded track rendered with the author's beat settings.

The same processors as the conversion (whole-track shift, optional carrier layer, or the carrier layer alone), on a
fragment only, so the author hears the result in a second instead of waiting for the whole job. The fragment also
reports how loud the source is, so a track that is too quiet is noticed before the conversion.
"""
from __future__ import annotations

import math
import struct
import subprocess

import numpy as np

from .engine import FS, BinauralRenderer, Profile, db_to_lin, lin_to_db
from .media import MediaError, _limits
from .modes import renderer_for

PREVIEW_MAX_SECONDS = 30.0
QUIET_PEAK_DBFS = -30.0      # below this the beat (and everything else) will be hard to hear
CEILING = db_to_lin(-1.0)


def _decode_fragment(path: str, start: float, seconds: float, cpu_seconds: int = 30, memory_bytes: int = 1 << 30) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-protocol_whitelist", "file,pipe",
           "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}", "-i", "file:" + path, "-map", "0:a:0", "-vn",
           "-af", "aresample=48000:resampler=soxr:precision=28:dither_method=0",
           "-ac", "2", "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, preexec_fn=_limits(cpu_seconds, memory_bytes))
    except subprocess.TimeoutExpired:
        raise MediaError("probe_timeout") from None
    if r.returncode != 0:
        raise MediaError("decode_failed", r.stderr.decode(errors="replace")[-300:])
    x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float64)
    if x.size < 2 * FS // 4:
        raise MediaError("empty_audio")
    return x[: x.size - x.size % 2].reshape(-1, 2)


def _wav16(y: np.ndarray) -> bytes:
    q = np.clip(np.rint(y * 32767), -32768, 32767).astype("<i2")
    n, ch = q.shape
    data = q.tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, ch, FS, FS * ch * 2, ch * 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


def preview_fragment(path: str, profile_dict: dict, method: str, start: float = 0.0,
                     seconds: float = 20.0) -> tuple[bytes, dict]:
    """Returns (stereo 16-bit WAV for listening, facts about the source and the processing)."""
    seconds = max(1.0, min(PREVIEW_MAX_SECONDS, float(seconds)))
    profile = Profile.from_dict(profile_dict)
    src = _decode_fragment(path, start, seconds)
    peak = float(np.max(np.abs(src))) if src.size else 0.0
    rms = math.sqrt(float(np.mean(src * src))) if src.size else 0.0
    n = len(src)
    # the fragment fades in and out over 50 ms so it does not click where it was cut
    edge = min(int(0.05 * FS), n // 4)
    if method == "layer":
        br = BinauralRenderer(profile, n)
        y, _ = br.process(src)
    else:
        r = renderer_for("stereo_2_0", profile)
        padded = np.concatenate([src, np.zeros((r.latency, 2))])
        y = r.process(padded)[r.latency:]
        if method == "shift_layer":
            br = BinauralRenderer(profile, n)
            y = y * db_to_lin(profile.source_gain_db) + br.layer(n)
    ramp = np.sin(0.5 * np.pi * np.arange(edge) / max(1, edge)) ** 2
    y[:edge] *= ramp[:, None]
    y[n - edge:] *= ramp[::-1][:, None]
    out_peak = float(np.max(np.abs(y))) if y.size else 0.0
    gain = 1.0 if out_peak <= CEILING else CEILING / out_peak    # the conversion applies the same kind of common gain
    y = y * gain
    info = {"seconds": round(n / FS, 3), "start": round(start, 3), "method": method,
            "beat_hz": profile.beat_hz if not profile.beat_curve else [p[1] for p in profile.beat_curve],
            "source_peak_dbfs": round(lin_to_db(peak), 1) if peak > 0 else None,
            "source_rms_dbfs": round(lin_to_db(rms), 1) if rms > 0 else None,
            "quiet": peak <= 0 or lin_to_db(peak) < QUIET_PEAK_DBFS,
            "preview_gain_db": round(lin_to_db(gain), 2)}
    return _wav16(y), info
