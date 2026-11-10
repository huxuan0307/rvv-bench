#!/usr/bin/env python3
"""Run gated RTL stages after explicit NEMU + public ABI acceptance.

No automatic retry, library change, diff disable or static fallback. Each stage
is audited before the next stage; raw evidence and failed stages are retained.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import collect
import runner_common as common

HERE=Path(__file__).resolve().parent
GSIM=Path('/nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572')

def require_gate(path,mode,manifest_sha,wanted):
    doc=collect.collect(path)
    if doc['state']!='passed' or doc['mode']!=mode or doc['manifest']['snapshot_sha256']!=manifest_sha:
        raise ValueError(f'{mode} gate not passed or mismatched manifest: {path}')
    names={c['name'] for c in doc['cases']}
    if not wanted<=names: raise ValueError(f'gate lacks {wanted-names}')
    return doc

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--nemu-run',type=Path,required=True)
    p.add_argument('--abi-run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); manifest=common.load_manifest(a.manifest); ms=manifest['sha256']
    funcs=['log','exp','sin','cos','pow','logf','expf']
    want={'abi-public'}|{'perf-'+f for f in funcs}
    require_gate(a.nemu_run,'nemu',ms,want)
    abi=require_gate(a.abi_run,'gsim',ms,{'abi-public'})
    if Path(abi['tools']['gsim']['realpath'])!=GSIM or 'static' in GSIM.name:
        raise ValueError('ABI gate not run with pinned non-static gsim')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    frozen=out/'runner-source';frozen.mkdir()
    source_identity={}
    for name in ('run.py','runner_common.py','collect.py','compact_csv.py','analyze.py','campaign.py'):
        dest=frozen/name;dest.write_bytes((HERE/name).read_bytes());source_identity[name]=common.identity(dest)
    state=dict(schema_version=1,started_at=common.utc_now(),state='running',manifest=manifest['path'],
        runner_sources=source_identity,
        manifest_sha256=ms,nemu_run=str(a.nemu_run.resolve()),abi_run=str(a.abi_run.resolve()),
        tools=abi['tools'],stages=[],policy='serial log+exp pilot, then other functions <=4 jobs, then independent log+exp replay')
    def save():common.write_json(out/'campaign.json',state)
    save();reports=[Path(a.abi_run).resolve()/'reports/results.json']
    try:
        stages=[('pilot',['log','exp'],1),('remaining',['sin','cos','pow','logf','expf'],4),('replay',['log','exp'],2)]
        for stage,functions,jobs in stages:
            run=out/stage
            # Freeze exact tools accepted by the ABI gate for all stages.
            for kind in ('gsim','reference','dram_config'):
                saved=state['tools'][kind]
                if common.sha256(saved['realpath'])!=saved['sha256']:
                    raise ValueError('tool/config changed during campaign: '+kind)
            cmd=[sys.executable,'-B',str(frozen/'run.py'),'gsim','--manifest',str(a.manifest.resolve()),
                '--output',str(run),'--emu',str(GSIM),'--reference',state['tools']['reference']['realpath'],
                '--dramsim3-ini',state['tools']['dram_config']['realpath'],'--jobs',str(jobs),'--timeout','48h']
            for f in functions:cmd+=['--case','perf-'+f]
            entry=dict(stage=stage,command=cmd,state='running',started_at=common.utc_now(),output=str(run))
            state['stages'].append(entry);save();print('STAGE_START='+stage,flush=True)
            with (out/(stage+'.stdout')).open('w') as so,(out/(stage+'.stderr')).open('w') as se:
                result=subprocess.run(cmd,stdout=so,stderr=se)
            entry['returncode']=result.returncode;entry['finished_at']=common.utc_now()
            audit_cmd=[sys.executable,'-B',str(frozen/'collect.py'),'--run',str(run),'--require-complete']
            with (out/(stage+'.audit.log')).open('w') as audit_log:
                audit_result=subprocess.run(audit_cmd,stdout=audit_log,stderr=subprocess.STDOUT)
            doc=json.loads((run/'reports/results.json').read_text());entry['audit_state']=doc['state']
            entry['audit_returncode']=audit_result.returncode
            entry['state']='passed' if result.returncode==0 and audit_result.returncode==0 and doc['state']=='passed' else 'failed';save()
            if entry['state']!='passed':raise RuntimeError('stage did not pass: '+stage)
            reports.append(run/'reports/results.json');print('STAGE_PASS='+stage,flush=True)
        cmd=[sys.executable,'-B',str(frozen/'analyze.py'),'--output',str(out/'analysis'),'--require-full']
        for report in reports:cmd+=['--report',str(report)]
        result=subprocess.run(cmd);state['analysis_command']=cmd
        if result.returncode:raise RuntimeError('full analysis incomplete')
        state['state']='passed';state['finished_at']=common.utc_now();save();return 0
    except BaseException as exc:
        state['state']='failed';state['error']=repr(exc);state['finished_at']=common.utc_now();save();raise
if __name__=='__main__':raise SystemExit(main())
