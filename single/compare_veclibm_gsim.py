#!/usr/bin/env python3
"""Compare already-collected gsim performance with historical veclibm HTML.

Never collect logs or launch emulators. All 25 performance cases must terminate
before any CSV is written. Each canonical historical platform contributes 25
rows, including explicit missing-baseline rows for empty tables. Historical
libc/input/build differences prevent interpreting ratios as pure CPU regressions.
"""
import argparse
from collections import Counter
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import math
from pathlib import Path
import re
import sys

DEFAULT_BASELINE = Path('/nfs/home/huxuan/bench_result/rvv-bench-results')
HEADERS = ('function', 'scalar libc fp64/cycle', 'rvv veclibm fp64/cycle', 'speedup')
TERMINAL = {'passed', 'failed', 'timeout'}
COMPARABILITY = 'different_workload_and_libm;baseline_build_and_sampling_unknown'
FIELDS = [
    'function', 'baseline_platform', 'baseline_title', 'baseline_html',
    'baseline_alias_paths', 'baseline_sha256', 'baseline_status', 'comparison_status',
    'current_status', 'current_valid', 'current_n', 'current_repeats', 'current_vlen_bits',
    'current_scalar_fp64_per_cycle', 'current_rvv_fp64_per_cycle',
    'baseline_scalar_fp64_per_cycle', 'baseline_rvv_fp64_per_cycle',
    'scalar_current_to_baseline_throughput_ratio', 'scalar_throughput_pct_change',
    'rvv_current_to_baseline_throughput_ratio', 'rvv_throughput_pct_change',
    'current_scalar_cycles_per_element', 'current_rvv_cycles_per_element',
    'baseline_scalar_cycles_per_element', 'baseline_rvv_cycles_per_element',
    'current_scalar_median_cycles', 'current_rvv_median_cycles',
    'current_scalar_to_rvv_speedup', 'baseline_published_speedup',
    'baseline_published_speedup_text', 'baseline_recomputed_speedup',
    'current_to_baseline_recomputed_speedup_ratio',
    'current_input_interval', 'baseline_input_interval', 'current_scalar_library',
    'baseline_scalar_library', 'baseline_n', 'baseline_repeats', 'baseline_toolchain',
    'baseline_library_version', 'baseline_sampling_details', 'comparability',
    'comparison_limitations', 'baseline_sampling_source', 'baseline_sampling_source_sha256',
    'current_failure_reasons', 'current_binary_sha256', 'current_stdout_log', 'current_stderr_log',
    'run_directory', 'generated_at', 'emulator', 'emulator_sha256', 'reference', 'reference_sha256',
]


def normalized(text):
    return ' '.join(text.split())


def positive_number(text, label):
    text = normalized(str(text))
    if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ValueError(f'{label}: invalid numeric value {text!r}')
    number = float(text)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{label}: expected finite positive number, got {text!r}')
    return number


class VeclibmHTMLParser(HTMLParser):
    """Read visible h1 and table cells; tolerate implicit end of header tr."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings, self.tables = [], []
        self.heading = None
        self.table = None
        self.row = None
        self.cell = None

    def finish_row(self):
        if self.cell is not None:
            raise ValueError('unclosed table cell')
        if self.row is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            if self.heading is not None:
                raise ValueError('nested h1')
            self.heading = []
        elif tag == 'table':
            if self.table is not None:
                raise ValueError('nested tables are not supported')
            self.table = []
        elif self.table is not None and tag == 'tr':
            self.finish_row()
            self.row = []
        elif self.table is not None and tag in ('td', 'th'):
            if self.cell is not None or self.row is None:
                raise ValueError('table cell outside row or nested cell')
            self.cell = (tag, [])
        elif tag == 'br':
            self.handle_data(' ')

    def handle_endtag(self, tag):
        if tag == 'h1':
            if self.heading is None:
                raise ValueError('unmatched h1 end')
            self.headings.append(normalized(''.join(self.heading)))
            self.heading = None
        elif self.table is not None and tag in ('td', 'th'):
            if self.cell is None or self.cell[0] != tag:
                raise ValueError('unmatched table cell end')
            self.row.append((tag, normalized(''.join(self.cell[1]))))
            self.cell = None
        elif self.table is not None and tag in ('tr', 'thead', 'tbody', 'tfoot'):
            self.finish_row()
        elif tag == 'table':
            if self.table is None:
                raise ValueError('unmatched table end')
            self.finish_row()
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, text):
        if self.heading is not None:
            self.heading.append(text)
        if self.cell is not None:
            self.cell[1].append(text)

    def finish(self):
        self.close()
        if self.table is not None or self.cell is not None or self.heading is not None:
            raise ValueError('truncated table or h1')


def parse_baseline_html(text, source='<html>'):
    parser = VeclibmHTMLParser()
    try:
        parser.feed(text)
        parser.finish()
        if len(parser.headings) != 1 or not parser.headings[0]:
            raise ValueError('expected one nonempty h1 platform title')
        candidates = []
        for table in parser.tables:
            if not table:
                continue
            header = tuple(normalized(cell[1]).casefold() for cell in table[0])
            if any(cell[0] == 'th' for cell in table[0]) and any(h in HEADERS for h in header):
                if header != HEADERS or any(cell[0] != 'th' for cell in table[0]):
                    raise ValueError(f'invalid veclibm table headers: {header}')
                candidates.append(table)
        if len(candidates) != 1:
            raise ValueError('expected exactly one veclibm performance table')
        rows = {}
        for cells in candidates[0][1:]:
            if len(cells) != 4 or any(kind != 'td' for kind, text in cells):
                raise ValueError('each data row must contain four td columns')
            function, scalar, rvv, speedup_text = [cell[1] for cell in cells]
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', function):
                raise ValueError(f'invalid function name {function!r}')
            if function in rows:
                raise ValueError(f'duplicate function {function}')
            match = re.fullmatch(r'(.+?)\s*[x×]', speedup_text)
            if not match:
                raise ValueError(f'{function}: speedup lacks x suffix')
            scalar_value = positive_number(scalar, function + ' scalar throughput')
            rvv_value = positive_number(rvv, function + ' RVV throughput')
            speedup_value = positive_number(match[1], function + ' speedup')
            if any(not math.isfinite(value) or value <= 0 for value in
                   (1 / scalar_value, 1 / rvv_value, rvv_value / scalar_value)):
                raise ValueError(f'{function}: numeric values overflow derived baseline metrics')
            rows[function] = dict(scalar=scalar_value, rvv=rvv_value, scalar_text=scalar,
                                  rvv_text=rvv, published_speedup=speedup_value,
                                  published_speedup_text=speedup_text,
                                  recomputed_speedup=rvv_value / scalar_value)
        return dict(title=parser.headings[0], rows=rows)
    except ValueError as exc:
        raise ValueError(f'{source}: {exc}') from exc


def load_baselines(baseline_dir):
    """Deduplicate symlink aliases by resolved HTML path, not platform spelling."""
    baseline_dir = Path(baseline_dir).absolute()
    groups = {}
    for path in sorted(baseline_dir.glob('*/veclibm.html')):
        if path.is_file():
            groups.setdefault(path.resolve(), []).append(path.absolute())
    if not groups:
        raise ValueError(f'no */veclibm.html baselines found under {baseline_dir}')
    template = baseline_dir / 'templates/veclibm.js'
    sampling_source, sampling_sha, interval = None, None, 'unknown'
    if template.is_file():
        raw = template.read_bytes()
        sampling_source, sampling_sha = str(template.resolve()), hashlib.sha256(raw).hexdigest()
        if '[0,1)' in raw.decode('utf-8'):
            interval = '[0,1)'
    platforms = []
    for canonical, paths in sorted(groups.items()):
        raw = canonical.read_bytes()
        parsed = parse_baseline_html(raw.decode('utf-8'), str(canonical))
        parsed.update(platform=canonical.parent.name, html=str(canonical),
                      alias_paths=[str(p) for p in paths if p != canonical],
                      sha256=hashlib.sha256(raw).hexdigest(), input_interval=interval,
                      sampling_source=sampling_source, sampling_source_sha256=sampling_sha)
        platforms.append(parsed)
    return platforms


def current_metrics(case, audit_issues):
    metric = case.get('metrics', {})
    issues = list(case.get('failure_reasons', [])) + list(audit_issues)
    if case['status'] != 'passed' or not case.get('functional_pass'):
        issues.append('current test is not a validated pass')
    issues += list(case.get('validation_issues', []))
    values = {}
    for prefix, mode in [('scalar', 'libm'), ('rvv', 'rvvlm')]:
        data = metric.get('modes', {}).get(mode, {})
        for label, key in [('fp64_per_cycle', 'median_elements_per_cycle'),
                           ('cycles_per_element', 'median_cycles_per_element'),
                           ('median_cycles', 'median_cycles')]:
            try:
                values[prefix + '_' + label] = positive_number(data.get(key, ''), mode + ' ' + key)
            except ValueError as exc:
                issues.append(str(exc))
    try:
        values['scalar_to_rvv_speedup'] = positive_number(metric.get('libm_to_rvvlm_cycle_ratio', ''), 'current cycle ratio')
    except ValueError as exc:
        issues.append(str(exc))
    if not issues:
        for prefix in ('scalar', 'rvv'):
            reciprocal = values[prefix + '_fp64_per_cycle'] * values[prefix + '_cycles_per_element']
            if not math.isclose(reciprocal, 1, rel_tol=1e-9):
                issues.append(prefix + ' throughput and cycles/element are inconsistent')
        ratio = values['scalar_median_cycles'] / values['rvv_median_cycles']
        if not math.isclose(ratio, values['scalar_to_rvv_speedup'], rel_tol=1e-9):
            issues.append('current cycle ratio differs from median cycle counts')
    if issues:
        return False, {}, list(dict.fromkeys(issues))
    return True, values, []


def compare_performance(document, baseline_dir, output):
    """Export one row per historical platform/current function; never reread logs."""
    performance = [case for case in document['cases'] if case['suite'] == 'performance']
    if len(performance) != 25 or len({c['original_name'] for c in performance}) != 25:
        raise ValueError('comparison requires exactly 25 unique performance functions')
    if any(c['status'] not in TERMINAL or (not document.get('complete') and
            not sum(c.get('list_membership', {}).values())) for c in performance):
        return dict(exported=False, output=str(Path(output).absolute()), rows=0, platforms=0,
                    matched_rows=0, missing_rows=0, reason='performance tests still running or pending')
    platforms = load_baselines(baseline_dir)
    metadata = document['run_metadata']
    entries = {c['name']: c['manifest_entry'] for c in metadata['cases']}
    rows = []
    for platform in platforms:
        for case in performance:
            function, metric = case['original_name'], case.get('metrics', {})
            entry = entries[case['name']]
            valid, current, issues = current_metrics(case, document.get('audit_issues', []))
            baseline = platform['rows'].get(function)
            row = dict(function=function, baseline_platform=platform['platform'],
                baseline_title=platform['title'], baseline_html=platform['html'],
                baseline_alias_paths=json.dumps(platform['alias_paths']), baseline_sha256=platform['sha256'],
                baseline_status='available' if baseline is not None else 'baseline_missing',
                comparison_status='invalid_current' if not valid else 'baseline_missing' if baseline is None else 'matched',
                current_status=case['status'], current_valid=valid,
                current_n=entry.get('n', metric.get('n')), current_repeats=entry.get('repeats', metric.get('repeats')),
                current_vlen_bits=metric.get('vlen_bits'),
                current_input_interval='[1.125,1.875)' if function == 'acosh' else '[0.125,0.875)',
                baseline_input_interval=platform['input_interval'],
                current_scalar_library='toolchain static libm', baseline_scalar_library='libc (published HTML label)',
                baseline_n='unknown', baseline_repeats='unknown', baseline_toolchain='unknown',
                baseline_library_version='unknown', baseline_sampling_details='unknown beyond shared input interval statement',
                comparability=COMPARABILITY,
                comparison_limitations='different input distributions and scalar library; historical build, n, repeats and sampling details unknown; not a pure microarchitecture regression; published speedup rounded independently of throughput',
                baseline_sampling_source=platform['sampling_source'], baseline_sampling_source_sha256=platform['sampling_source_sha256'],
                current_failure_reasons=json.dumps(issues, ensure_ascii=False), current_binary_sha256=case.get('sha256'),
                current_stdout_log=case.get('log'), current_stderr_log=case.get('err'),
                run_directory=document['run'], generated_at=document['generated_at'],
                emulator=metadata['emulator']['path'], emulator_sha256=metadata['emulator']['sha256'],
                reference=metadata['reference']['path'], reference_sha256=metadata['reference']['sha256'])
            for key, value in current.items():
                row['current_' + key] = value
            if baseline is not None:
                row.update(baseline_scalar_fp64_per_cycle=baseline['scalar_text'],
                           baseline_rvv_fp64_per_cycle=baseline['rvv_text'],
                           baseline_scalar_cycles_per_element=1 / baseline['scalar'],
                           baseline_rvv_cycles_per_element=1 / baseline['rvv'],
                           baseline_published_speedup=baseline['published_speedup'],
                           baseline_published_speedup_text=baseline['published_speedup_text'],
                           baseline_recomputed_speedup=baseline['recomputed_speedup'])
                if valid:
                    for mode in ('scalar', 'rvv'):
                        ratio = current[mode + '_fp64_per_cycle'] / baseline[mode]
                        row[mode + '_current_to_baseline_throughput_ratio'] = ratio
                        row[mode + '_throughput_pct_change'] = (ratio - 1) * 100
                    row['current_to_baseline_recomputed_speedup_ratio'] = current['scalar_to_rvv_speedup'] / baseline['recomputed_speedup']
            if any(isinstance(value, float) and not math.isfinite(value) for value in row.values()):
                raise ValueError(f'{platform["platform"]}/{function}: derived comparison metric is not finite')
            rows.append(row)
    output = Path(output).absolute()
    if any(path.is_symlink() for path in [output, *output.parents]):
        raise ValueError('refusing symlink output or parent directory')
    if output.exists():
        previous = output.read_text()
        reader = csv.DictReader(io.StringIO(previous))
        old_rows = list(reader)
        if reader.fieldnames != FIELDS or not old_rows or any(r.get('run_directory') != document['run'] for r in old_rows):
            raise ValueError('existing comparison CSV does not belong to this run/schema')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return dict(exported=True, output=str(output), rows=len(rows), platforms=len(platforms),
                matched_rows=sum(r['comparison_status'] == 'matched' for r in rows),
                missing_rows=sum(r['baseline_status'] == 'baseline_missing' for r in rows),
                invalid_current_rows=sum(not r['current_valid'] for r in rows),
                platform_details=[dict(platform=p['platform'], title=p['title'], html=p['html'],
                                       aliases=p['alias_paths'], functions=len(p['rows']), sha256=p['sha256']) for p in platforms])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True, type=Path, help='existing RUN/reports/results.json; never refresh logs')
    parser.add_argument('--baseline-dir', type=Path, default=DEFAULT_BASELINE)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent / '.dsh/data/veclibm-comparison.csv')
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.results.read_text())
        result = compare_performance(document, args.baseline_dir, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'comparison error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return int(bool(result.get('invalid_current_rows')))


if __name__ == '__main__':
    sys.exit(main())
