"""Acceptance criteria 1-6 (+ determinism) for the binaural converter. Real files through real ffmpeg."""
import math
import os
import subprocess

import numpy as np
import pytest

from app.dsp.engine import ALPHA_DEMO_V1, FS, BinauralRenderer, Profile, TruePeakMeter, analyze_layer
from app.dsp.media import MediaError
from app.dsp.pipeline import Limits, _wav_header, convert, read_wav_pcm

LIM = Limits(max_bytes=1 << 30, max_seconds=7200)
ALPHA = ALPHA_DEMO_V1.to_dict()


def write_wav(path, data: np.ndarray, fs: int):
    data = np.atleast_2d(data.T).T if data.ndim == 1 else data
    if data.ndim == 1:
        data = data[:, None]
    ch = data.shape[1]
    q = np.clip(np.rint(data * 32767), -32768, 32767).astype("<i2")
    with open(path, "wb") as fh:
        hdr = _wav_header(data.shape[0], ch, 16, fs)
        fh.write(hdr)
        fh.write(q.tobytes())
    return path


def speechlike(n, fs, seed=1, level=0.3):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    return level * env * (0.6 * np.sin(2 * np.pi * 180 * t) + 0.4 * rng.standard_normal(n) * 0.3)


@pytest.mark.parametrize("fs,channels", [(44100, 1), (44100, 2), (48000, 1), (48000, 2)])
def test_formats_mono_stereo_44_48(tmp_path, fs, channels):
    n = fs * 3
    sig = speechlike(n, fs)
    data = sig[:, None] if channels == 1 else np.stack([sig, speechlike(n, fs, 2)], axis=1)
    src = write_wav(tmp_path / "in.wav", data, fs)
    out = convert(str(src), str(tmp_path / "w"), ALPHA, LIM)
    pcm = read_wav_pcm(out.master)
    expected = n * 48000 / fs
    assert abs(pcm.shape[0] - expected) <= 1
    assert out.params["output"]["frames"] == pcm.shape[0]
    assert pcm.shape[1] == 2
    assert np.all(np.isfinite(pcm))
    assert out.report["checks"]["clipped_samples"] == 0
    assert out.report["levels"]["true_peak_master_dbtp"] <= -1.0
    # wav master header: 24 bit, 48 kHz, stereo
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,bits_per_sample,codec_name",
                            "-of", "csv=p=0", out.master], capture_output=True, text=True).stdout.strip()
    assert probe == "pcm_s24le,48000,2,24"
    if channels == 1:  # mono duplicated: L - layer == R - layer
        rend = BinauralRenderer(Profile.from_dict(ALPHA), pcm.shape[0])
        lay = rend.layer(pcm.shape[0])
        g = 10 ** (out.params["processing"]["common_gain_db"] / 20)
        src_l = pcm[:, 0] - g * lay[:, 0]
        src_r = pcm[:, 1] - g * lay[:, 1]
        assert np.max(np.abs(src_l - src_r)) < 1e-5


@pytest.mark.parametrize("fmt,args", [("flac", ["-c:a", "flac"]), ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
                                      ("m4a", ["-c:a", "aac", "-b:a", "192k"])])
def test_compressed_inputs(tmp_path, fmt, args):
    src = write_wav(tmp_path / "in.wav", np.stack([speechlike(96000, 48000)] * 2, axis=1), 48000)
    enc = tmp_path / f"in.{fmt}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *args, str(enc)], check=True)
    out = convert(str(enc), str(tmp_path / "w"), ALPHA, LIM)
    assert out.params["input"]["probe"]["format_name"]
    assert out.report["status"] == "passed"


def test_real_format_checked_by_content(tmp_path):
    """An MP3 renamed to .wav is detected by content; a text file named .wav is rejected."""
    src = write_wav(tmp_path / "in.wav", speechlike(48000, 48000)[:, None], 48000)
    mp3 = tmp_path / "disguised.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-f", "mp3", str(mp3)], check=True)
    out = convert(str(mp3), str(tmp_path / "w"), ALPHA, LIM)
    assert out.params["input"]["probe"]["format_name"] == "mp3"
    txt = tmp_path / "fake.wav"
    txt.write_text("this is not audio at all " * 100)
    with pytest.raises(MediaError) as e:
        convert(str(txt), str(tmp_path / "w2"), ALPHA, LIM)
    assert e.value.code in ("unsupported_or_corrupt", "unsupported_format")


def test_corrupt_empty_and_multichannel_rejected(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(MediaError) as e:
        convert(str(empty), str(tmp_path / "a"), ALPHA, LIM)
    assert e.value.code == "empty_file"

    hdr_only = tmp_path / "hdr.wav"
    hdr_only.write_bytes(_wav_header(0, 2, 16, 48000))
    with pytest.raises(MediaError) as e:
        convert(str(hdr_only), str(tmp_path / "b"), ALPHA, LIM)
    assert e.value.code in ("empty_audio", "unsupported_or_corrupt")

    good = write_wav(tmp_path / "g.wav", speechlike(48000 * 2, 48000)[:, None], 48000)
    flac = tmp_path / "g.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(good), str(flac)], check=True)
    raw = bytearray(flac.read_bytes())
    for i in range(len(raw) // 3, len(raw) // 3 + 4000):  # damage frames in the middle
        raw[i] = (raw[i] * 7 + 13) % 256
    bad = tmp_path / "bad.flac"
    bad.write_bytes(bytes(raw))
    with pytest.raises(MediaError) as e:
        convert(str(bad), str(tmp_path / "c"), ALPHA, LIM)
    assert e.value.code in ("decode_failed", "truncated_audio", "unsupported_or_corrupt")

    trunc = tmp_path / "trunc.wav"
    trunc.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])  # header claims 2 s, data has 1 s
    with pytest.raises(MediaError) as e:
        convert(str(trunc), str(tmp_path / "d"), ALPHA, LIM)
    assert e.value.code in ("truncated_audio", "decode_failed")

    six = tmp_path / "six.wav"
    write_wav(six, np.zeros((48000, 6)) + 0.01, 48000)
    with pytest.raises(MediaError) as e:
        convert(str(six), str(tmp_path / "e"), ALPHA, LIM)
    assert e.value.code == "too_many_channels"


def test_layer_frequencies_and_separation():
    """Criteria 2 and 3: 250/260 Hz, 10 Hz beat within 0.1 Hz; opposite carrier >= 60 dB down."""
    p = Profile.from_dict(ALPHA)
    n = FS * 12
    lay = BinauralRenderer(p, n).layer(n)
    a = analyze_layer(lay[FS:FS * 11], p)  # 10 s steady segment
    assert abs(a["left_hz"] - 250.0) <= 0.1 and abs(a["right_hz"] - 260.0) <= 0.1
    assert abs(a["beat_hz"] - 10.0) <= 0.1
    for side in ("left", "right"):
        s = a["opposite_suppression_db"][side]
        assert s == ">200" or s >= 60.0
    assert abs(a["tone_peak_dbfs"]["left"] - (-30.0)) < 0.05


def test_blockwise_equals_continuous_reference():
    """Criterion 4: arbitrary block sizes == one-shot == closed-form reference (<=1e-6), no phase resets."""
    p = Profile.from_dict(ALPHA)
    n = FS * 20
    rng = np.random.default_rng(7)
    src = rng.standard_normal((n, 2)) * 0.1
    one, _ = BinauralRenderer(p, n).process(src)
    r = BinauralRenderer(p, n)
    parts, i = [], 0
    while i < n:
        k = int(rng.integers(1, 70000))
        parts.append(r.process(src[i:i + k])[0])
        i += k
    blk = np.concatenate(parts)
    assert np.max(np.abs(blk - one)) <= 1e-6
    # closed-form continuous reference
    k = np.arange(n, dtype=np.float64)
    ref = BinauralRenderer(p, n)
    env = ref.envelope(0, n) * 10 ** (-30 / 20)
    ref_l = 10 ** (-6 / 20) * src[:, 0] + env * np.sin(2 * np.pi * 250.0 * k / FS)
    ref_r = 10 ** (-6 / 20) * src[:, 1] + env * np.sin(2 * np.pi * 260.0 * k / FS)
    assert np.max(np.abs(blk[:, 0] - ref_l)) <= 1e-6
    assert np.max(np.abs(blk[:, 1] - ref_r)) <= 1e-6
    # envelope starts and ends at zero
    assert env[0] == 0.0 and env[-1] == 0.0


def test_short_file_fades_shrink_proportionally():
    p = Profile.from_dict(ALPHA)
    r = BinauralRenderer(p, 24000)  # 0.5 s < 0.5 + 0.5 s fades
    assert r.fi + r.fo <= 24000 and r.fi == r.fo == 12000


def test_true_peak_ceiling_with_loud_source(tmp_path):
    """Criterion 5: loud source forces a single common gain; master TP <= -1 dBTP, no clipping/NaN."""
    rng = np.random.default_rng(3)
    n = FS * 4
    data = np.clip(rng.standard_normal((n, 2)) * 0.5, -1, 1) * 0.999
    src = write_wav(tmp_path / "loud.wav", data, FS)
    prof = dict(ALPHA, source_gain_db=0.0)
    out = convert(str(src), str(tmp_path / "w"), prof, LIM)
    lv = out.report["levels"]
    assert lv["common_gain_db"] < 0
    assert lv["true_peak_master_dbtp"] <= -1.0
    pcm = read_wav_pcm(out.master)
    tp = TruePeakMeter()
    tp.feed(pcm)
    assert tp.dbtp <= -1.0 + 1e-6
    assert pcm.shape[0] == n
    assert np.max(np.abs(pcm)) < 1.0


def test_layer_disabled_preserves_source(tmp_path):
    """Criterion 6: with the layer off and 0 dB gain the 48 kHz source is preserved up to quantization."""
    n = FS * 3
    data = np.stack([speechlike(n, FS, 4), speechlike(n, FS, 5)], axis=1)
    src = write_wav(tmp_path / "s.wav", data, FS)
    prof = dict(ALPHA, source_gain_db=0.0, tone_gain_dbfs=None, slug="passthrough-qa")
    out = convert(str(src), str(tmp_path / "w"), prof, LIM)
    assert out.report["layer"] == "layer_disabled"
    pcm = read_wav_pcm(out.master)
    orig = read_wav_pcm(str(src))
    assert pcm.shape == orig.shape
    assert np.max(np.abs(pcm - orig)) <= 2.0 / 8388607


def test_deterministic_pcm(tmp_path):
    src = write_wav(tmp_path / "s.wav", speechlike(FS * 2, 44100)[:, None], 44100)
    a = convert(str(src), str(tmp_path / "a"), ALPHA, LIM)
    b = convert(str(src), str(tmp_path / "b"), ALPHA, LIM)
    assert a.params["output"]["pcm_sha256"] == b.params["output"]["pcm_sha256"]


def test_silence_reports_not_measurable(tmp_path):
    src = write_wav(tmp_path / "z.wav", np.zeros((FS // 4, 2)), FS)  # 0.25 s silence
    out = convert(str(src), str(tmp_path / "w"), ALPHA, LIM)
    assert out.report["levels"]["source_integrated_lufs"] == "not_measurable"
