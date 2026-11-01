"""Read-only program sent to the existing VPS over SSH stdin; no files written there."""
import json
from pathlib import Path
import subprocess

days = []
for path in sorted(Path('/var/log/sysstat').glob('sa[0-9][0-9]')):
    host = json.loads(subprocess.check_output(['sadf', '-j', str(path), '--', '-r', '-u', '-q']))['sysstat']['hosts'][0]
    rows = host['statistics']
    rows = [r for r in rows if 'memory' in r and 'cpu-load' in r]
    if not rows:
        continue
    days.append({'date': host['file-date'], 'samples':len(rows),
                 'min_available_mib': round(min(r['memory']['avail'] for r in rows)/1024, 2),
                 'max_interval_cpu_busy_pct':round(max(100-r['cpu-load'][0]['idle'] for r in rows),2),
                 'max_interval_iowait_pct':max(r['cpu-load'][0]['iowait'] for r in rows),
                 'max_load1':max(r['queue']['ldavg-1'] for r in rows),
                 'max_interval_seconds':max(r['timestamp']['interval'] for r in rows)})
print(json.dumps({'scope':'sampled history, not worst-case acceptance','days':days},indent=2))
