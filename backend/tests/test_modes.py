"""NAP-063 / NAP-108: converter layouts 2.0 (headphones), 3.0 (speakers), 5.1 and 7.1 (NAP chair)."""
import json
import subprocess

import numpy as np
import pytest

from app.dsp.engine import ALPHA_DEMO_V1, FS, Profile, measure_tone
from app.dsp.media import MediaError
from app.dsp.modes import CHANNELS_51, LAYOUTS, LayoutRenderer, Shifter, Stereo20Renderer, verify_mode
from app.dsp.pipeline import Limits
from app.dsp.variants import convert_variant
from tests.test_dsp import speechlike, write_wav

LIM = Limits(max_bytes=1 << 30, max_seconds=7200)
ALPHA = ALPHA_DEMO_V1.to_dict()


def tone(f, seconds, fs=FS, a=0.3):
    return a * np.sin(2 * np.pi * f * np.arange(int(seconds * fs)) / fs)


def test_shifter_moves_a_tone_by_exactly_half_the_beat():
    x = tone(1000, 8)
    for sign in (-0.5, 0.5):
        s = Shifter(sign, ALPHA_DEMO_V1)
        y = s(x)[s.delay.buf.shape[0] + FS:]
        f, mag = measure_tone(y, FS, 1000 + sign * 10)
        assert abs(f - (1000 + sign * 10)) < 0.01
        assert abs(mag - 0.3) < 0.01  # SSB keeps the amplitude
        # nothing left at the original frequency or the mirror image
        from app.dsp.engine import magnitude_at
        assert 20 * np.log10(magnitude_at(y, FS, 1000) / mag) < -60
        assert 20 * np.log10(magnitude_at(y, FS, 1000 - sign * 10) / mag) < -60


def test_block_size_does_not_change_output():
    x = np.stack([speechlike(3 * FS, FS), speechlike(3 * FS, FS, seed=2)], 1)
    one = Stereo20Renderer(ALPHA_DEMO_V1).process(x)
    r = Stereo20Renderer(ALPHA_DEMO_V1)
    parts, i = [], 0
    for n in (1, 777, 4096, 65536, 10):
        parts.append(r.process(x[i:i + n]))
        i += n
    parts.append(r.process(x[i:]))
    assert np.allclose(np.concatenate(parts), one, atol=1e-9)  # FFT rounding only; 24-bit LSB is 1.2e-7


@pytest.mark.parametrize("beat", [0.5, 4.0, 10.0, 40.0])
def test_mode_20_beat_within_0_1_hz(beat):
    p = Profile("t", 1, 300.0, beat, -6.0, None, 0.0, 0.0)
    v = verify_mode("stereo_2_0", p)
    assert v["passed"], v
    assert v["points"][0]["error_hz"] <= 0.1


def test_mode_20_follows_a_frequency_curve():
    p = Profile("t", 1, 300.0, 10.0, -6.0, None, 0.0, 0.0, beat_curve=((0, 10.0), (60, 4.0)))
    v = verify_mode("stereo_2_0", p)
    assert v["passed"] and {pt["beat_hz"] for pt in v["points"]} == {10.0, 4.0}


STEM_CASES = [
    ("acoustic_3_0", set()), ("acoustic_3_0", {"FC"}),
    ("surround_5_1", set()), ("surround_5_1", {"FC"}), ("surround_5_1", {"LFE"}), ("surround_5_1", {"SL", "SR"}),
    ("surround_5_1", {"FC", "LFE", "SL", "SR"}),
    ("surround_7_1", set()), ("surround_7_1", {"BL", "BR"}), ("surround_7_1", {"FC", "LFE", "BL", "BR", "SL", "SR"}),
    ("stereo_2_0", {"FL"}), ("surround_5_1", {"FL", "FR", "FC"}),
]


@pytest.mark.parametrize("mode,stems", STEM_CASES)
def test_layout_channels_own_files_and_no_cross_mixing(mode, stems):
    v = verify_mode(mode, ALPHA_DEMO_V1, stems)
    assert v["passed"], [c for c in v["checks"] if not c["passed"]]
    assert list(v["matrix_dbfs"]) == LAYOUTS[mode]
    src = LayoutRenderer(mode, ALPHA_DEMO_V1, stems).sources()
    for c in LAYOUTS[mode]:
        assert (src[c] == "uploaded") == (c in stems)


@pytest.mark.parametrize("mode", ["acoustic_3_0", "surround_5_1", "surround_7_1"])
def test_binaural_difference_only_in_front_channels(mode):
    v = verify_mode(mode, ALPHA_DEMO_V1)
    m = v["matrix_dbfs"]
    for ch in LAYOUTS[mode][2:]:
        if ch == "LFE":
            continue
        assert m[ch]["main"] - max(m[ch]["main_shifted_left"], m[ch]["main_shifted_right"]) >= 40


def test_layout_renderer_block_size_does_not_change_output():
    x = np.stack([speechlike(2 * FS, FS), speechlike(2 * FS, FS, seed=2)], 1)
    st = {"FC": speechlike(2 * FS, FS, seed=4)[:, None]}
    one = LayoutRenderer("surround_7_1", ALPHA_DEMO_V1, {"FC"}).process(x, st)
    r = LayoutRenderer("surround_7_1", ALPHA_DEMO_V1, {"FC"})
    parts, i = [], 0
    for n in (1, 4096, 50000, 333):
        parts.append(r.process(x[i:i + n], {"FC": st["FC"][i:i + n]}))
        i += n
    parts.append(r.process(x[i:], {"FC": st["FC"][i:]}))
    assert np.allclose(np.concatenate(parts), one, atol=1e-9)


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def test_full_conversion_20(tmp_path):
    src = write_wav(tmp_path / "in.wav", np.stack([speechlike(12 * 44100, 44100), speechlike(12 * 44100, 44100, 3)], 1), 44100)
    out = convert_variant(str(src), str(tmp_path / "w"), ALPHA, LIM, "stereo_2_0")
    assert out.report["status"] == "passed" and out.report["checks"]["delivery_bit_exact"]
    assert [c["channel"] for c in out.report["channels"]] == ["FL", "FR"]
    assert out.report["levels"]["true_peak_master_dbtp"] <= -1.0
    s = _probe(out.delivery)
    assert s["codec_name"] == "flac" and s["channels"] == 2 and s["sample_rate"] == "48000"
    assert out.params["output"]["frames"] == 12 * FS


def test_full_conversion_51_with_and_without_stems(tmp_path):
    n = 10 * 48000
    main = write_wav(tmp_path / "main.wav", np.stack([speechlike(n, 48000), speechlike(n, 48000, 5)], 1), 48000)
    voice = write_wav(tmp_path / "voice.wav", speechlike(n // 2, 48000, 9), 48000)  # shorter: padded
    out = convert_variant(str(main), str(tmp_path / "a"), ALPHA, LIM, "surround_5_1", {"FC": str(voice)})
    rep = out.report
    assert rep["status"] == "passed" and rep["checks"]["binaural_only_in_front"]
    by = {c["channel"]: c for c in rep["channels"]}
    assert list(by) == CHANNELS_51
    assert by["FC"]["uploaded"] and not by["LFE"]["uploaded"] and not by["SL"]["uploaded"]
    assert out.params["stems"]["FC"]["fit"] == "padded"
    s = _probe(out.delivery)
    assert s["channels"] == 6 and s["channel_layout"] == "5.1(side)"
    m = _probe(out.master)
    assert m["channels"] == 6 and m["bits_per_sample"] == 24
    gen = convert_variant(str(main), str(tmp_path / "b"), ALPHA, LIM, "surround_5_1")
    assert not any(c["uploaded"] for c in gen.report["channels"])
    again = convert_variant(str(main), str(tmp_path / "c"), ALPHA, LIM, "surround_5_1")
    assert again.params["output"]["pcm_sha256"] == gen.params["output"]["pcm_sha256"]  # reproducible


def test_loud_source_gets_common_gain_only(tmp_path):
    n = 6 * 48000
    loud = np.stack([tone(440, 6, a=0.99), tone(660, 6, a=0.99)], 1)[:n]
    src = write_wav(tmp_path / "loud.wav", loud, 48000)
    out = convert_variant(str(src), str(tmp_path / "w"), ALPHA, LIM, "surround_5_1")
    assert out.report["levels"]["common_gain_db"] < 0
    assert out.report["levels"]["true_peak_master_dbtp"] <= -1.0
    assert out.report["checks"]["clipped_samples"] == 0


@pytest.mark.parametrize("mode,layout,mask", [("acoustic_3_0", "3.0", 0x7), ("surround_7_1", "7.1", 0x63F)])
def test_full_conversion_30_and_71(tmp_path, mode, layout, mask):
    n = 6 * 48000
    main = write_wav(tmp_path / "main.wav", np.stack([speechlike(n, 48000), speechlike(n, 48000, 5)], 1), 48000)
    stereo_voice = write_wav(tmp_path / "v.wav", np.stack([speechlike(n, 48000, 7)] * 2, 1), 48000)  # downmixed to mono
    out = convert_variant(str(main), str(tmp_path / "w"), ALPHA, LIM, mode, {"FC": str(stereo_voice)})
    assert out.report["status"] == "passed"
    assert [c["channel"] for c in out.report["channels"]] == LAYOUTS[mode]
    assert {c["channel"] for c in out.report["channels"] if c["uploaded"]} == {"FC"}
    assert out.params["output"]["channel_mask"] == mask
    s = _probe(out.delivery)
    assert s["channels"] == len(LAYOUTS[mode]) and s["channel_layout"] == layout
    with open(out.master, "rb") as fh:  # WAVE_FORMAT_EXTENSIBLE carries the speaker mask
        head = fh.read(64)
    assert int.from_bytes(head[40:44], "little") == mask


def test_invalid_requests(tmp_path):
    src = write_wav(tmp_path / "in.wav", speechlike(2 * 48000, 48000), 48000)
    with pytest.raises(MediaError) as e:
        convert_variant(str(src), str(tmp_path / "w"), ALPHA, LIM, "stereo_2_0", {"FC": str(src)})
    assert e.value.code == "unknown_stem"     # no centre channel in 2.0
    with pytest.raises(MediaError) as e:
        convert_variant(str(src), str(tmp_path / "w"), ALPHA, LIM, "surround_5_1", {"rear": str(src)})
    assert e.value.code == "unknown_stem"
    with pytest.raises(MediaError) as e:
        convert_variant(str(src), str(tmp_path / "w"), ALPHA, LIM, "7.1")
    assert e.value.code == "unknown_mode"
