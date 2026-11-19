#!/usr/bin/env python3
"""在日期运行目录中预检短 trace：逐镜像暂存、核验、NEMU、独立日志。"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--nemu',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--max-instr',type=int,default=50000000)
    ap.add_argument('--timeout',type=int,default=120)
    a=ap.parse_args()
    if a.max_instr<1 or a.timeout<1: ap.error('max-instr 和 timeout 必须为正')
    manifest=a.manifest.resolve(strict=True); nemu=a.nemu.resolve(strict=True); out=a.output.resolve()
    if not nemu.is_file() or not os.access(nemu,os.X_OK): ap.error('NEMU 不存在或不可执行')
    if any(p.lower()=='build' or p.lower().startswith('build-') for p in out.parts): ap.error('运行目录不能位于 build')
    if not re.search(r'\d{8}[-T]\d{6}',out.name): ap.error('运行目录名须含 YYYYMMDD-HHMMSS')
    doc=json.loads(manifest.read_text()); images=doc.get('images',[])
    if not images: ap.error('没有 trace 镜像')
    for item in images:
        if sha(item['bin'])!=item['sha256'] or sha(item['elf'])!=item['elf_sha256']: ap.error('构建镜像 SHA 不匹配')
    out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(manifest,out/'trace-manifest.json')
    nemu_hash=sha(nemu)
    rows=[]
    with (out/'results.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['mode','bin_sha256','staged_before','staged_after','nemu_sha256','rc','timeout','trace_result','good_trap','status'])
        writer.writeheader()
        for item in images:
            mode=item['mode']; case=out/mode; case.mkdir(); work=case/'work'; work.mkdir()
            staged=case/(mode+'.bin'); shutil.copyfile(item['bin'],staged)
            before=sha(staged)
            if before!=item['sha256'] or sha(item['bin'])!=before: raise RuntimeError('复制前后 BIN SHA 不匹配')
            cmd=[str(nemu),'-b',f'--max-instr={a.max_instr}',str(staged)]
            (case/'command.txt').write_text('cwd='+shlex.quote(str(work))+'\n'+shlex.join(cmd)+'\n')
            env=os.environ.copy(); env.update(TMPDIR=str(work),TMP=str(work),TEMP=str(work))
            timed_out=False
            with (case/'stdout.log').open('w') as stdout,(case/'stderr.log').open('w') as stderr:
                try: rc=subprocess.run(cmd,cwd=work,env=env,stdout=stdout,stderr=stderr,timeout=a.timeout).returncode
                except subprocess.TimeoutExpired: timed_out=True; rc=None
            after=sha(staged)
            text=(case/'stdout.log').read_text(errors='replace')+'\n'+(case/'stderr.log').read_text(errors='replace')
            clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
            trace=re.findall(r'TRACE_RESULT n=16 k=(\d+) begin=(\d+) end=(\d+) cycles=(\d+) failed=(\d+)',clean)
            trap=bool(re.search(r'\bHIT\s+GOOD\s+TRAP\b',clean,re.I))
            bad=bool(re.search(r'HIT\s+BAD\s+TRAP|HIT\s+CRITICAL\s+ERROR|LIBMVEC_ERROR',clean,re.I))
            ok=(rc==0 and not timed_out and before==after==item['sha256'] and sha(item['bin'])==before and
                sha(nemu)==nemu_hash and trap and not bad and len(trace)==1 and
                int(trace[0][0])==doc['k'] and trace[0][4]=='0')
            row=dict(mode=mode,bin_sha256=item['sha256'],staged_before=before,staged_after=after,
                     nemu_sha256=nemu_hash,rc=rc,timeout=timed_out,trace_result=' '.join(trace[0]) if len(trace)==1 else '',
                     good_trap=trap,status='passed' if ok else 'failed')
            rows.append(row); writer.writerow(row); f.flush()
            print(mode,row['status'],'rc='+str(rc),'good_trap='+str(trap),flush=True)
            if not ok: return 1
    return 0


if __name__=='__main__': sys.exit(main())
