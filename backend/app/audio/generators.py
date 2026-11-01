"""Rhythm generators (spec v2.1 §4). Sample values are a closed-form function of the absolute frame index, so
any block split, seek or preview window yields bit-identical samples (deterministic, phase-continuous).

Binaural:  f_L(t) = f_c(t) - Δ(t)/2,  f_R(t) = f_c(t) + Δ(t)/2, with f_c and Δ piecewise linear (automation).
           Φ(t) = ∫ f dt is integrated exactly per segment (quadratic), so frequency ramps keep phase continuity.
Isochronic: one carrier gated at `rate_hz` with duty cycle and raised-cosine edges (smooth, band-limited envelope).
Amplitude is the peak of each sine in dBFS; `ramp_frames` is an explicit sin² fade of the generator clip.
"""
from __future__ import annotations

import math

import numpy as np

from .schema import BinauralParams, IsochronicParams

TWO_PI = 2.0 * math.pi


def _integral(points: list[tuple[float, float]], const: float, t: np.ndarray) -> np.ndarray:
    """∫0^t f(τ)dτ for f piecewise linear through (time_s, value) points, held flat outside them."""
    if not points:
        return const * t
    ts = np.array([p[0] for p in points], dtype=np.float64)
    vs = np.array([p[1] for p in points], dtype=np.float64)
    # cumulative integral at each knot (flat before the first knot)
    cum = np.empty(len(ts))
    cum[0] = vs[0] * ts[0]
    for i in range(1, len(ts)):
        cum[i] = cum[i - 1] + 0.5 * (vs[i - 1] + vs[i]) * (ts[i] - ts[i - 1])
    out = np.empty_like(t)
    before = t <= ts[0]
    out[before] = vs[0] * t[before]
    after = t >= ts[-1]
    out[after] = cum[-1] + vs[-1] * (t[after] - ts[-1])
    mid = ~(before | after)
    if mid.any():
        tm = t[mid]
        i = np.searchsorted(ts, tm, side="right") - 1
        dt = tm - ts[i]
        slope = (vs[i + 1] - vs[i]) / (ts[i + 1] - ts[i])
        out[mid] = cum[i] + vs[i] * dt + 0.5 * slope * dt * dt
    return out


def _ramp(frames: np.ndarray, length: int, ramp: int) -> np.ndarray:
    if ramp <= 0:
        return np.ones(len(frames))
    r = min(ramp, length // 2)
    env = np.ones(len(frames))
    if r <= 0:
        return env
    a = frames < r
    env[a] = np.sin(0.5 * math.pi * frames[a] / r) ** 2
    last = length - 1
    b = frames > last - r
    env[b] = np.minimum(env[b], np.sin(0.5 * math.pi * np.maximum(last - frames[b], 0) / r) ** 2)
    return env


def binaural(p: BinauralParams, fs: int, start: int, n: int, length: int) -> tuple[np.ndarray, np.ndarray]:
    """Left/right samples for clip-relative frames [start, start+n) of a clip `length` frames long."""
    k = np.arange(start, start + n, dtype=np.float64)
    t = k / fs
    cp = [(pt.frame / fs, pt.value) for pt in p.carrier_automation]
    dp = [(pt.frame / fs, pt.value) for pt in p.delta_automation]
    phi_c = TWO_PI * _integral(cp, p.carrier_hz, t)
    phi_d = TWO_PI * _integral(dp, p.delta_hz, t)
    amp = 10.0 ** (p.amplitude_dbfs / 20.0) * _ramp(k, length, p.ramp_frames)
    left = amp * np.sin(np.mod(phi_c - 0.5 * phi_d + math.radians(p.phase_left_deg), TWO_PI))
    right = amp * np.sin(np.mod(phi_c + 0.5 * phi_d + math.radians(p.phase_right_deg), TWO_PI))
    return left, right


def isochronic(p: IsochronicParams, fs: int, start: int, n: int, length: int) -> np.ndarray:
    period = 1.0 / p.rate_hz
    on = p.duty * period
    edge = p.edge_ms / 1000.0
    if 2 * edge > on or 2 * edge > period - on:
        raise ValueError("isochronic edges are longer than the on/off parts: lower edge_ms or change duty/rate")
    k = np.arange(start, start + n, dtype=np.float64)
    t = k / fs
    tau = np.mod(t * p.rate_hz, 1.0) * period
    g = np.zeros(n)
    rise = tau < edge
    g[rise] = 0.5 - 0.5 * np.cos(math.pi * tau[rise] / edge)
    hold = (tau >= edge) & (tau < on - edge)
    g[hold] = 1.0
    fall = (tau >= on - edge) & (tau < on)
    g[fall] = 0.5 + 0.5 * np.cos(math.pi * (tau[fall] - (on - edge)) / edge)
    amp = 10.0 ** (p.amplitude_dbfs / 20.0) * _ramp(k, length, p.ramp_frames)
    return amp * g * np.sin(np.mod(TWO_PI * p.carrier_hz * t, TWO_PI))
