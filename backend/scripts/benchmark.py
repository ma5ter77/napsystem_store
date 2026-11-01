"""Real conversion benchmark: synthesizes inputs, runs the production pipeline, records wall time, CPU time and
peak memory (process + ffmpeg children). Usage (from backend/): uv run python scripts/benchmark.py 10 60 120 [--mp3]"""
import json, os, platform, resource, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run without PYTHONPATH
import numpy as np
from app.dsp.engine import ALPHA_DEMO_V1
from app.dsp.pipeline import Limits, _wav_header, convert


def synth(path, minutes, fs=44100):
    n = int(minutes * 60 * fs)
    with open(path, "wb") as fh:
        fh.write(_wav_header(n, 2, 16, fs))
        rng = np.random.default_rng(1)
        step = fs * 60
        for i in range(0, n, step):
            k = min(step, n - i)
            t = (np.arange(k) + i) / fs
            s = 0.2 * np.sin(2 * np.pi * 180 * t) + 0.05 * rng.standard_normal(k)
            fh.write(np.clip(np.rint(np.stack([s, s * 0.9], 1) * 32767), -32768, 32767).astype("<i2").tobytes())


def run(minutes, mp3=False):
    d = tempfile.mkdtemp(dir=os.environ.get("BENCH_DIR", "/tmp"))
    src = os.path.join(d, "in.wav")
    synth(src, minutes)
    if mp3:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-b:a", "192k", src + ".mp3"], check=True)
        os.remove(src)
        src = src + ".mp3"
    size = os.path.getsize(src)
    # the conversion runs in a fresh process so peak memory reflects only the pipeline (+ its ffmpeg children)
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, __file__, "--child", src, os.path.join(d, "w")], capture_output=True, text=True,
                       check=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    wall = time.perf_counter() - t0
    child = json.loads(r.stdout.strip().splitlines()[-1])
    res = {"input_minutes": minutes, "input_format": "mp3 192k" if mp3 else "wav 16-bit 44.1 kHz stereo",
           "input_bytes": size, "wall_seconds": round(wall, 1), "realtime_factor": round(minutes * 60 / wall, 1), **child}
    subprocess.run(["rm", "-rf", d])
    return res


def child(src, workdir):
    out = convert(src, workdir, ALPHA_DEMO_V1.to_dict(), Limits(max_bytes=2 << 30, max_seconds=7200))
    me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    print(json.dumps({"cpu_seconds_python": round(me.ru_utime + me.ru_stime, 1),
                      "cpu_seconds_ffmpeg": round(kids.ru_utime + kids.ru_stime, 1),
                      "peak_rss_pipeline_mb": round(me.ru_maxrss / 1024, 1),
                      "peak_rss_ffmpeg_mb": round(kids.ru_maxrss / 1024, 1),
                      "true_peak_master_dbtp": out.report["levels"]["true_peak_master_dbtp"],
                      "beat_hz": out.report["layer"]["beat_hz"], "master_bytes": os.path.getsize(out.master),
                      "delivery_flac_bytes": os.path.getsize(out.delivery)}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child(sys.argv[2], sys.argv[3])
        sys.exit(0)
    mp3 = "--mp3" in sys.argv
    mins = [float(x) for x in sys.argv[1:] if not x.startswith("--")] or [10]
    env = {"cpu": platform.processor() or platform.machine(), "cpus": os.cpu_count(),
           "ram_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1),
           "python": platform.python_version()}
    try:
        env["cpu_model"] = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:
        pass
    print(json.dumps({"environment": env, "runs": [run(m, mp3) for m in mins]}, indent=2))
