"""Adapt TSVC host harness, retaining upstream kernel bodies and separate dummy TU."""
from pathlib import Path
import re


def prepare(source: Path, output: Path) -> list[dict]:
    source = source / 'src' if (source / 'src').is_dir() else source
    output.mkdir(parents=True, exist_ok=True)
    kernel_functions = re.findall(r'^real_t (\w+)\(struct args_t', (source / 'tsvc.c').read_text(), re.M)
    for name in ('common.h', 'array_defs.h', 'common.c', 'dummy.c', 'tsvc.c'):
        text = (source / name).read_text()
        text = re.sub(r'^#include <[^>]+>\s*$', '', text, flags=re.M)
        if name == 'common.h':
            text = text.replace('#define iterations 100000', '#define iterations 32')
            text = text.replace('#define LEN_1D 32000', '#define LEN_1D 320')
            text = text.replace('#define LEN_2D 256', '#define LEN_2D 32')
        if name == 'tsvc.c':
            begin = text.index('    double tic=func_args.t1.tv_sec')
            end = text.index('\n}', begin)
            text = text[:begin] + '''    unsigned long long cycles = (unsigned long long)(func_args.t2.tv_usec - func_args.t1.tv_usec);
    printf("AV cycles=%llu checksum=%.9g\\n", cycles, result);''' + text[end:]
            text = text.replace('Loop \\tTime(sec) \\tChecksum', 'TSVC cycles and checksum')
        (output / name).write_text('#include "av_runtime.h"\n' + text)
    return [{'suite': 'tsvc', 'name': 'tsvc',
             'sources': [str(output / n) for n in ('tsvc.c', 'common.c', 'dummy.c')],
             'include_dirs': [str(output)], 'defines': [],
             'kernel_sources': ['tsvc.c'],
             'kernel_functions': kernel_functions}]
