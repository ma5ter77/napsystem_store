"""NAP Audio Studio M1 (spec v2.1): acceptance tests T01–T09 with real ffmpeg decoding and independent readers."""
import hashlib
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
