"""Disposable GitHub-hosted runner ONLY. Never invoke on NAP VPS or VM."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'cohost4-report'
SLICE = 'napstore4probe.slice'
CG = Path('/sys/fs/cgroup') / SLICE
PROFILE = json.loads(Path(__file__).with_name('profile.json').read_text())


def run(args, timeout=300, capture=True):
    r = subprocess.run(args, cwd=ROOT, capture_output=capture, text=True, timeout=timeout)
    if r.returncode:
        # Do not dump config/environment/DB URLs on failure.
        raise RuntimeError(f'Command failed ({r.returncode}): {args[:3]}')
    return r.stdout if capture else ''


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.environ.get('GITHUB_REPOSITORY') == 'BrothersB/nap-store'
    assert not Path('/var/www/nap-trainer').exists()
    assert not Path('/srv/nap-store').exists()
    assert not CG.exists(), 'Never reuse a test cgroup'
    REPORT.mkdir(exist_ok=False)
    project = 'cohost4-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    # Builds occur outside the runtime budget, as future production deployment must use CI-built images.
    run(['docker','compose','build','api','web'],timeout=1200,capture=False)
    base=json.loads(run(['docker','compose','config','--format','json']))
    config={'name':project,'services':{},'networks':base['networks'],'volumes':base['volumes']}
    for section in ['networks','volumes']:
        for item in config[section].values():
            item.pop('name',None); item.pop('external',None)
    for name, limits in PROFILE['services'].items():
        svc=base['services'][name]
        if 'build' in svc:
            svc['image']='nap-store-web' if name=='web' else 'nap-store-api'
        for key in ['build','ports','profiles','deploy','depends_on','container_name','restart']:
            svc.pop(key,None)
        svc.update(mem_limit=f"{limits['memory_mib']}m",memswap_limit=f"{limits['memory_mib']}m",
                   cpus=limits['cpus'],pids_limit=128,cgroup_parent=SLICE,
                   logging={'driver':'json-file','options':{'max-size':'5m','max-file':'2'}})
        env=svc.setdefault('environment',{})
        env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
                   NAP_API_WORKERS='1',NAP_RUN_JOBS_INLINE='false',
                   NAP_MAX_UPLOAD_BYTES=str(PROFILE['max_upload_bytes']),
                   NAP_MAX_DURATION_SECONDS=str(PROFILE['max_duration_seconds']))
        if name=='web':
            svc['ports']=[{'target':3000,'published':'3100','host_ip':'127.0.0.1','protocol':'tcp'}]
            env['NODE_OPTIONS']='--max-old-space-size=160'
        if name=='worker-media': svc['command']=['celery','-A','app.worker.celery','worker','-Q','media','--concurrency','1','--loglevel','WARNING']
        if name=='worker-ops': svc['command']=['celery','-A','app.worker.celery','worker','-B','-Q','ops','--concurrency','1','--loglevel','WARNING']
        if name=='postgres': svc['command']=['postgres','-c','shared_buffers=64MB','-c','work_mem=2MB','-c','max_connections=80']
        if name=='redis': svc['command']=['redis-server','--maxmemory','32mb','--maxmemory-policy','noeviction']
        if name=='backup':
            # Start the scheduler; don't repeat or shorten retention on any existing data.
            env['BACKUP_AT']='23:59'
        config['services'][name]=svc
    path=REPORT/'compose.private.json'
    path.write_text(json.dumps(config));path.chmod(0o600)
    compose=['docker','compose','-p',project,'-f',str(path)]
    unit='[Slice]\nMemoryAccounting=yes\nCPUAccounting=yes\nMemoryHigh=1536M\nMemoryMax=1792M\nMemorySwapMax=0\nCPUQuota=75%\nTasksMax=768\n'
    subprocess.run(['sudo','tee','/run/systemd/system/'+SLICE],input=unit,text=True,stdout=subprocess.DEVNULL,check=True)
    run(['sudo','systemctl','daemon-reload']);run(['sudo','systemctl','start',SLICE])
    samples=[]
    stop=False
    def observe():
        while not stop:
            try:
                samples.append({'t':time.time(),'memory':int((CG/'memory.current').read_text()),
                                'events':(CG/'memory.events').read_text(),'cpu':(CG/'cpu.stat').read_text()})
            except FileNotFoundError: pass
            time.sleep(1)
    from threading import Thread
    monitor=Thread(target=observe); monitor.start()
    result={'status':'FAIL','scope':'isolated runtime caps; NOT production/no-interference acceptance'}
    try:
        assert int((CG/'memory.max').read_text())==1792*1024**2
        quota,period=map(int,(CG/'cpu.max').read_text().split());assert quota/period==0.75
        run(compose+['up','-d','--wait','--wait-timeout','90','postgres','redis'])
        # Seed invokes the real audio converter on synthetic inputs, within this same parent budget.
        run(compose+['run','--rm','migrate'],timeout=600)
        run(compose+['up','-d','--wait','--wait-timeout','180','api','web','worker-media','worker-ops','backup'],timeout=240)
        ids=run(compose+['ps','-q']).split()
        inspected=json.loads(run(['docker','inspect',*ids]))
        for c in inspected:
            pid=c['State']['Pid']
            assert SLICE in Path(f'/proc/{pid}/cgroup').read_text()
            assert c['HostConfig']['Memory']>0 and c['HostConfig']['NanoCpus']>0
        timings=[]
        def get(i):
            path=['/ru','/en','/api/v1/ready','/api/v1/catalog/apps'][i%4]
            t=time.monotonic()
            with urllib.request.urlopen('http://127.0.0.1:3100'+path,timeout=10) as r:
                assert r.status==200; r.read(2*1024*1024)
            return time.monotonic()-t
        # Bounded 60 requests / 2 clients, not 50/45 or a production capacity claim.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for batch in range(15):
                timings.extend(pool.map(get,range(batch*4,batch*4+4)));time.sleep(2)
        result.update(status='PASS_SCOPED_RUNTIME_CAPS',requests=len(timings),max_request_seconds=max(timings),
                      limitations=['No rootless daemon budget validation', 'No simultaneous media conversion and browsing',
                                   'No maximum-size media, backup cycle, disk/network caps or platform load acceptance'])
        events=dict(line.split() for line in (CG/'memory.events').read_text().splitlines())
        assert int(events.get('oom',0))==0 and int(events.get('oom_kill',0))==0
        assert all(not c['State']['OOMKilled'] for c in json.loads(run(['docker','inspect',*ids])))
    except Exception as e:
        result['status']='FAIL';result['error']=str(e)
        raise
    finally:
        stop=True;monitor.join(timeout=3)
        result['peak_sampled_memory_mib']=round(max((s['memory'] for s in samples),default=0)/1024**2,2)
        (REPORT/'samples.json').write_text(json.dumps(samples))
        try:
            run(compose+['down','--volumes','--remove-orphans'],timeout=120)
            remaining=run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project]).strip()
            result['cleanup_empty']=not remaining
            assert not remaining
        except Exception as e:
            result['status']='FAIL_CLEANUP'
            result['cleanup_error']=str(e)
            raise
        finally:
            (REPORT/'result.json').write_text(json.dumps(result,indent=2))
            print(json.dumps(result,indent=2))
            run(['sudo','systemctl','stop',SLICE])


if __name__=='__main__': main()
