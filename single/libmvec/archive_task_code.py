#!/usr/bin/env python3
"""将任务树中的代码与嵌套Git元数据移入树外归档，逐文件校验并生成清单。"""
import argparse
import hashlib
import json
from pathlib import Path

SUFFIXES = {'.c', '.h', '.cc', '.cpp', '.cxx', '.C', '.hpp', '.hh', '.f', '.f90',
            '.f95', '.F', '.F90', '.py', '.pyc', '.sh', '.bash', '.zsh', '.S', '.s',
            '.ll', '.bc', '.ld', '.lds', '.scala', '.td', '.def', '.opt', '.mk',
            '.ac', '.in', '.patch', '.diff', '.cmd', '.js', '.ts', '.tsx', '.jsx',
            '.rs', '.go', '.java', '.cmake', '.sample', '.c-static', '.c-shared'}
NAMES = {'Makefile', 'GNUmakefile', 'CMakeLists.txt', 'configure', 'configure.ac',
         'meson.build', 'meson_options.txt', 'build.ninja'}

def is_code(path):
    if path.suffix in SUFFIXES or path.name in NAMES or '.git' in path.parts:
        return True
    if path.is_symlink():
        return False
    if path.is_file():
        with path.open('rb') as f:
            return f.read(2) == b'#!'
    return False

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tasks', type=Path, required=True)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    root = args.tasks.resolve(strict=True)
    archive = args.archive.resolve()
    assert not archive.is_relative_to(root) and not root.is_relative_to(archive)
    files = [p for p in root.rglob('*') if (p.is_file() or p.is_symlink()) and is_code(p)]
    records = []
    for path in sorted(files):
        rel = path.relative_to(root)
        target = archive / rel
        assert not target.exists() and not target.is_symlink(), target
        records.append(dict(path=str(rel), bytes=path.lstat().st_size,
                            symlink=str(path.readlink()) if path.is_symlink() else None,
                            sha256=None if path.is_symlink() else sha(path)))
    if args.apply:
        for record in records:
            source = root / record['path']
            target = archive / record['path']
            assert source.parent.resolve().is_relative_to(root)
            assert target.parent.resolve().is_relative_to(archive) or not target.parent.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            assert (str(target.readlink()) == record['symlink'] if record['symlink'] is not None
                    else sha(target) == record['sha256'])
        # 删除空Git元数据目录，避免任务树继续显示仓库。
        gitdirs = sorted([p for p in root.rglob('*') if p.is_dir() and '.git' in p.parts],
                         key=lambda p: len(p.parts), reverse=True)
        for path in gitdirs:
            assert path.resolve().is_relative_to(root)
            if not any(path.iterdir()):
                path.rmdir()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(dict(tasks=str(root), archive=str(archive),
        applied=args.apply, count=len(records), files=records), ensure_ascii=False, indent=2) + '\n')
    print(f'{"Moved" if args.apply else "Planned"} {len(records)} code files')

if __name__ == '__main__':
    main()
