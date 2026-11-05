"""NAP Audio Studio M1 (spec v2.1): acceptance tests T01–T09 with real ffmpeg decoding and independent readers."""
import hashlib
import json
import os
import struct
import subprocess
import time
import uuid

import numpy as np
import pytest

from app.audio.generators import binaural, isochronic
from app.audio.render import RenderError, Source, render, verify
from app.audio.schema import PROFILES, BinauralParams, IsochronicParams, ProjectState
from app.db import session_scope
from app.dsp.engine import measure_tone
from app.models import Asset
from app.storage import get_storage

from .conftest import Client, ok

FS = 48_000


# ------------------------------------------------------------------ helpers

def wav24(path, data: np.ndarray, fs=FS):
    """Integer PCM 24-bit WAV (plain header) from float data in [-1, 1) on the 2^23 grid."""
    data = data if data.ndim == 2 else data[:, None]
    q = np.rint(data * (1 << 23)).astype("<i4")
    ch = data.shape[1]
    raw = q.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVEfmt " +
                 struct.pack("<IHHIIHH", 16, 1, ch, fs, fs * ch * 3, ch * 3, 24) + b"data" + struct.pack("<I", len(raw)))
        fh.write(raw)
    return path


def read_pcm(path) -> tuple[np.ndarray, dict]:
    """Independent decode with ffmpeg to int32 (24-bit values left-justified) → integers on the source grid."""
    info = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path], capture_output=True,
                          text=True, check=True).stdout
    import json
    st = json.loads(info)["streams"][0]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s32le", "-acodec", "pcm_s32le", "pipe:1"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<i4").reshape(-1, int(st["channels"])) >> 8
    return x, st


def src_from_array(tmp_path, name, data, fs=FS) -> Source:
    data = data if data.ndim == 2 else data[:, None]
    p = tmp_path / f"{name}.f64"
    data.astype("<f8").tofile(p)
    return Source(str(p), data.shape[1], data.shape[0], {})


def project(tracks, generators=(), on_clip="fail"):
    return {"tracks": tracks, "generators": list(generators), "output": {"on_clip": on_clip}}


def tone(f, n, a=0.25, fs=FS):
    return a * np.sin(2 * np.pi * f * np.arange(n) / fs)


# ------------------------------------------------------------------ DSP / render (unit level)

def test_t05_binaural_steady_state_196_204():
    p = BinauralParams(carrier_hz=200, delta_hz=8, amplitude_dbfs=-12)
    n = 20 * FS
    left, right = binaural(p, FS, 0, n, n)
    fl, _ = measure_tone(left[FS:], FS, 196)
    fr, _ = measure_tone(right[FS:], FS, 204)
    assert abs(fl - 196) <= 0.01 and abs(fr - 204) <= 0.01 and abs((fr - fl) - 8) <= 0.01


def test_t06_phase_continuity_and_block_independence():
    p = BinauralParams(carrier_hz=250, delta_hz=10, amplitude_dbfs=-6,
                       delta_automation=[{"frame": FS, "value": 10}, {"frame": 6 * FS, "value": 4}],
                       carrier_automation=[{"frame": 2 * FS, "value": 250}, {"frame": 5 * FS, "value": 300}])
    n = 8 * FS
    whole_l, whole_r = binaural(p, FS, 0, n, n)
    l2, r2, s, sizes = [], [], 0, [1, 4096, 999, 65536, 12345]
    i = 0
    while s < n:                                      # irregular blocks, as a streaming preview would request
        m = min(sizes[i % len(sizes)], n - s)
        a, b = binaural(p, FS, s, m, n)
        l2.append(a); r2.append(b); s += m; i += 1
    assert np.array_equal(np.concatenate(l2), whole_l) and np.array_equal(np.concatenate(r2), whole_r)
    amp = 10 ** (-6 / 20)
    bound = 2 * np.pi * 305 * amp / FS * 1.01        # max step of a sine at the highest frequency: no clicks
    assert np.max(np.abs(np.diff(whole_l))) <= bound and np.max(np.abs(np.diff(whole_r))) <= bound
    fl, _ = measure_tone(whole_l[int(6.5 * FS):], FS, 298)
    fr, _ = measure_tone(whole_r[int(6.5 * FS):], FS, 302)
    assert abs((fr - fl) - 4) <= 0.01                 # automation reached its end values


def test_isochronic_gate_and_no_clicks():
    p = IsochronicParams(carrier_hz=440, rate_hz=10, duty=0.5, edge_ms=5, amplitude_dbfs=-6)
    n = 2 * FS
    y = isochronic(p, FS, 0, n, n)
    env = np.abs(y).reshape(-1, FS // 10).max(axis=1)  # one value per period
    assert np.all(env > 0.4)
    assert np.all(np.abs(y[int(0.06 * FS):int(0.094 * FS)]) == 0)   # off half of the first period is silent
    assert np.max(np.abs(np.diff(y))) < 0.1
    with pytest.raises(ValueError):
        isochronic(IsochronicParams(carrier_hz=440, rate_hz=40, duty=0.5, edge_ms=10, amplitude_dbfs=-6), FS, 0, 10, 10)


def test_t02_t07_transparent_pass_through(tmp_path):
    rng = np.random.default_rng(3)
    ints = rng.integers(-(1 << 22), 1 << 22, size=FS * 2)
    data = ints / float(1 << 23)
    s = src_from_array(tmp_path, "a", data)
    st = project([{"id": "t1", "bus": "NEURO_LEFT", "clips": [{"id": "c1", "asset_id": "a", "start_frame": 0,
                                                                "duration_frames": len(ints)}]}])
    res = render(st, fs=FS, bits=24, profile_key="HEADPHONES_2_0", sources={"a": s}, out_path=str(tmp_path / "o.wav"))
    pcm, info = read_pcm(res.path)
    assert np.array_equal(pcm[:, 0], ints)        # bit-identical, no gain/normalisation/dither
    assert np.all(pcm[:, 1] == 0)                 # nothing leaks to the other ear
    assert res.report["processing"]["src"] == {} and res.report["hard_clipped_samples"] == 0
    assert verify(res.path, res)["passed"]


@pytest.mark.parametrize("profile", ["NAP_CHAIR_5_1", "NAP_PRO_7_1", "NAP_ACOUSTIC_3_0"])
def test_t03_channel_mapping_markers(tmp_path, profile):
    buses = ["NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER", "MUSIC_LEFT", "MUSIC_RIGHT", "LFE", "BACK_LEFT", "BACK_RIGHT"]
    freqs = {b: 300 + 100 * i for i, b in enumerate(buses)}
    n = FS
    sources = {b: src_from_array(tmp_path, b, tone(freqs[b], n)) for b in buses}
    st = project([{"id": f"t{i}", "bus": b, "clips": [{"id": f"c{i}", "asset_id": b, "start_frame": 0,
                                                       "duration_frames": n}]} for i, b in enumerate(buses)])
    res = render(st, fs=FS, bits=24, profile_key=profile, sources=sources, out_path=str(tmp_path / "o.wav"))
    pcm, info = read_pcm(res.path)
    prof = PROFILES[profile]
    assert info["channels"] == len(prof["channels"])
    assert {"NAP_CHAIR_5_1": "5.1(side)", "NAP_PRO_7_1": "7.1", "NAP_ACOUSTIC_3_0": "3.0"}[profile] == info["channel_layout"]
    x = pcm / float(1 << 23)
    for i, row in enumerate(prof["matrix"]):
        if len(row) != 1:
            continue
        (bus, _), = row.items()
        expected = np.rint(tone(freqs[bus], n) * (1 << 23)) / (1 << 23)
        assert np.array_equal(x[:, i], expected), f"{prof['channels'][i]} should carry exactly {bus}"


def test_t04_alignment_at_120_minutes(tmp_path):
    join = 119 * 60 * FS + 12345                    # late in a 120-minute timeline
    n = 1000
    a = src_from_array(tmp_path, "a", np.full(n, 0.25))
    b = src_from_array(tmp_path, "b", np.full(n, -0.5))
    st = project([{"id": "t1", "bus": "NEURO_LEFT", "clips": [
        {"id": "c1", "asset_id": "a", "start_frame": join - n, "duration_frames": n},
        {"id": "c2", "asset_id": "b", "start_frame": join, "duration_frames": n}]}])
    assert ProjectState.model_validate(st).duration_frames() == join + n
    res = render(st, fs=FS, bits=24, profile_key="HEADPHONES_2_0", sources={"a": a, "b": b},
                 out_path=str(tmp_path / "w.wav"), start=join - 4, frames=8)
    pcm, _ = read_pcm(res.path)
    v = pcm[:, 0] / float(1 << 23)
    assert list(v) == [0.25] * 4 + [-0.5] * 4        # the join lands exactly on its frame (error 0 samples)


def test_t08_clipping_is_an_error_unless_explicitly_allowed(tmp_path):
    n = FS // 10
    loud = src_from_array(tmp_path, "l", np.full(n, 0.75))
    tracks = [{"id": "t1", "bus": "NEURO_LEFT", "clips": [{"id": "c1", "asset_id": "l", "start_frame": 0, "duration_frames": n}]},
              {"id": "t2", "bus": "MUSIC_LEFT", "clips": [{"id": "c2", "asset_id": "l", "start_frame": 0, "duration_frames": n}]}]
    with pytest.raises(RenderError) as e:
        render(project(tracks), fs=FS, bits=24, profile_key="HEADPHONES_2_0", sources={"l": loud},
               out_path=str(tmp_path / "c.wav"))
    assert e.value.code == "clipping" and e.value.data["channels"]["FL"]["first_frame"] == 0
    assert not os.path.exists(tmp_path / "c.wav")    # no half-written file
    res = render(project(tracks, on_clip="hard_clip"), fs=FS, bits=24, profile_key="HEADPHONES_2_0",
                 sources={"l": loud}, out_path=str(tmp_path / "h.wav"))
    assert res.report["hard_clipped_samples"] == n and res.report["channels"][0]["clipped_samples"] == n


@pytest.mark.parametrize("fs,bits", [(48_000, 24), (96_000, 24), (44_100, 16), (48_000, 32)])
def test_t09_wav_formats_decode_independently(tmp_path, fs, bits):
    n = fs // 2
    s = src_from_array(tmp_path, "a", tone(1000, n, fs=fs))
    st = project([{"id": "t1", "bus": "VOICE_CENTER", "clips": [{"id": "c1", "asset_id": "a", "start_frame": 0,
                                                                  "duration_frames": n}]}])
    res = render(st, fs=fs, bits=bits, profile_key="NAP_CHAIR_5_1", sources={"a": s}, out_path=str(tmp_path / "f.wav"))
    v = verify(res.path, res)
    assert v["passed"] and v["sample_rate"] == fs and v["channels"] == 6 and v["ffprobe_layout"] == "5.1(side)"
    silent = [c["channel"] for c in res.report["channels"] if c["silent"]]
    assert silent == ["FL", "FR", "LFE", "SL", "SR"]  # only FC carries the voice


def test_deterministic_render(tmp_path):
    gen = {"id": "g1", "type": "binaural", "params": {"carrier_hz": 220, "delta_hz": 6, "amplitude_dbfs": -18,
                                                       "ramp_frames": 4800}}
    st = project([{"id": "t1", "bus": "NEURO_LEFT", "clips": [{"id": "c1", "generator_id": "g1", "generator_output": "left",
                                                                "start_frame": 0, "duration_frames": 3 * FS}]},
                  {"id": "t2", "bus": "NEURO_RIGHT", "clips": [{"id": "c2", "generator_id": "g1", "generator_output": "right",
                                                                 "start_frame": 0, "duration_frames": 3 * FS}]}], [gen])
    a = render(st, fs=FS, bits=24, profile_key="HEADPHONES_2_0", sources={}, out_path=str(tmp_path / "1.wav"))
    b = render(st, fs=FS, bits=24, profile_key="HEADPHONES_2_0", sources={}, out_path=str(tmp_path / "2.wav"))
    assert a.report["sha256"] == b.report["sha256"]
    pcm, _ = read_pcm(a.path)
    fl, _ = measure_tone(pcm[FS:2 * FS, 0].astype(float), FS, 217)
    fr, _ = measure_tone(pcm[FS:2 * FS, 1].astype(float), FS, 223)
    assert abs((fr - fl) - 6) < 0.05


# ------------------------------------------------------------------ API level (T01, revisions, jobs, preview, authZ)

def _upload(dev: Client, path, rights=None):
    data = open(path, "rb").read()
    t = ok(dev.post("/audio/assets", {"filename": os.path.basename(path), "size_bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest(),
                                      "rights": rights or {"source": "own recording", "license": "all rights reserved"}}), 201)
    r = dev.c.put(t["upload_url"], content=data, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return t["asset_id"], hashlib.sha256(data).hexdigest()


def _wait(dev, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = ok(dev.get(f"/audio/render-jobs/{job_id}"))
        if j["state"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.2)
    raise AssertionError("render did not finish")


def test_studio_api_flow_t01_immutable_sources(tmp_path):
    dev = Client().register(developer=True)
    rng = np.random.default_rng(1)
    voice = wav24(tmp_path / "voice.wav", (rng.integers(-(1 << 20), 1 << 20, size=FS) / float(1 << 23)))
    aid, digest = _upload(dev, str(voice))
    a = ok(dev.get(f"/audio/assets/{aid}"))
    assert a["sha256"] == digest and a["channels"] == 1 and a["sample_rate"] == FS and a["bit_depth"] == 24
    assert a["immutable"] and a["rights"]["source"] == "own recording"
    wf = ok(dev.get(f"/audio/assets/{aid}/waveform?buckets=128"))
    assert wf["buckets"] == 128 and len(wf["channels"][0]["max"]) == 128

    pr = ok(dev.post("/audio/projects", {"title": "Focus 01", "bus_count": 3}), 201)
    assert [t["bus"] for t in pr["state"]["tracks"]] == ["NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER"]
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {"carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -20}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": FS}]
    st["tracks"][1]["clips"] = [{"id": "c2", "generator_id": "g1", "generator_output": "right", "start_frame": 0, "duration_frames": FS}]
    st["tracks"][2]["clips"] = [{"id": "c3", "asset_id": aid, "start_frame": 0, "duration_frames": FS}]
    saved = ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    assert saved["revision"] == pr["revision"] + 1 and saved["duration_frames"] == FS
    stale = dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    bad = dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": saved["revision"], "state": {**st, "tracks": [
        {"id": "x", "bus": "NOPE", "clips": []}]}})
    assert bad.status_code == 422

    job = ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "NAP_ACOUSTIC_3_0"},
                      headers={"Idempotency-Key": "r1"}), 201)
    again = ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "NAP_ACOUSTIC_3_0"},
                        headers={"Idempotency-Key": "r1"}), 201)
    assert again["id"] == job["id"]
    done = _wait(dev, job["id"])
    assert done["state"] == "completed", (done.get("error_code"), done.get("error_detail"), done.get("logs"))
    art = done["artifact"]
    assert art["layout"] == "3.0" and art["channel_order"] == ["FL", "FR", "FC"] and art["report"]["verification"]["passed"]
    link = ok(dev.get(f"/audio/render-jobs/{job['id']}/download"))
    body = dev.c.get(link["url"]).content
    assert hashlib.sha256(body).hexdigest() == art["sha256"]
    x, info = read_pcm_bytes(tmp_path, body)
    assert np.array_equal(x[:, 2], _ints(voice))     # FC == uploaded voice, bit-exact

    with session_scope() as db:                       # T01: the stored original is unchanged
        asset = db.get(Asset, uuid.UUID(aid))
        assert get_storage().sha256(asset.storage_key) == digest

    # preview renders the same samples through the export path
    prev = dev.c.post(f"/api/v1/audio/projects/{pr['id']}/preview", json={"start_frame": 1000, "frames": 2000,
                      "profile": "NAP_ACOUSTIC_3_0"}, headers=dev._h())
    assert prev.status_code == 200 and prev.headers["x-render-path"] == "export-identical"
    pv, _ = read_pcm_bytes(tmp_path, prev.content)
    assert np.array_equal(pv, x[1000:3000])

    other = Client().register(developer=True)         # owners only
    assert other.get(f"/audio/projects/{pr['id']}").status_code == 404
    assert other.get(f"/audio/render-jobs/{job['id']}").status_code == 404
    assert other.get(f"/audio/assets/{aid}").status_code == 404
    listener = Client().register()
    assert listener.get("/audio/projects").status_code == 403


def test_render_job_reports_clipping_honestly(tmp_path):
    dev = Client().register(developer=True)
    aid, _ = _upload(dev, str(wav24(tmp_path / "loud.wav", np.full(FS // 4, 0.75))))
    pr = ok(dev.post("/audio/projects", {"title": "Loud", "bus_count": 2}), 201)
    st = pr["state"]
    st["tracks"][0]["clips"] = [{"id": "a", "asset_id": aid, "start_frame": 0, "duration_frames": FS // 4}]
    st["tracks"].append({"id": "t9", "bus": "MUSIC_LEFT", "clips": [{"id": "b", "asset_id": aid, "start_frame": 0,
                                                                      "duration_frames": FS // 4}]})
    pr = ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    job = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "HEADPHONES_2_0"}), 201)["id"])
    assert job["state"] == "failed" and job["error_code"] == "clipping" and job["error_detail"]["channels"]["FL"]
    profs = {p["key"]: p for p in ok(dev.get("/audio/routing-profiles"))["items"]}
    assert profs["NAP_ACOUSTIC_3_0"]["channels"] == ["FL", "FR", "FC"] and "not 2.1" in profs["NAP_ACOUSTIC_3_0"]["note"]


def test_upload_requires_rights_and_supported_format(tmp_path):
    dev = Client().register(developer=True)
    r = dev.post("/audio/assets", {"filename": "a.wav", "size_bytes": 10, "rights": {}})
    assert r.status_code == 422
    r = dev.post("/audio/assets", {"filename": "a.exe", "size_bytes": 10, "rights": {"source": "x", "license": "y"}})
    assert r.status_code == 415


def _ints(path):
    raw = open(path, "rb").read()[44:]
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    return np.where(v >= 1 << 23, v - (1 << 24), v)


def read_pcm_bytes(tmp_path, data: bytes):
    p = tmp_path / f"b-{uuid.uuid4().hex[:6]}.wav"
    p.write_bytes(data)
    return read_pcm(str(p))


def test_render_recovers_after_worker_crash():
    """A worker that dies mid-render leaves an expired lease: recovery re-queues the job and it completes."""
    from datetime import timedelta

    from app.db import utcnow
    from app.models import RenderJob
    from app.services.audio_projects import recover_render_leases, run_render

    dev = Client().register(developer=True)
    pr = ok(dev.post("/audio/projects", {"title": "Crash", "bus_count": 2}), 201)
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "isochronic", "params": {"carrier_hz": 300, "rate_hz": 10, "amplitude_dbfs": -20}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "start_frame": 0, "duration_frames": FS // 2}]
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    with session_scope() as db:                       # simulate: claimed by a worker that then vanished
        from app.services import audio_projects as svc
        from app.models import AudioProject
        p = db.get(AudioProject, uuid.UUID(pr["id"]))
        job = svc.start_render(db, p, p.owner_id, "HEADPHONES_2_0", None)
        job.state, job.attempt, job.lease_owner = "rendering", 1, "dead-worker"
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        job_id = str(job.id)
    assert job_id in recover_render_leases()
    with session_scope() as db:
        assert db.get(RenderJob, uuid.UUID(job_id)).state == "queued"
    assert run_render(job_id) == "completed"
    j = ok(dev.get(f"/audio/render-jobs/{job_id}"))
    assert j["state"] == "completed" and j["attempt"] == 2 and any("lease expired" in x["msg"] for x in j["logs"])


# ------------------------------------------------------------------ M2: RF64 and FLAC

def test_rf64_above_the_wav_limit(tmp_path, monkeypatch):
    """Above 4 GiB the file is RF64 (EBU Tech 3306); ffmpeg and our reader both read it, samples bit-exact."""
    import app.audio.render as R
    monkeypatch.setattr(R, "WAV_LIMIT", 1000)          # make a small render cross the limit
    ints = np.random.default_rng(7).integers(-(1 << 22), 1 << 22, size=FS)
    s = src_from_array(tmp_path, "a", ints / float(1 << 23))
    st = project([{"id": "t1", "bus": "VOICE_CENTER", "clips": [{"id": "c1", "asset_id": "a", "start_frame": 0,
                                                                  "duration_frames": FS}]}])
    res = render(st, fs=FS, bits=24, profile_key="NAP_PRO_7_1", sources={"a": s}, out_path=str(tmp_path / "r.wav"))
    head = open(res.path, "rb").read(64)
    assert head[:4] == b"RF64" and head[12:16] == b"ds64" and res.report["container"] == "RF64"
    v = verify(res.path, res)
    assert v["passed"] and v["container"] == "RF64" and v["ffprobe_layout"] == "7.1" and v["frames"] == FS
    pcm, info = read_pcm(res.path)
    assert np.array_equal(pcm[:, 2], ints) and not pcm[:, [0, 1, 3, 4, 5, 6, 7]].any()


@pytest.mark.parametrize("profile,bits", [("HEADPHONES_2_0", 16), ("NAP_CHAIR_5_1", 24), ("NAP_PRO_7_1", 24)])
def test_flac_is_lossless_and_keeps_the_layout(tmp_path, profile, bits):
    from app.audio.render import encode_flac, verify_flac
    buses = ["NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER", "MUSIC_LEFT", "MUSIC_RIGHT", "LFE", "BACK_LEFT", "BACK_RIGHT"]
    srcs = {b: src_from_array(tmp_path, b, tone(300 + 100 * i, FS // 2)) for i, b in enumerate(buses)}
    st = project([{"id": f"t{i}", "bus": b, "clips": [{"id": f"c{i}", "asset_id": b, "start_frame": 0,
                                                       "duration_frames": FS // 2}]} for i, b in enumerate(buses)])
    res = render(st, fs=FS, bits=bits, profile_key=profile, sources=srcs, out_path=str(tmp_path / "r.wav"))
    wav_check = verify(res.path, res)
    encode_flac(res.path, str(tmp_path / "r.flac"), bits)
    v = verify_flac(str(tmp_path / "r.flac"), res.path, wav_check, res)
    assert v["passed"] and v["samples_identical_to_wav"] and v["bits"] == bits
    assert v["ffprobe_layout"] == {"HEADPHONES_2_0": "stereo", "NAP_CHAIR_5_1": "5.1(side)", "NAP_PRO_7_1": "7.1"}[profile]
    assert os.path.getsize(tmp_path / "r.flac") < os.path.getsize(res.path)
    a, _ = read_pcm(res.path)
    b, _ = read_pcm(str(tmp_path / "r.flac"))
    assert np.array_equal(a, b)                       # independent decode: identical samples


def test_flac_refuses_float(tmp_path):
    from app.audio.render import encode_flac
    with pytest.raises(RenderError) as e:
        encode_flac(str(tmp_path / "x.wav"), str(tmp_path / "x.flac"), 32)
    assert e.value.code == "flac_needs_integer"


def test_flac_export_via_api(tmp_path):
    dev = Client().register(developer=True)
    pr = ok(dev.post("/audio/projects", {"title": "FLAC", "bus_count": 6}), 201)
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {"carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -18}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": FS}]
    st["tracks"][1]["clips"] = [{"id": "c2", "generator_id": "g1", "generator_output": "right", "start_frame": 0, "duration_frames": FS}]
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    assert dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "NAP_CHAIR_5_1", "format": "mp3"}).status_code == 422
    job = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "NAP_CHAIR_5_1", "format": "flac"}), 201)["id"])
    assert job["state"] == "completed", (job["error_code"], job["error_detail"])
    art = job["artifact"]
    assert job["format"] == "flac" and art["format"] == "flac" and art["report"]["flac"]["samples_identical_to_wav"]
    link = ok(dev.get(f"/audio/render-jobs/{job['id']}/download"))
    assert link["format"] == "flac" and link["url"]
    body = dev.c.get(link["url"]).content
    assert body[:4] == b"fLaC" and hashlib.sha256(body).hexdigest() == art["sha256"]
    x, info = read_pcm_bytes(tmp_path, body)
    assert info["channel_layout"] == "5.1(side)" and x.shape == (FS, 6) and x[:, 4].any() and not x[:, 2].any()
    fl = dev.post("/audio/projects", {"title": "float", "bus_count": 2, "bit_depth": 32})
    fp = ok(fl, 201)
    r = dev.post(f"/audio/projects/{fp['id']}/renders", {"profile": "HEADPHONES_2_0", "format": "flac"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "flac_needs_integer"


# ------------------------------------------------------------------ M2: channel router (custom profiles)

def _router_profile(**opts):
    from app.audio.schema import RoutingSpec
    spec = {"name": "Test 3.0", "layout": "3.0",
            "matrix": [{"NEURO_LEFT": 1.0}, {"NEURO_RIGHT": 1.0}, {"VOICE_CENTER": 0.5}],
            "options": [{"delay_ms": 0}, {"invert": True}, {"delay_ms": opts.get("delay_ms", 1.0)}]}
    spec.update(opts.get("override", {}))
    return RoutingSpec.model_validate(spec).as_profile() | {"custom": True}


def test_router_delay_polarity_gain_and_block_boundaries(tmp_path):
    import app.audio.render as R
    ints = np.random.default_rng(11).integers(-(1 << 21), 1 << 21, size=3 * R.BLOCK + 777)
    x = ints / float(1 << 23)
    a = src_from_array(tmp_path, "a", x)
    n = len(ints)
    st = project([{"id": f"t{i}", "bus": b, "clips": [{"id": f"c{i}", "asset_id": "a", "start_frame": 0,
                                                       "duration_frames": n}]}
                  for i, b in enumerate(["NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER"])])
    prof = _router_profile(delay_ms=1.0)                  # 1 ms at 48 kHz = 48 frames on FC
    res = render(st, fs=FS, bits=24, profile_key="custom", sources={"a": a}, out_path=str(tmp_path / "r.wav"),
                 profile=prof)
    assert res.frames == n + 48                          # a full export keeps the delayed tail
    pcm, info = read_pcm(res.path)
    assert info["channel_layout"] == "3.0" and verify(res.path, res)["passed"]
    assert np.array_equal(pcm[:n, 0], ints) and not pcm[n:, 0].any()          # FL untouched
    assert np.array_equal(pcm[:n, 1], -ints)                                   # FR polarity flipped, bit-exact
    fc = np.rint(x * 0.5 * (1 << 23)).astype(np.int64)
    assert not pcm[:48, 2].any() and np.array_equal(pcm[48:, 2], fc)          # FC: -6 dB, 48 frames late
    ro = res.report["routing"]["channel_options"]
    assert ro[1]["invert"] and ro[2]["delay_frames"] == 48 and res.report["routing"]["custom"]


def test_router_preview_window_equals_export_slice_with_delay(tmp_path):
    """A delayed channel must be right at any window start: the window is rendered with a pre-roll."""
    s = src_from_array(tmp_path, "a", tone(440, 2 * FS))
    st = project([{"id": "t1", "bus": "VOICE_CENTER", "clips": [{"id": "c1", "asset_id": "a", "start_frame": 0,
                                                                  "duration_frames": 2 * FS}]}])
    prof = _router_profile(delay_ms=12.5)
    full = render(st, fs=FS, bits=24, profile_key="custom", sources={"a": s}, out_path=str(tmp_path / "f.wav"), profile=prof)
    win = render(st, fs=FS, bits=24, profile_key="custom", sources={"a": s}, out_path=str(tmp_path / "w.wav"),
                 profile=prof, start=FS // 3, frames=FS // 2)
    a, _ = read_pcm(full.path)
    b, _ = read_pcm(win.path)
    assert np.array_equal(b, a[FS // 3: FS // 3 + FS // 2])


def test_router_mute_and_validation(tmp_path):
    from app.audio.schema import RoutingSpec
    s = src_from_array(tmp_path, "a", tone(500, FS // 4))
    st = project([{"id": "t1", "bus": "NEURO_LEFT", "clips": [{"id": "c1", "asset_id": "a", "start_frame": 0,
                                                                "duration_frames": FS // 4}]}])
    prof = _router_profile(override={"options": [{"mute": True}, {}, {}]})
    res = render(st, fs=FS, bits=16, profile_key="custom", sources={"a": s}, out_path=str(tmp_path / "m.wav"), profile=prof)
    assert [c["silent"] for c in res.report["channels"]] == [True, True, True]
    for bad in ({"name": "x", "layout": "5.1(side)", "matrix": [{}] * 5},                  # wrong row count
                {"name": "x", "layout": "stereo", "matrix": [{"NOPE": 1.0}, {}]},           # unknown bus
                {"name": "x", "layout": "stereo", "matrix": [{"LFE": 5.0}, {}]},            # above +12 dB
                {"name": "x", "layout": "stereo", "matrix": [{}, {}], "options": [{"delay_ms": 500}, {}]}):
        with pytest.raises(Exception):
            RoutingSpec.model_validate(bad)


def test_router_api_versions_and_access(tmp_path):
    dev = Client().register(developer=True)
    spec = {"name": "Кресло, тыл с задержкой", "layout": "5.1(side)",
            "matrix": [{"MUSIC_LEFT": 1.0}, {"MUSIC_RIGHT": 1.0}, {"VOICE_CENTER": 1.0}, {"LFE": 1.0},
                       {"NEURO_LEFT": 1.0}, {"NEURO_RIGHT": 1.0}],
            "options": [{}, {}, {}, {}, {"delay_ms": 2.0}, {"delay_ms": 2.0}]}
    bad = dev.post("/audio/routing-profiles", {**spec, "matrix": spec["matrix"][:3]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_profile"
    p1 = ok(dev.post("/audio/routing-profiles", spec), 201)
    assert p1["key"].startswith("u-") and p1["version"] == 1 and p1["channels"] == ["FL", "FR", "FC", "LFE", "SL", "SR"]
    keys = [x["key"] for x in ok(dev.get("/audio/routing-profiles"))["items"]]
    assert p1["key"] in keys and "NAP_CHAIR_5_1" in keys
    assert dev.put("/audio/routing-profiles/NAP_CHAIR_5_1", spec).status_code == 409   # built-ins are read-only

    pr = ok(dev.post("/audio/projects", {"title": "Router", "bus_count": 6}), 201)
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {"carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -18}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": FS // 2}]
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    j1 = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": p1["key"]}), 201)["id"])
    assert j1["state"] == "completed" and j1["profile_version"] == 1, (j1["error_code"], j1["error_detail"])
    assert j1["artifact"]["frames"] == FS // 2 + 96 and j1["artifact"]["report"]["routing"]["custom"]

    p2 = ok(dev.put(f"/audio/routing-profiles/{p1['key']}", {**spec, "options": [{}] * 6}))
    assert p2["version"] == 2
    j2 = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": p1["key"]}), 201)["id"])
    assert j2["profile_version"] == 2 and j2["artifact"]["frames"] == FS // 2
    assert ok(dev.get(f"/audio/render-jobs/{j1['id']}"))["artifact"]["frames"] == FS // 2 + 96   # old render unchanged

    prev = dev.c.post(f"/api/v1/audio/projects/{pr['id']}/preview", json={"start_frame": 0, "frames": 1000,
                      "profile": p1["key"]}, headers=dev._h())
    assert prev.status_code == 200

    other = Client().register(developer=True)
    assert p1["key"] not in [x["key"] for x in ok(other.get("/audio/routing-profiles"))["items"]]
    assert other.put(f"/audio/routing-profiles/{p1['key']}", spec).status_code == 422
    assert other.post(f"/audio/projects/{pr['id']}/renders", {"profile": p1["key"]}).status_code == 404

    assert dev.delete(f"/audio/routing-profiles/{p1['key']}").status_code == 204
    assert p1["key"] not in [x["key"] for x in ok(dev.get("/audio/routing-profiles"))["items"]]
    assert dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": p1["key"]}).status_code == 422
    assert ok(dev.get(f"/audio/render-jobs/{j1['id']}"))["state"] == "completed"   # history survives the archive


def test_carrier_ramp_saved_through_api_is_rendered(tmp_path):
    """Carrier automation from the inspector: 200 → 300 Hz over 1 s, beat stays 8 Hz, phase-continuous."""
    dev = Client().register(developer=True)
    pr = ok(dev.post("/audio/projects", {"title": "Ramp", "bus_count": 2}), 201)
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {
        "carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -12,
        "carrier_automation": [{"frame": 0, "value": 200}, {"frame": FS, "value": 300}]}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": 3 * FS}]
    st["tracks"][1]["clips"] = [{"id": "c2", "generator_id": "g1", "generator_output": "right", "start_frame": 0, "duration_frames": 3 * FS}]
    bad = {**st, "generators": [{**st["generators"][0], "params": {**st["generators"][0]["params"],
           "carrier_automation": [{"frame": FS, "value": 200}, {"frame": 0, "value": 300}]}}]}
    assert dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": bad}).status_code == 422
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    job = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": "HEADPHONES_2_0"}), 201)["id"])
    assert job["state"] == "completed"
    body = dev.c.get(ok(dev.get(f"/audio/render-jobs/{job['id']}/download"))["url"]).content
    x, _ = read_pcm_bytes(tmp_path, body)
    x = x / float(1 << 23)
    fl, _ = measure_tone(x[int(1.5 * FS):, 0], FS, 296)       # after the ramp: 300 - 4
    fr, _ = measure_tone(x[int(1.5 * FS):, 1], FS, 304)
    assert abs(fl - 296) < 0.01 and abs(fr - 304) < 0.01
    amp = 10 ** (-12 / 20)
    assert np.max(np.abs(np.diff(x[:, 0]))) <= 2 * np.pi * 305 * amp / FS * 1.02   # no clicks across the ramp


def test_height_layouts_for_own_profiles(tmp_path):
    """NAP-101: an author's own profile can target 5.1.2 / 7.1.4 (height speakers); the export carries the
    height bits in the WAVE channel mask; FLAC (at most 8 channels) is refused for 7.1.4 with a clear code."""
    dev = Client().register(developer=True)
    rows = [{"MUSIC_LEFT": 1.0}, {"MUSIC_RIGHT": 1.0}, {"VOICE_CENTER": 1.0}, {"LFE": 1.0}, {"BACK_LEFT": 1.0},
            {"BACK_RIGHT": 1.0}, {"NEURO_LEFT": 1.0}, {"NEURO_RIGHT": 1.0},
            {"MUSIC_LEFT": 0.5}, {"MUSIC_RIGHT": 0.5}, {"BACK_LEFT": 0.5}, {"BACK_RIGHT": 0.5}]
    p = ok(dev.post("/audio/routing-profiles", {"name": "Комната 7.1.4", "layout": "7.1.4", "matrix": rows}), 201)
    assert p["channels"][-4:] == ["TFL", "TFR", "TBL", "TBR"]
    assert dev.post("/audio/routing-profiles", {"name": "x", "layout": "5.1.2", "matrix": rows[:6]}).status_code == 422
    pr = ok(dev.post("/audio/projects", {"title": "Heights", "bus_count": 8}), 201)
    st = pr["state"]
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {"carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -18}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": FS // 2}]
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    r = dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": p["key"], "format": "flac"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "flac_max_channels"
    j = _wait(dev, ok(dev.post(f"/audio/projects/{pr['id']}/renders", {"profile": p["key"]}), 201)["id"])
    assert j["state"] == "completed", (j["error_code"], j["error_detail"])
    x, info = read_pcm_bytes(tmp_path, dev.raw().get(ok(dev.get(f"/audio/render-jobs/{j['id']}/download"))["url"]).content)
    assert x.shape[1] == 12
    with open(tmp_path / "h.wav", "wb") as fh:
        fh.write(dev.raw().get(ok(dev.get(f"/audio/render-jobs/{j['id']}/download"))["url"]).content)
    pr_ = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp_path / "h.wav")], capture_output=True, text=True)
    assert json.loads(pr_.stdout)["streams"][0]["channel_layout"] == "7.1.4"
