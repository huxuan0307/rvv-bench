#!/usr/bin/env python3
"""Package verified bare-metal images and provenance without claiming RTL completion."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

HERE=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build=a.build.resolve();manifest=json.loads((build/'manifest.json').read_text())
    if manifest['build_status']!='built':raise ValueError('build incomplete')
    files={};checks=[]
    def add(path,name):
        if name in files:raise ValueError('duplicate archive path '+name)
        files[name]=path
    add(build/'manifest.json','build/manifest.json')
    for case in manifest['cases']:
        for key,hashkey in [('bin','sha256'),('elf','elf_sha256')]:
            path=build/case[key]
            if sha(path)!=case[hashkey]:raise ValueError('artifact hash mismatch '+str(path))
            add(path,'build/'+case[key])
        for name in ('disassembly.stdout','elf.stdout','link.map','link.stdout'):
            path=build/case['name']/name
            if path.is_file():add(path,'build/'+case['name']+'/'+name)
    for path in (build/'source').rglob('*'):
        if path.is_file():add(path,'build/'+str(path.relative_to(build)))
    for path in (build/'data').iterdir():
        if path.is_file():add(path,'build/data/'+path.name)
    for path in HERE.iterdir():
        if path.is_file() and path.suffix in ('.py','.md','.c','.h'):add(path,'current-suite/'+path.name)
    for name in ('image-source-audit.json','host-tests-launch.log','commands.json'):
        path=build/name
        if path.is_file():add(path,'build/'+name)
    for gate in ('nemu-full','gsim-abi'):
        for name in ('run-metadata.json','runner-state.json','reports/results.json','reports/summary.md','reports/samples.csv'):
            path=build/gate/name
            if path.is_file():add(path,'build/'+gate+'/'+name)
    note={'scope':'BIN+ELF+构建证据与已完成门禁；性能campaign仍在运行，不是最终性能报告',
        'original_build':str(build),'manifest_sha256':sha(build/'manifest.json'),
        'path_note':'BIN/ELF可复制运行；历史manifest/日志保留原主机绝对路径，不宣称解压后历史runner可直接重放。请用current-suite/build.py建立新的可复现构建。',
        'files':{name:sha(path) for name,path in sorted(files.items())}}
    with zipfile.ZipFile(a.output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,path in sorted(files.items()):z.write(path,name)
        z.writestr('PACKAGE.json',json.dumps(note,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'archive':str(a.output.resolve()),'sha256':sha(a.output),'files':len(files)+1,'bytes':a.output.stat().st_size}))
if __name__=='__main__':main()
