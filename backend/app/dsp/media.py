"""ffprobe/ffmpeg wrappers. Always argument arrays (no shell), local files only, resource limits."""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
from dataclasses import dataclass

ALLOWED = {
    # container format_name token -> allowed codecs
    "wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_u8"},
    "flac": {"flac"},
    "mp3": {"mp3"},
    "mov,mp4,m4a,3gp,3g2,mj2": {"aac", "alac"},
    "aiff": {"pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_f32be"},  # spec v2.1 §2.1 (studio sources)
}


class MediaError(Exception):
    def __init__(self, code: str, detail: str = "", retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.retryable = retryable


@dataclass
class ProbeResult:
    format_name: str
    codec: str
    channels: int
    sample_rate: int
    duration: float
    bit_rate: int | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def tool_versions() -> dict:
    def first_line(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        except Exception:
            return "unknown"
    import numpy
    import scipy
    return {"ffmpeg": first_line(["ffmpeg", "-hide_banner", "-version"]),
            "ffprobe": first_line(["ffprobe", "-hide_banner", "-version"]),
            "numpy": numpy.__version__, "scipy": scipy.__version__}


def _limits(cpu_seconds: int, memory_bytes: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    return apply


def probe(path: str, timeout: int = 60) -> ProbeResult:
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
           "-show_format", "-show_streams", "-select_streams", "a", "file:" + path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           preexec_fn=_limits(timeout, 1 << 30))
    except subprocess.TimeoutExpired:
        raise MediaError("probe_timeout", retryable=True)
    if r.returncode != 0:
        raise MediaError("unsupported_or_corrupt", r.stderr[-500:])
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    if not streams:
        raise MediaError("no_audio_stream")
    s = streams[0]
    fname = fmt.get("format_name", "")
    codec = s.get("codec_name", "")
    allowed = ALLOWED.get(fname)
    if allowed is None or codec not in allowed:
        raise MediaError("unsupported_format", f"{fname}/{codec}")
    ch = int(s.get("channels") or 0)
    sr = int(s.get("sample_rate") or 0)
    dur = float(fmt.get("duration") or s.get("duration") or 0.0)
    if ch < 1 or sr < 8000:
        raise MediaError("unsupported_or_corrupt", "bad stream parameters")
    br = fmt.get("bit_rate")
    return ProbeResult(fname, codec, ch, sr, dur, int(br) if br else None)


class Decoder:
    """Streams decoded 48 kHz float32 interleaved PCM from ffmpeg (original channel count)."""

    def __init__(self, path: str, channels: int, cpu_seconds: int, memory_bytes: int):
        self.channels = channels
        self.cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
                    "-protocol_whitelist", "file,pipe", "-i", "file:" + path, "-map", "0:a:0", "-vn",
                    "-af", "aresample=48000:resampler=soxr:precision=28:dither_method=0",
                    "-ac", str(channels), "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     preexec_fn=_limits(cpu_seconds, memory_bytes))

    def kill(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 9)
            except ProcessLookupError:
                pass
        self.proc.wait()

    def finish(self) -> None:
        err = self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
        code = self.proc.wait()
        if code != 0 or err.strip():
            raise MediaError("decode_failed", err[-500:])


def run_ffmpeg(args: list[str], timeout: int, cpu_seconds: int, memory_bytes: int) -> None:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-protocol_whitelist", "file,pipe"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           preexec_fn=_limits(cpu_seconds, memory_bytes))
    except subprocess.TimeoutExpired:
        raise MediaError("encode_timeout", retryable=True)
    if r.returncode != 0:
        raise MediaError("encode_failed", r.stderr[-500:])


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free
