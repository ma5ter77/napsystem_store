"""NAP-063: converter modes beyond the MVP layer.

Mode 2.0 (headphones) — the whole track is frequency-shifted, with different shifts per ear:
    left  = SSB(sourceL, -b(t)/2)        right = SSB(sourceR, +b(t)/2)
so every spectral component differs between the ears by b(t). Single-sideband shift via the analytic signal:
    a[n] = x[n-D] + j*H{x}[n]            (H: Kaiser-windowed type III FIR Hilbert transformer, delay D)
    y[n] = Re(a[n] * exp(j*phi[n])) = x[n-D]*cos(phi) - H{x}[n]*sin(phi),  phi[n] = sum 2*pi*df/Fs
The method follows published descriptions of whole-track frequency shifting; it is an independent
implementation and does not reproduce or claim any proprietary algorithm (spec §3).
Content below ~HILBERT_LOW_HZ is not shifted cleanly (FIR Hilbert limit); 5.1 carries it in the LFE channel.

Mode 5.1 (NAP acoustic system), channel order FL FR FC LFE BL BR (WAVE mask 0x3F, ffmpeg layout "5.1"):
    FL/FR  binaural shift of the main track (the ONLY channels with a frequency difference)
    FC     uploaded centre stem (e.g. voice) or, if absent, the band-limited mid of the main track
    LFE    uploaded LFE stem or the mid of the main track, both low-passed at LFE_CUTOFF_HZ
    BL/BR  uploaded surround stem or the unshifted main track (high-passed, delayed, attenuated)
Uploaded stems are never mixed into any other channel (no cross-mixing).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt, sosfilt_zi
from scipy.signal.windows import kaiser

from .engine import FS, TWO_PI, Profile, magnitude_at, measure_tone

SHIFT_ENGINE_VERSION = "nap-shift-engine/1.0.0"
HILBERT_TAPS = 8191
HILBERT_LOW_HZ = 30.0
LFE_CUTOFF_HZ = 120.0
CENTER_BAND_HZ = (150.0, 8000.0)
SURROUND_HP_HZ = 100.0
SURROUND_DELAY_S = 0.015
SURROUND_GAIN = 0.5
CENTER_GAIN = 0.5
CHANNELS_51 = ["FL", "FR", "FC", "LFE", "BL", "BR"]
STEM_KINDS = {"center": 1, "lfe": 1, "surround": 2}


def hilbert_fir(taps: int = HILBERT_TAPS, beta: float = 8.0) -> np.ndarray:
    if taps % 2 == 0:
        raise ValueError("odd length required (type III)")
    m = (taps - 1) // 2
    n = np.arange(-m, m + 1)
    h = np.zeros(taps)
    odd = n % 2 != 0
    h[odd] = 2.0 / (math.pi * n[odd])
    return h * kaiser(taps, beta)


_H = hilbert_fir()


class StreamFIR:
    """Overlap-save FIR filter for one channel; block size does not change the output."""

    def __init__(self, h: np.ndarray):
        self.h = h
        self.tail = np.zeros(len(h) - 1)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self.tail, x))
        self.tail = buf[len(buf) - (len(self.h) - 1):]
        return fftconvolve(buf, self.h, mode="valid")


class DelayLine:
    def __init__(self, d: int, channels: int = 1):
        self.buf = np.zeros((d, channels))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x2 = x.reshape(len(x), -1)
        buf = np.concatenate((self.buf, x2))
        out = buf[:len(x2)]
        self.buf = buf[len(x2):]
        return out.reshape(x.shape)


class Shifter:
    """Single-channel SSB frequency shifter with phase continuity; output delayed by D = (taps-1)/2."""

    def __init__(self, sign: float, profile: Profile, fs: int = FS):
        self.sign = sign  # -0.5 for left, +0.5 for right: shift = sign * b(t)
        self.p = profile
        self.fs = fs
        self.hil = StreamFIR(_H)
        self.delay = DelayLine((len(_H) - 1) // 2)
        self.phase = 0.0
        self.pos = 0  # input position (time base of b(t))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if n == 0:
            return x
        t = (self.pos + np.arange(n, dtype=np.float64)) / self.fs
        inc = TWO_PI * self.sign * self.p.beat_at(t) / self.fs
        c = np.cumsum(inc)
        ph = self.phase + np.concatenate(([0.0], c[:-1]))
        self.phase = math.fmod(self.phase + c[-1], TWO_PI)
        self.pos += n
        xd = self.delay(x)
        xh = self.hil(x)
        return xd * np.cos(ph) - xh * np.sin(ph)


class _SOS:
    def __init__(self, sos: np.ndarray):
        self.sos = sos
        self.zi = sosfilt_zi(sos) * 0.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = sosfilt(self.sos, x, zi=self.zi)
        return y


class Stereo20Renderer:
    """Mode 2.0. process((n,2)) -> (n,2); the first `latency` output frames are the filter delay."""
    channels = 2

    def __init__(self, profile: Profile, fs: int = FS):
        profile.validate()
        self.l = Shifter(-0.5, profile, fs)
        self.r = Shifter(+0.5, profile, fs)
        self.latency = (len(_H) - 1) // 2

    def process(self, main: np.ndarray, stems: dict | None = None) -> np.ndarray:
        out = np.empty_like(main, dtype=np.float64)
        out[:, 0] = self.l(main[:, 0])
        out[:, 1] = self.r(main[:, 1])
        return out


class Surround51Renderer:
    """Mode 5.1. process(main (n,2), stems {kind: (n,c)}) -> (n,6); stems present must be fed every block."""
    channels = 6

    def __init__(self, profile: Profile, stems_present: set[str] = frozenset(), fs: int = FS):
        profile.validate()
        unknown = set(stems_present) - set(STEM_KINDS)
        if unknown:
            raise ValueError(f"unknown stems: {sorted(unknown)}")
        self.present = set(stems_present)
        self.latency = (len(_H) - 1) // 2
        d = self.latency
        self.front = Stereo20Renderer(profile, fs)
        # every other channel is delayed by the same D so all six stay time-aligned with the shifted fronts
        self.align = {k: DelayLine(d, 2 if k in ("main", "surround") else 1) for k in ("main", "center", "lfe", "surround")}
        self.c_bp = _SOS(butter(4, CENTER_BAND_HZ, btype="bandpass", fs=fs, output="sos"))
        self.lfe_lp = _SOS(butter(4, LFE_CUTOFF_HZ, btype="lowpass", fs=fs, output="sos"))
        self.s_hp = [_SOS(butter(2, SURROUND_HP_HZ, btype="highpass", fs=fs, output="sos")) for _ in range(2)]
        self.s_delay = DelayLine(int(round(SURROUND_DELAY_S * fs)), 2)

    def sources(self) -> dict:
        return {"FL": "binaural_shift(main)", "FR": "binaural_shift(main)",
                "FC": "uploaded" if "center" in self.present else "generated(mid of main, band-pass)",
                "LFE": "uploaded" if "lfe" in self.present else "generated(mid of main, low-pass)",
                "BL": "uploaded" if "surround" in self.present else "generated(main, unshifted, high-pass+delay)",
                "BR": "uploaded" if "surround" in self.present else "generated(main, unshifted, high-pass+delay)"}

    def process(self, main: np.ndarray, stems: dict | None = None) -> np.ndarray:
        stems = stems or {}
        n = main.shape[0]
        out = np.zeros((n, 6), dtype=np.float64)
        out[:, 0:2] = self.front.process(main)
        m = self.align["main"](main)
        mid = 0.5 * (m[:, 0] + m[:, 1])
        if "center" in self.present:
            out[:, 2] = self.align["center"](stems["center"][:, 0])
        else:
            out[:, 2] = CENTER_GAIN * self.c_bp(mid)
        lfe_in = self.align["lfe"](stems["lfe"][:, 0]) if "lfe" in self.present else mid
        out[:, 3] = self.lfe_lp(lfe_in)
        if "surround" in self.present:
            out[:, 4:6] = self.align["surround"](stems["surround"])
        else:
            s = np.stack([self.s_hp[0](m[:, 0]), self.s_hp[1](m[:, 1])], 1)
            out[:, 4:6] = SURROUND_GAIN * self.s_delay(s)
        return out


def renderer_for(mode: str, profile: Profile, stems_present: set[str] = frozenset()):
    if mode == "stereo_2_0":
        if stems_present:
            raise ValueError("stems are only used in 5.1 mode")
        return Stereo20Renderer(profile)
    if mode == "surround_5_1":
        return Surround51Renderer(profile, stems_present)
    raise ValueError(f"unknown mode {mode}")


# ---------------------------------------------------------------- verification (pilot signals)

PILOT = {"main": 1000.0, "center": 700.0, "lfe": 45.0, "surround": 1500.0}
PILOT_SECONDS = 6.0


def _pilot_inputs(stems_present, frames, fs=FS):
    t = np.arange(frames) / fs
    tone = lambda f: 0.25 * np.sin(TWO_PI * f * t)  # noqa: E731
    main = np.stack([tone(PILOT["main"])] * 2, 1)
    stems = {}
    if "center" in stems_present:
        stems["center"] = tone(PILOT["center"])[:, None]
    if "lfe" in stems_present:
        stems["lfe"] = tone(PILOT["lfe"])[:, None]
    if "surround" in stems_present:
        stems["surround"] = np.stack([tone(PILOT["surround"])] * 2, 1)
    return main, stems


def _db(x: float) -> float:
    return -300.0 if x <= 1e-15 else round(20 * math.log10(x), 2)


def verify_mode(mode: str, profile: Profile, stems_present: set[str] = frozenset(), fs: int = FS) -> dict:
    """Runs pilot tones through a fresh renderer: measured shift per front channel, beat, channel placement
    and cross-talk matrix. Checks the processor, independent of the programme material."""
    beats = sorted({round(float(b), 6) for _, b in profile.beat_curve} or {profile.beat_hz})
    points = []
    matrix = None
    ok = True
    for b in beats:
        const = Profile(profile.slug, profile.version, profile.carrier_center_hz, b, profile.source_gain_db,
                        profile.tone_gain_dbfs, 0.0, 0.0)
        r = renderer_for(mode, const, stems_present)
        # BH4 main lobe is 4 bins wide: the window must resolve b/2 between shifted and unshifted pilots
        frames = int(max(PILOT_SECONDS, 10.0 / b) * fs) + r.latency
        main, stems = _pilot_inputs(stems_present, frames, fs)
        y = r.process(main, stems)[r.latency + fs // 2:]
        fl, _ = measure_tone(y[:, 0], fs, PILOT["main"] - b / 2)
        fr, _ = measure_tone(y[:, 1], fs, PILOT["main"] + b / 2)
        err = abs((fr - fl) - b)
        points.append({"beat_hz": b, "left_hz": round(fl, 4), "right_hz": round(fr, 4),
                       "measured_beat_hz": round(fr - fl, 4), "error_hz": round(err, 4)})
        ok &= err <= 0.1
        if matrix is None:
            names = CHANNELS_51 if mode == "surround_5_1" else ["FL", "FR"]
            freqs = {"main": PILOT["main"], "main_shifted_left": PILOT["main"] - b / 2,
                     "main_shifted_right": PILOT["main"] + b / 2,
                     **{k: PILOT[k] for k in ("center", "lfe", "surround") if k in stems_present}}
            matrix = {ch: {k: _db(magnitude_at(y[:, i], fs, f)) for k, f in freqs.items()}
                      for i, ch in enumerate(names)}
    checks = _placement_checks(mode, stems_present, matrix)
    ok &= all(c["passed"] for c in checks)
    return {"method": "pilot tones (main 1000 Hz; centre 700, LFE 45, surround 1500 Hz when uploaded) through a "
                      "fresh renderer; Blackman-Harris FFT peak interpolation and windowed DTFT magnitudes",
            "tolerance_hz": 0.1, "points": points, "matrix_dbfs": matrix, "checks": checks, "passed": bool(ok)}


def _placement_checks(mode, stems, m):
    """Expected placement: >= 40 dB between the wanted component and anything that must not be there."""
    checks = []

    def need(name, ch, wanted, absent):
        w = m[ch][wanted]
        worst = max((m[ch][a] for a in absent), default=-300.0)
        checks.append({"check": name, "channel": ch, "wanted": wanted, "margin_db": round(w - worst, 1),
                       "passed": w - worst >= 40.0})

    others = [k for k in ("center", "lfe", "surround") if k in stems]
    need("front_left_shifted_down", "FL", "main_shifted_left", ["main", "main_shifted_right", *others])
    need("front_right_shifted_up", "FR", "main_shifted_right", ["main", "main_shifted_left", *others])
    if mode != "surround_5_1":
        return checks
    nostems = list(others)
    if "center" in stems:
        need("centre_is_uploaded_stem_only", "FC", "center", ["main", "main_shifted_left", "main_shifted_right",
                                                              *[k for k in nostems if k != "center"]])
    else:
        need("centre_generated_unshifted", "FC", "main", ["main_shifted_left", "main_shifted_right", *nostems])
    if "lfe" in stems:
        need("lfe_is_uploaded_stem_only", "LFE", "lfe", ["main", *[k for k in nostems if k != "lfe"]])
    else:
        checks.append({"check": "lfe_generated_low_pass", "channel": "LFE", "wanted": "1 kHz pilot <= -60 dBFS",
                       "margin_db": round(-60.0 - m["LFE"]["main"], 1), "passed": m["LFE"]["main"] <= -60.0})
    for ch in ("BL", "BR"):
        if "surround" in stems:
            need(f"surround_is_uploaded_stem_only_{ch}", ch, "surround",
                 ["main", "main_shifted_left", "main_shifted_right", *[k for k in nostems if k != "surround"]])
        else:
            need(f"surround_generated_unshifted_{ch}", ch, "main", ["main_shifted_left", "main_shifted_right", *nostems])
    return checks
