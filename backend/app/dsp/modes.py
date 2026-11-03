"""NAP-063 / NAP-108: converter layouts for the NAP equipment.

Layout 2.0 (headphones) — the whole track is frequency-shifted, with different shifts per ear:
    left  = SSB(sourceL, -b(t)/2)        right = SSB(sourceR, +b(t)/2)
so every spectral component differs between the ears by b(t). Single-sideband shift via the analytic signal:
    a[n] = x[n-D] + j*H{x}[n]            (H: Kaiser-windowed type III FIR Hilbert transformer, delay D)
    y[n] = Re(a[n] * exp(j*phi[n])) = x[n-D]*cos(phi) - H{x}[n]*sin(phi),  phi[n] = sum 2*pi*df/Fs
The method follows published descriptions of whole-track frequency shifting; it is an independent
implementation and does not reproduce or claim any proprietary algorithm (spec §3).
Content below ~HILBERT_LOW_HZ is not shifted cleanly (FIR Hilbert limit); layouts with LFE carry it there.

Speaker layouts (channel order = WAVE / ffmpeg order):
    acoustic_3_0  FL FR FC                       speakers 3.0        mask 0x007   ffmpeg "3.0"
    surround_5_1  FL FR FC LFE SL SR             NAP chair 5.1       mask 0x60F   ffmpeg "5.1(side)"
    surround_7_1  FL FR FC LFE BL BR SL SR       NAP chair 7.1       mask 0x63F   ffmpeg "7.1"
Every channel takes the author's own file for that channel when one is uploaded (a mono stem; a stereo file is
downmixed to mono). A channel without its own file is derived from the main track:
    FL/FR   binaural shift of the main track L/R (the ONLY channels with a frequency difference;
            an uploaded FL/FR stem is shifted the same way)
    FC      band-limited mid of the main track
    LFE     mid of the main track, low-passed at LFE_CUTOFF_HZ (an uploaded LFE stem is low-passed too)
    SL/SR   unshifted main L/R, high-passed, delayed SURROUND_DELAY_S, attenuated
    BL/BR   unshifted main L/R, high-passed, delayed BACK_DELAY_S, attenuated more
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
BACK_DELAY_S = 0.025
BACK_GAIN = 0.4
CENTER_GAIN = 0.5
LAYOUTS = {
    "stereo_2_0": ["FL", "FR"],
    "acoustic_3_0": ["FL", "FR", "FC"],
    "surround_5_1": ["FL", "FR", "FC", "LFE", "SL", "SR"],
    "surround_7_1": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
}
WAVE_MASK = {"FL": 0x1, "FR": 0x2, "FC": 0x4, "LFE": 0x8, "BL": 0x10, "BR": 0x20, "SL": 0x200, "SR": 0x400}
FFMPEG_LAYOUT = {"stereo_2_0": "stereo", "acoustic_3_0": "3.0", "surround_5_1": "5.1(side)", "surround_7_1": "7.1"}
CHANNELS_51 = LAYOUTS["surround_5_1"]


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


class LayoutRenderer:
    """Any layout. process(main (n,2), stems {channel: (n,1)}) -> (n,C); stems present must be fed every block."""

    def __init__(self, mode: str, profile: Profile, stems_present: set[str] = frozenset(), fs: int = FS):
        if mode not in LAYOUTS:
            raise ValueError(f"unknown mode {mode}")
        profile.validate()
        self.mode = mode
        self.names = LAYOUTS[mode]
        self.channels = len(self.names)
        unknown = set(stems_present) - set(self.names)
        if unknown:
            raise ValueError(f"unknown stems: {sorted(unknown)}")
        self.present = set(stems_present)
        self.latency = d = (len(_H) - 1) // 2
        self.l = Shifter(-0.5, profile, fs)
        self.r = Shifter(+0.5, profile, fs)
        # every non-front channel is delayed by the same D so all stay time-aligned with the shifted fronts
        self.align_main = DelayLine(d, 2)
        self.align = {c: DelayLine(d, 1) for c in self.present if c not in ("FL", "FR")}
        self.c_bp = _SOS(butter(4, CENTER_BAND_HZ, btype="bandpass", fs=fs, output="sos"))
        self.lfe_lp = _SOS(butter(4, LFE_CUTOFF_HZ, btype="lowpass", fs=fs, output="sos"))
        self.hp = {c: _SOS(butter(2, SURROUND_HP_HZ, btype="highpass", fs=fs, output="sos"))
                   for c in ("SL", "SR", "BL", "BR")}
        self.s_delay = DelayLine(int(round(SURROUND_DELAY_S * fs)), 2)
        self.b_delay = DelayLine(int(round(BACK_DELAY_S * fs)), 2)

    def sources(self) -> dict:
        gen = {"FL": "binaural_shift(main L)", "FR": "binaural_shift(main R)",
               "FC": "generated(mid of main, band-pass)", "LFE": "generated(mid of main, low-pass)",
               "SL": "generated(main L, unshifted, high-pass+delay)", "SR": "generated(main R, unshifted, high-pass+delay)",
               "BL": "generated(main L, unshifted, high-pass+delay)", "BR": "generated(main R, unshifted, high-pass+delay)"}
        return {c: ("uploaded" if c in self.present else gen[c]) for c in self.names}

    def process(self, main: np.ndarray, stems: dict | None = None) -> np.ndarray:
        stems = stems or {}
        n = main.shape[0]
        out = np.zeros((n, self.channels), dtype=np.float64)
        out[:, 0] = self.l(stems["FL"][:, 0] if "FL" in self.present else main[:, 0])
        out[:, 1] = self.r(stems["FR"][:, 0] if "FR" in self.present else main[:, 1])
        if self.channels == 2:
            return out
        m = self.align_main(main)
        mid = 0.5 * (m[:, 0] + m[:, 1])
        own = {c: self.align[c](stems[c][:, 0]) for c in self.align}
        need_s = any(c not in self.present for c in ("SL", "SR") if c in self.names)
        need_b = any(c not in self.present for c in ("BL", "BR") if c in self.names)
        # filters run every block for the channels that use them, so their state stays continuous
        side = self.s_delay(np.stack([self.hp["SL"](m[:, 0]), self.hp["SR"](m[:, 1])], 1)) if need_s else None
        back = self.b_delay(np.stack([self.hp["BL"](m[:, 0]), self.hp["BR"](m[:, 1])], 1)) if need_b else None
        for i, c in enumerate(self.names[2:], start=2):
            if c == "FC":
                out[:, i] = own["FC"] if "FC" in own else CENTER_GAIN * self.c_bp(mid)
            elif c == "LFE":
                out[:, i] = self.lfe_lp(own["LFE"] if "LFE" in own else mid)
            elif c in own:
                out[:, i] = own[c]
            elif c in ("SL", "SR"):
                out[:, i] = SURROUND_GAIN * side[:, 0 if c == "SL" else 1]
            else:
                out[:, i] = BACK_GAIN * back[:, 0 if c == "BL" else 1]
        return out


def renderer_for(mode: str, profile: Profile, stems_present: set[str] = frozenset()):
    if mode == "stereo_2_0" and not stems_present:
        return Stereo20Renderer(profile)
    return LayoutRenderer(mode, profile, stems_present)


# ---------------------------------------------------------------- verification (pilot signals)

PILOT = {"main": 1000.0, "FL": 1300.0, "FR": 1350.0, "FC": 700.0, "LFE": 45.0, "SL": 1500.0, "SR": 1700.0,
         "BL": 1900.0, "BR": 2100.0}
PILOT_SECONDS = 6.0


def _pilot_inputs(stems_present, frames, fs=FS):
    t = np.arange(frames) / fs
    tone = lambda f: 0.25 * np.sin(TWO_PI * f * t)  # noqa: E731
    main = np.stack([tone(PILOT["main"])] * 2, 1)
    stems = {c: tone(PILOT[c])[:, None] for c in stems_present}
    return main, stems


def _db(x: float) -> float:
    return -300.0 if x <= 1e-15 else round(20 * math.log10(x), 2)


def verify_mode(mode: str, profile: Profile, stems_present: set[str] = frozenset(), fs: int = FS) -> dict:
    """Runs pilot tones through a fresh renderer: measured shift per front channel, beat, channel placement
    and cross-talk matrix. Checks the processor, independent of the programme material."""
    beats = sorted({round(float(b), 6) for _, b in profile.beat_curve} or {profile.beat_hz})
    stems_present = set(stems_present)
    names = LAYOUTS[mode]
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
        fl_src = PILOT["FL"] if "FL" in stems_present else PILOT["main"]
        fr_src = PILOT["FR"] if "FR" in stems_present else PILOT["main"]
        fl, _ = measure_tone(y[:, 0], fs, fl_src - b / 2)
        fr, _ = measure_tone(y[:, 1], fs, fr_src + b / 2)
        measured = (fr - fr_src) - (fl - fl_src)
        err = abs(measured - b)
        points.append({"beat_hz": b, "left_hz": round(fl, 4), "right_hz": round(fr, 4),
                       "measured_beat_hz": round(measured, 4), "error_hz": round(err, 4)})
        ok &= err <= 0.1
        if matrix is None:
            freqs = {"main": PILOT["main"], "main_shifted_left": PILOT["main"] - b / 2,
                     "main_shifted_right": PILOT["main"] + b / 2}
            for c in sorted(stems_present):
                freqs[c] = PILOT[c]
                if c == "FL":
                    freqs["FL_shifted"] = PILOT["FL"] - b / 2
                if c == "FR":
                    freqs["FR_shifted"] = PILOT["FR"] + b / 2
            matrix = {ch: {k: _db(magnitude_at(y[:, i], fs, f)) for k, f in freqs.items()}
                      for i, ch in enumerate(names)}
    checks = _placement_checks(mode, stems_present, matrix)
    ok &= all(c["passed"] for c in checks)
    return {"method": "pilot tones (main 1000 Hz; a distinct tone per uploaded channel file) through a fresh "
                      "renderer; Blackman-Harris FFT peak interpolation and windowed DTFT magnitudes",
            "tolerance_hz": 0.1, "points": points, "matrix_dbfs": matrix, "checks": checks, "passed": bool(ok)}


def _placement_checks(mode, stems, m):
    """Expected placement: >= 40 dB between the wanted component and anything that must not be there."""
    checks = []
    stems = set(stems)
    keys = set(next(iter(m.values())).keys())

    def need(name, ch, wanted, absent):
        w = m[ch][wanted]
        worst = max((m[ch][a] for a in absent if a in keys and a != wanted), default=-300.0)
        checks.append({"check": name, "channel": ch, "wanted": wanted, "margin_db": round(w - worst, 1),
                       "passed": w - worst >= 40.0})

    others = sorted(stems)          # tones of uploaded files: each may appear only in its own channel
    shifted = ["main_shifted_left", "main_shifted_right"]
    if "FL" in stems:
        need("front_left_own_file_shifted_down", "FL", "FL_shifted", ["main", *shifted, *[k for k in others if k != "FL"], "FL"])
    else:
        need("front_left_shifted_down", "FL", "main_shifted_left", ["main", "main_shifted_right", *others])
    if "FR" in stems:
        need("front_right_own_file_shifted_up", "FR", "FR_shifted", ["main", *shifted, *[k for k in others if k != "FR"], "FR"])
    else:
        need("front_right_shifted_up", "FR", "main_shifted_right", ["main", "main_shifted_left", *others])
    for ch in LAYOUTS[mode][2:]:
        if ch in stems:
            need(f"{ch}_is_own_file_only", ch, ch, ["main", *shifted, *[k for k in others if k != ch]])
        elif ch == "LFE":
            checks.append({"check": "lfe_generated_low_pass", "channel": "LFE", "wanted": "1 kHz pilot <= -60 dBFS",
                           "margin_db": round(-60.0 - m["LFE"]["main"], 1), "passed": m["LFE"]["main"] <= -60.0})
        else:
            need(f"{ch}_generated_unshifted", ch, "main", [*shifted, *others])
    return checks
