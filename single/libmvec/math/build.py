#!/usr/bin/env python3
"""仅构建本快照静态库并归档反汇编/属性；不运行仿真。"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

HERE=Path(__file__).resolve().parent
PREFIX=Path('/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec')
BASE=['-O3','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany',
      '-fno-pie','-fno-stack-protector','-fno-lto','-mrvv-vector-bits=scalable',
      '-fno-fast-math','-fno-tree-vectorize','-fno-tree-slp-vectorize']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--exp-only',action='store_true');a=ap.parse_args()
    out=HERE/'build';out.mkdir(exist_ok=True)
    commands=[]
    def run(cmd,label):
        p=subprocess.run([str(x) for x in cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        (out/(label+'.stdout')).write_bytes(p.stdout)
        (out/(label+'.stderr')).write_bytes(p.stderr)
        commands.append(dict(label=label,command=[str(x) for x in cmd],returncode=p.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if p.returncode: raise RuntimeError(label+' failed; see '+str(out/(label+'.stderr')))
        return p.stdout.decode()
    cc=PREFIX/'bin/riscv64-unknown-linux-gnu-gcc'
    stem=['expvec','expvec_data'] if a.exp_only else ['expvec','expvec_data','expfvec','expfvec_data','logfvec','logfvec_data','powvec','sinvec','cosvec','trigvec_data','logvec','logvec_data']
    objects=[]
    for name in stem:
        obj=out/(name+'.o');objects.append(obj)
        contract='off' if name in ('sinvec','cosvec') else 'fast'
        run([cc,*BASE,'-ffp-contract='+contract,'-c',HERE/(name+'.c'),'-o',obj],name+'.compile')
        run([PREFIX/'bin/riscv64-unknown-linux-gnu-objdump','-dr',obj],name+'.disassembly')
        run([PREFIX/'bin/riscv64-unknown-linux-gnu-readelf','-A','-sW',obj],name+'.attributes')
    # 通过临时名字创建新archive，防止ar保留旧成员。
    archive=out/('libnewmath-exp.a' if a.exp_only else 'libnewmath.a')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='archive-',dir=out) as td:
        temporary=Path(td)/archive.name
        run([PREFIX/'bin/riscv64-unknown-linux-gnu-ar','rcs',temporary,*objects],'archive')
        archive.write_bytes(temporary.read_bytes())
    syms=run([PREFIX/'bin/riscv64-unknown-linux-gnu-nm','-g',archive],'archive.symbols')
    functions=['exp'] if a.exp_only else ['log','exp','expf','logf','pow','sin','cos']
    expected=[f'_ZGV{lm}Nx'+('vv' if f=='pow' else 'v')+'_'+f for f in functions for lm in (1,2)]
    for sym in expected:
        if not any(line.endswith(' T '+sym) for line in syms.splitlines()): raise RuntimeError('missing symbol '+sym)
    manifest=dict(archive=str(archive),sha256=sha(archive),compile_flags=BASE,
                  functions=functions,nx_symbols=expected,
                  objects=[dict(path=str(p),sha256=sha(p)) for p in objects],
                  sources={str(p.relative_to(HERE)):sha(p) for p in HERE.glob('*') if p.is_file()})
    (out/('manifest-exp.json' if a.exp_only else 'manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
    print('ARCHIVE='+str(archive)); print('SHA256='+sha(archive))
if __name__=='__main__':main()
