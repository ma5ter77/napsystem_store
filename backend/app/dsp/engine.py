"""NAP binaural engine: keeps the source intact and adds an independent two-carrier layer.

    fL = carrier_center_hz - beat_hz/2          fR = carrier_center_hz + beat_hz/2
    phaseL[n+1] = (phaseL[n] + 2*pi*fL/Fs) mod 2*pi   (float64, state carried across blocks)
    outL = sourceGain*sourceL + toneAmplitude*envelope*sin(phaseL)    (same for R)

Gains: lin = 10**(dB/20). tone_gain_dbfs is the peak level of each sine before the common output gain.
No compression, no per-channel limiter/normalisation, no automatic LUFS gain. Only a single common gain
(identical for both channels) may be applied to keep the master true peak <= -1 dBTP.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi

ENGINE_VERSION = "nap-binaural-engine/1.0.0"
FS = 48_000
TWO_PI = 2.0 * math.pi


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def lin_to_db(x: float) -> float:
    return -math.inf if x <= 0 else 20.0 * math.log10(x)


@dataclass(frozen=True)
class Profile:
    slug: str
    version: int
    carrier_center_hz: float
    beat_hz: float
    source_gain_db: float
    tone_gain_dbfs: float | None  # None => layer disabled
    fade_in_seconds: float
    fade_out_seconds: float
    # NAP-021: optional transition of the frequency difference over time — ((t_seconds, beat_hz), ...),
    # linear between points, held before the first and after the last point. Empty = constant beat_hz.
    beat_curve: tuple[tuple[float, float], ...] = ()

    @property
    def f_left(self) -> float:
        return self.carrier_center_hz - self.beat_hz / 2.0

    @property
    def f_right(self) -> float:
        return self.carrier_center_hz + self.beat_hz / 2.0

    @property
    def layer_enabled(self) -> bool:
        return self.tone_gain_dbfs is not None

    def validate(self) -> None:
        if not (20.0 <= self.f_left and self.f_right <= 1500.0):
            raise ValueError("carriers must stay within 20..1500 Hz")
        if not (0.0 < self.beat_hz <= 40.0):
            raise ValueError("beat_hz must be within (0, 40]")
        if not (-40.0 <= self.source_gain_db <= 0.0):
            raise ValueError("source_gain_db must be within [-40, 0]")
        if self.tone_gain_dbfs is not None and not (-60.0 <= self.tone_gain_dbfs <= -12.0):
            raise ValueError("tone_gain_dbfs must be within [-60, -12]")
        if not (0.0 <= self.fade_in_seconds <= 30 and 0.0 <= self.fade_out_seconds <= 30):
            raise ValueError("fades must be within [0, 30] s")
        if self.beat_curve:
            if len(self.beat_curve) < 2 or len(self.beat_curve) > 32:
                raise ValueError("beat_curve needs 2..32 points")
            ts = [t for t, _ in self.beat_curve]
            if ts[0] < 0 or any(b <= a for a, b in zip(ts, ts[1:])) or ts[-1] > 6 * 3600:
                raise ValueError("beat_curve times must start at >= 0 s and strictly increase")
            for _, b in self.beat_curve:
                if not (0.0 < b <= 40.0):
                    raise ValueError("beat_curve values must be within (0, 40] Hz")
                if not (20.0 <= self.carrier_center_hz - b / 2 and self.carrier_center_hz + b / 2 <= 1500.0):
                    raise ValueError("carriers must stay within 20..1500 Hz along the curve")

    def beat_at(self, t: np.ndarray) -> np.ndarray:
        """Frequency difference (Hz) at times t (seconds)."""
        if not self.beat_curve:
            return np.full(np.shape(t), self.beat_hz, dtype=np.float64)
        ts = np.array([p[0] for p in self.beat_curve], dtype=np.float64)
        bs = np.array([p[1] for p in self.beat_curve], dtype=np.float64)
        return np.interp(t, ts, bs)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        p = cls(slug=d["slug"], version=int(d["version"]), carrier_center_hz=float(d["carrier_center_hz"]),
                beat_hz=float(d["beat_hz"]), source_gain_db=float(d["source_gain_db"]),
                tone_gain_dbfs=None if d.get("tone_gain_dbfs") is None else float(d["tone_gain_dbfs"]),
                fade_in_seconds=float(d["fade_in_seconds"]), fade_out_seconds=float(d["fade_out_seconds"]),
                beat_curve=tuple((float(t), float(b)) for t, b in (d.get("beat_curve") or ())))
        p.validate()
        return p

    def to_dict(self) -> dict:
        d = asdict(self)
        if not self.beat_curve:
            d.pop("beat_curve")  # constant profiles keep their original snapshot (and manifest hash)
        else:
            d["beat_curve"] = [list(p) for p in self.beat_curve]
        d["f_left_hz"] = self.f_left
        d["f_right_hz"] = self.f_right
        return d


ALPHA_DEMO_V1 = Profile("alpha-demo-v1", 1, 255.0, 10.0, -6.0, -30.0, 0.5, 0.5)


def fade_lengths(profile: Profile, total_frames: int, fs: int = FS) -> tuple[int, int]:
    """Fade lengths in frames; shortened proportionally when the file is shorter than both fades."""
    fi = int(round(profile.fade_in_seconds * fs))
    fo = int(round(profile.fade_out_seconds * fs))
    if fi + fo > total_frames and fi + fo > 0:
        scale = total_frames / (fi + fo)
        fi = int(math.floor(fi * scale))
        fo = int(math.floor(fo * scale))
    return fi, fo


class BinauralRenderer:
    """Stateful block renderer. Feeding blocks of any size gives the same output as one block."""

    def __init__(self, profile: Profile, total_frames: int, fs: int = FS):
        profile.validate()
        self.p = profile
        self.fs = fs
        self.total = int(total_frames)
        self.src_gain = db_to_lin(profile.source_gain_db)
        self.tone_amp = db_to_lin(profile.tone_gain_dbfs) if profile.layer_enabled else 0.0
        self.inc_l = TWO_PI * profile.f_left / fs
        self.inc_r = TWO_PI * profile.f_right / fs
        self.phase_l = 0.0
        self.phase_r = 0.0
        self.pos = 0
        self.fi, self.fo = fade_lengths(profile, self.total, fs)

    def envelope(self, start: int, n: int) -> np.ndarray:
        idx = np.arange(start, start + n, dtype=np.float64)
        env = np.ones(n, dtype=np.float64)
        if self.fi > 0:
            m = idx < self.fi
            env[m] = np.sin(0.5 * math.pi * idx[m] / self.fi) ** 2  # env[0] == 0
        if self.fo > 0:
            last = self.total - 1
            m = idx > last - self.fo
            env[m] = np.minimum(env[m], np.sin(0.5 * math.pi * (last - idx[m]) / self.fo) ** 2)  # env[last]==0
        if self.total > 0:
            env[idx >= self.total] = 0.0
        return env

    def _curve_phases(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """NAP-021: instantaneous frequency integrated into phase — continuous across blocks, no resets."""
        t = (self.pos + np.arange(n, dtype=np.float64)) / self.fs
        half = self.p.beat_at(t) / 2.0
        inc_l = TWO_PI * (self.p.carrier_center_hz - half) / self.fs
        inc_r = TWO_PI * (self.p.carrier_center_hz + half) / self.fs
        cl, cr = np.cumsum(inc_l), np.cumsum(inc_r)
        ph_l = self.phase_l + np.concatenate(([0.0], cl[:-1]))
        ph_r = self.phase_r + np.concatenate(([0.0], cr[:-1]))
        self._next_l = math.fmod(self.phase_l + cl[-1], TWO_PI) if n else self.phase_l
        self._next_r = math.fmod(self.phase_r + cr[-1], TWO_PI) if n else self.phase_r
        return np.mod(ph_l, TWO_PI), np.mod(ph_r, TWO_PI)

    def advance(self, n: int, chunk: int = 1 << 20) -> None:
        """Move the phase state forward by n frames without producing samples (QA checkpoints)."""
        while n > 0:
            k = min(n, chunk)
            if self.p.beat_curve:
                self._curve_phases(k)
                self.phase_l, self.phase_r = self._next_l, self._next_r
            else:
                self.phase_l = math.fmod(self.phase_l + self.inc_l * k, TWO_PI)
                self.phase_r = math.fmod(self.phase_r + self.inc_r * k, TWO_PI)
            self.pos += k
            n -= k

    def layer(self, n: int) -> np.ndarray:
        """Return the isolated layer (n, 2) for the next n frames and advance the phase state."""
        if self.p.beat_curve:
            ph_l, ph_r = self._curve_phases(n)
            env = self.envelope(self.pos, n) * self.tone_amp
            out = np.empty((n, 2), dtype=np.float64)
            out[:, 0] = env * np.sin(ph_l)
            out[:, 1] = env * np.sin(ph_r)
            self.phase_l, self.phase_r = self._next_l, self._next_r
            self.pos += n
            return out
        k = np.arange(n, dtype=np.float64)
        ph_l = np.mod(self.phase_l + self.inc_l * k, TWO_PI)
        ph_r = np.mod(self.phase_r + self.inc_r * k, TWO_PI)
        env = self.envelope(self.pos, n) * self.tone_amp
        out = np.empty((n, 2), dtype=np.float64)
        out[:, 0] = env * np.sin(ph_l)
        out[:, 1] = env * np.sin(ph_r)
        self.phase_l = math.fmod(self.phase_l + self.inc_l * n, TWO_PI)
        self.phase_r = math.fmod(self.phase_r + self.inc_r * n, TWO_PI)
        self.pos += n
        return out

    def process(self, source: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """source: (n, 2) float. Returns (mix, layer) in float64, before the common output gain."""
        src = np.asarray(source, dtype=np.float64)
        if src.ndim != 2 or src.shape[1] != 2:
            raise ValueError("renderer expects stereo blocks; duplicate mono before rendering")
        lay = self.layer(src.shape[0])
        return self.src_gain * src + lay, lay


# ---------------------------------------------------------------- measurement --

def curve_checkpoints(profile: Profile, total_frames: int, fs: int = FS, window_s: float = 4.0) -> list[dict]:
    """NAP-021: measure left/right/beat at the curve points (and segment middles) on freshly rendered windows.

    The expected value is the mean instantaneous frequency over the window (exact for linear segments)."""
    if not profile.beat_curve or not profile.layer_enabled:
        return []
    w = int(window_s * fs)
    pts = [t for t, _ in profile.beat_curve]
    # windows lie entirely inside one linear segment (a window across a kink has no single spectral peak):
    # the middle of every segment long enough, and the held part after the last point
    starts = [((a + b) / 2) * fs - w / 2 for a, b in zip(pts, pts[1:]) if (b - a) >= window_s]
    starts.append(pts[-1] * fs + 0.25 * fs)
    out = []
    for st in starts:
        start = int(st)
        if start < 0 or start + w > total_frames or w <= fs:
            continue
        r = BinauralRenderer(profile, total_frames, fs)
        r.advance(start)
        seg = r.layer(w)
        env = r.envelope(start, w)
        if env.min() < 0.999:  # skip windows inside fades
            continue
        tt = (start + np.arange(w)) / fs
        exp_b = float(np.mean(profile.beat_at(tt)))
        exp_l, exp_r = profile.carrier_center_hz - exp_b / 2, profile.carrier_center_hz + exp_b / 2
        ml, _ = measure_tone(seg[:, 0], fs, exp_l)
        mr, _ = measure_tone(seg[:, 1], fs, exp_r)
        out.append({"t": round(start / fs + window_s / 2, 2), "expected_beat_hz": round(exp_b, 4),
                    "beat_hz": round(mr - ml, 4), "left_hz": round(ml, 4), "right_hz": round(mr, 4),
                    "error_hz": round(abs((mr - ml) - exp_b), 4)})
        if len(out) >= 24:
            break
    return out


class TruePeakMeter:
    """4x oversampled true-peak (polyphase FIR, state kept across blocks)."""
    FACTOR = 4

    def __init__(self, channels: int = 2, fs: int = FS):
        h = firwin(128, cutoff=20_500, fs=fs * self.FACTOR, window=("kaiser", 9.0)) * self.FACTOR
        self.phases = [h[p::self.FACTOR] for p in range(self.FACTOR)]
        self.zi = [[np.zeros(len(ph) - 1) for _ in range(channels)] for ph in self.phases]
        self.peak = 0.0
        self.sample_peak = 0.0

    def feed(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        self.sample_peak = max(self.sample_peak, float(np.max(np.abs(block))))
        for pi, ph in enumerate(self.phases):
            for c in range(block.shape[1]):
                y, self.zi[pi][c] = lfilter(ph, 1.0, block[:, c], zi=self.zi[pi][c])
                m = float(np.max(np.abs(y)))
                if m > self.peak:
                    self.peak = m
        self.peak = max(self.peak, self.sample_peak)

    @property
    def dbtp(self) -> float:
        return lin_to_db(self.peak)


class LoudnessMeter:
    """ITU-R BS.1770-4 integrated loudness (K-weighting, 400 ms blocks, 75 % overlap, gating)."""
    _B1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
    _A1 = [1.0, -1.69065929318241, 0.73248077421585]
    _B2 = [1.0, -2.0, 1.0]
    _A2 = [1.0, -1.99004745483398, 0.99007225036621]

    def __init__(self, channels: int = 2, fs: int = FS):
        if fs != 48_000:
            raise ValueError("coefficients are for 48 kHz")
        self.ch = channels
        self.z1 = [lfilter_zi(self._B1, self._A1) * 0.0 for _ in range(channels)]
        self.z2 = [lfilter_zi(self._B2, self._A2) * 0.0 for _ in range(channels)]
        self.sub = fs // 10  # 100 ms
        self.acc = np.zeros(channels)
        self.acc_n = 0
        self.subblocks: list[np.ndarray] = []

    def feed(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        filt = np.empty_like(block, dtype=np.float64)
        for c in range(self.ch):
            y, self.z1[c] = lfilter(self._B1, self._A1, block[:, c], zi=self.z1[c])
            y, self.z2[c] = lfilter(self._B2, self._A2, y, zi=self.z2[c])
            filt[:, c] = y
        sq = filt * filt
        i = 0
        n = sq.shape[0]
        while i < n:
            take = min(self.sub - self.acc_n, n - i)
            self.acc += sq[i:i + take].sum(axis=0)
            self.acc_n += take
            i += take
            if self.acc_n == self.sub:
                self.subblocks.append(self.acc / self.sub)
                self.acc = np.zeros(self.ch)
                self.acc_n = 0

    def integrated(self) -> float | None:
        """Returns LUFS or None (not_measurable) for silence / files shorter than one 400 ms block."""
        if len(self.subblocks) < 4:
            return None
        sb = np.array(self.subblocks)
        blocks = np.array([sb[j:j + 4].mean(axis=0).sum() for j in range(len(sb) - 3)])
        with np.errstate(divide="ignore"):
            lk = -0.691 + 10 * np.log10(blocks)
        gated = blocks[lk > -70.0]
        if gated.size == 0:
            return None
        rel = -0.691 + 10 * np.log10(gated.mean()) - 10.0
        with np.errstate(divide="ignore"):
            lk2 = -0.691 + 10 * np.log10(gated)
        final = gated[lk2 > rel]
        if final.size == 0:
            return None
        return float(-0.691 + 10 * np.log10(final.mean()))


def measure_tone(x: np.ndarray, fs: int, expected_hz: float, search_hz: float = 2.0) -> tuple[float, float]:
    """Frequency (Hz) and linear magnitude of the dominant tone near expected_hz.

    Method: 4-term Blackman-Harris window, 8x zero padding, quadratic interpolation of the log-magnitude
    peak. For a >=10 s segment the bin spacing is 0.0125 Hz before interpolation.
    """
    n = len(x)
    w = np.blackman(n) if n < 8 else _bh4(n)
    nfft = 1 << int(math.ceil(math.log2(n * 8)))
    spec = np.abs(np.fft.rfft(x * w, nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / fs)
    band = np.where((freqs > expected_hz - search_hz) & (freqs < expected_hz + search_hz))[0]
    k = band[np.argmax(spec[band])]
    a, b, c = np.log(spec[k - 1] + 1e-300), np.log(spec[k] + 1e-300), np.log(spec[k + 1] + 1e-300)
    denom = a - 2 * b + c
    delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    f = (k + delta) * fs / nfft
    mag = math.exp(b - 0.25 * (a - c) * delta) / w.sum() * 2
    return float(f), float(mag)


def magnitude_at(x: np.ndarray, fs: int, freq: float) -> float:
    """Windowed single-frequency magnitude (Goertzel-equivalent DTFT at freq)."""
    n = len(x)
    w = _bh4(n)
    t = np.arange(n) / fs
    return float(abs(np.sum(x * w * np.exp(-2j * math.pi * freq * t))) / w.sum() * 2)


def _bh4(n: int) -> np.ndarray:
    a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
    k = np.arange(n) / (n - 1)
    return a0 - a1 * np.cos(2 * math.pi * k) + a2 * np.cos(4 * math.pi * k) - a3 * np.cos(6 * math.pi * k)


def analyze_layer(layer: np.ndarray, profile: Profile, fs: int = FS, start_frame: int = 0) -> dict:
    """Technical verification of the isolated layer (steady part after the fade-in)."""
    fl, fr = profile.f_left, profile.f_right
    exp_beat = profile.beat_hz
    if profile.beat_curve:  # NAP-021: expected = mean instantaneous frequency over the analysed window
        exp_beat = float(np.mean(profile.beat_at((start_frame + np.arange(len(layer))) / fs)))
        fl, fr = profile.carrier_center_hz - exp_beat / 2, profile.carrier_center_hz + exp_beat / 2
    left, right = layer[:, 0], layer[:, 1]
    fl_m, ml = measure_tone(left, fs, fl)
    fr_m, mr = measure_tone(right, fs, fr)
    opp_in_left = magnitude_at(left, fs, fr)
    opp_in_right = magnitude_at(right, fs, fl)
    sup_l = lin_to_db(ml) - lin_to_db(opp_in_left) if opp_in_left > 0 else math.inf
    sup_r = lin_to_db(mr) - lin_to_db(opp_in_right) if opp_in_right > 0 else math.inf
    return {
        "method": "Blackman-Harris-4 window, 8x zero-padded FFT, quadratic log-peak interpolation; "
                  "opposite carrier: windowed DTFT at the other carrier frequency",
        "segment_seconds": round(len(left) / fs, 3),
        "left_hz": round(fl_m, 5), "right_hz": round(fr_m, 5), "beat_hz": round(fr_m - fl_m, 5),
        "expected": {"left_hz": round(fl, 6), "right_hz": round(fr, 6), "beat_hz": round(exp_beat, 6)},
        "left_error_hz": round(abs(fl_m - fl), 6), "right_error_hz": round(abs(fr_m - fr), 6),
        "opposite_suppression_db": {"left": _finite(sup_l), "right": _finite(sup_r)},
        "tone_peak_dbfs": {"left": round(lin_to_db(ml), 3), "right": round(lin_to_db(mr), 3)},
    }


def _finite(x: float) -> float | str:
    return ">200" if not math.isfinite(x) or x > 200 else round(x, 2)
