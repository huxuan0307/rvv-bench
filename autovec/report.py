#!/usr/bin/env python3
"""Summarize GCC diagnostics and kernel-only RVV static instruction counts."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--build', type=Path, default=HERE / 'build/O3')
    p.add_argument('--output', type=Path, default=HERE / 'reports/gcc-default.md')
    a = p.parse_args()
    manifest = json.loads((a.build / 'manifest.json').read_text())
    runs = {(r['suite'], r['name']): r['status'] for r in json.loads((a.build / 'runs.json').read_text())} if (a.build / 'runs.json').exists() else {}
    rows = []
    details = []
    for case in manifest['cases']:
        dest = Path(case['directory'])
        optimized, missed = set(), set()
        for file in sorted(dest.glob('*.vect')):
            if re.search(r'\.\d+t\.vect$', file.name):
                continue
            if case['suite'] == 'tsvc' and file.stem in ('common-3', 'arrays-2', 'dummy-4'):
                continue  # Shared support is recompiled for each standalone case, not a test kernel.
            for line in file.read_text().splitlines():
                m = re.match(r'(.+?):(\d+):(\d+): (optimized|missed): (.*)', line)
                if not m:
                    continue
                path, lineno, col, level, message = m.groups()
                # Successful loop/SLP sites, not internal retries or summary notes.
                if level == 'optimized' and ('loop vectorized' in message or 'basic block part vectorized' in message):
                    optimized.add((Path(path).name, int(lineno), int(col), message))
                if level == 'missed' and 'couldn\'t vectorize loop' in message:
                    missed.add((Path(path).name, int(lineno), int(col)))
        asm = (dest / 'disassembly.txt').read_text() if (dest / 'disassembly.txt').exists() else ''
        functions = Counter()
        current = ''
        for line in asm.splitlines():
            m = re.match(r'^[0-9a-f]+ <(.+)>:', line)
            if m:
                current = m.group(1)
            m = re.match(r'^\s*[0-9a-f]+:\s+[0-9a-f]+\s+(v\w+(?:\.\w+)*)\s', line)
            if m:
                functions[current] += 1
        vectorized_functions = set()
        kernel_vectorized_functions = set()
        for dump in dest.glob('*.vect'):
            if not re.search(r'\.\d+t\.vect$', dump.name):
                continue
            fn = ''
            for line in dump.read_text().splitlines():
                m = re.match(r';; Function (.+?) \(', line)
                if m:
                    fn = m.group(1)
                if 'LOOP VECTORIZED' in line:
                    vectorized_functions.add(fn)
        if case['suite'] == 'tsvc':
            kernel = {f: n for f, n in functions.items() if re.fullmatch(r'(s\d+|v\w+)(?:\..*)?', f)}
            kernel_sites = [s for s in optimized if s[0] == 'tsvc.c' and s[1] < 3944]
        elif case['suite'] == 'polybench':
            kernel = {f: n for f, n in functions.items() if 'kernel_' in f}
            kernel_sites = [s for s in optimized if any('kernel_' in f for f in kernel)]
        else:
            kernel = {f: n for f, n in functions.items() if not any(x in f for x in ['printf', 'mem', 'av_', 'init', '_trm', '_start', '__', 'rand', 'put', 'print'])}
            kernel_sites = list(optimized)
        if case['suite'] == 'tsvc':
            kernel_vectorized_functions = vectorized_functions.intersection(case.get('kernel_functions', []))
        elif case['suite'] == 'polybench':
            kernel_vectorized_functions = {f for f in vectorized_functions if f.startswith('kernel_')}
        else:
            kernel_vectorized_functions = {f for f in vectorized_functions if 'VectorFn' in f or f.startswith('example')}
        row = {'suite': case['suite'], 'name': case['name'], 'build': case['status'], 'run': runs.get((case['suite'], case['name']), 'not-run'), 'optimized_sites': len(optimized), 'missed_sites': len(missed), 'kernel_rvv_instructions': sum(kernel.values()), 'kernel_rvv_functions': len(kernel), 'loop_vectorized_kernel_functions': len(kernel_vectorized_functions)}
        rows.append(row)
        details.append({'suite': case['suite'], 'name': case['name'], 'optimized': sorted(optimized), 'missed': sorted(missed), 'rvv_functions': dict(functions), 'kernel_rvv_functions': kernel, 'loop_vectorized_functions': sorted(vectorized_functions), 'loop_vectorized_kernel_functions': sorted(kernel_vectorized_functions)})
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.with_suffix('.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n'); w.writeheader(); w.writerows(rows)
    a.output.with_suffix('.json').write_text(json.dumps({'rows': rows, 'details': details}, indent=2))
    text = ['# GCC 默认策略 RVV 自动向量化报告', '', f"编译器：`{manifest['compiler']}`。", '',
            '基线：`-O3 -march=rv64gcv -mabi=lp64d -mcmodel=medany -ffreestanding -fno-builtin`。未设置 fast-math、向量化成本模型、LMUL、VLEN 或强制向量化选项。运行时单独关闭向量化，其代码不参与 benchmark 成功位点统计。', '',
            '## 总体结果', '', '| 测试组 | 可执行文件 | 构建成功 | 裸机运行通过 | GCC 成功位点 |', '| --- | ---: | ---: | ---: | ---: |']
    for suite in ['tsvc', 'polybench', 'llvm']:
        subset = [r for r in rows if r['suite'] == suite]
        if subset:
            text.append(f"| {suite} | {len(subset)} | {sum(r['build']=='built' for r in subset)} | {sum(r['run']=='passed' for r in subset)} | {sum(r['optimized_sites'] for r in subset)} |")
    comparisons = json.loads((a.build / 'comparison.json').read_text()) if (a.build / 'comparison.json').exists() else []
    comparison_counts = {status: sum(x['status'] == status for x in comparisons) for status in ('exact-match', 'tolerance-match', 'different-or-missing')}
    text += ['', f"标量参考比对：{comparison_counts['exact-match']} 项精确匹配，{comparison_counts['tolerance-match']} 项在数值容差内，{comparison_counts['different-or-missing']} 项不匹配或缺失。详见 comparison.json。", '', '## 统计口径与限制', '',

             '- 成功位点：GCC `-fopt-info-vec-all` 中 loop vectorized / basic block part vectorized 的源码位置与消息去重；TSVC 排除逐项重复编译的公共 arrays/common/dummy 文件，其他测试组仍包含初始化、检查代码；不等价于内核覆盖率。',
             '- missed 位点只计 couldn\'t vectorize loop，GCC 同一循环有多次分析与变换，成功和 missed 可同时出现，不能据此直接计算失败率。',
             '- 内核 RVV 数量为反汇编中的静态指令数，含 vset 指令，不能解释为运行时执行次数或加速比。TSVC 使用 sNNN/v* 函数，PolyBench 使用 kernel_* 函数；LLVM 的混合 harness 统计是筛选后的近似值，精确归因见逐函数 JSON。',
             '- TSVC：151 项独立源码/ELF/BIN，LEN_1D=320、LEN_2D=32、iterations=32；保留独立 dummy 翻译单元且不启用 LTO，每个程序仅执行一项。宿主时间调用换为 cycle 读取。独立 BSS 与上游部分不完全初始化导致 23 项 checksum 不同于旧聚合运行，但全部与各自独立标量参考匹配。',
             '- PolyBench 使用 SMALL_DATASET；LLVM 保留可移植标量循环与检查，适配范围和排除原因见移植说明。',
             '- NEMU 验证裸机启动和程序成功退出；各向量版本另外与关闭 loop/SLP vectorization 的同编译器构建逐项比较。PolyBench 比较所有 live-out 值，TSVC 比较原始 checksum，LLVM 用例比较上游驱动 checksum。模拟器周期不代表硬件性能。',
             '- 没有开展 LLVM 编译器实验；LLVM test-suite 在这里是由 GCC 编译的测试源码集合。', '', '## 逐项结果', '',
             '| 测试组 | 用例 | 构建 | 运行 | 成功位点 | missed 位点 | 内核静态 RVV |', '| --- | --- | --- | --- | ---: | ---: | ---: |']
    for r in rows:
        text.append(f"| {r['suite']} | {r['name']} | {r['build']} | {r['run']} | {r['optimized_sites']} | {r['missed_sites']} | {r['kernel_rvv_instructions']} |")
    text += ['', '## 复现与原始证据', '',
             '在 rvv-bench 执行 `make -C autovec all run report`。各用例目录保存 commands.json、GCC .vect 文本及 tree-vect dump、链接日志、反汇编、ELF、BIN 和 NEMU run.log；build/O3 中保存编译器默认优化器与目标选项、构建清单及运行状态。报告旁的 CSV 用于汇总，JSON 保存去重位点和逐函数 RVV 指令计数。', '']
    a.output.write_text('\n'.join(text))
    print(a.output)

if __name__ == '__main__':
    main()
