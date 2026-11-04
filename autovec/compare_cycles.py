#!/usr/bin/env python3
"""Merge recorded cycle measurements without building or running benchmarks.

From any directory:
    python3 autovec/compare_cycles.py
    python3 autovec/compare_cycles.py --measurements /path/to/measurements --output /tmp/cycles

Writes comparison.csv (one selected attempt per configuration/benchmark),
comparison.json (selected records, configuration signatures, and every attempt
in an audit), and comparison.md (successful total_cycles, blank when missing).
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parent
CSV_FIELDS = ['configuration_id', 'configuration_label', 'suite', 'name', 'status',
              'total_cycles', 'cycles_per_outer_repetition', 'outer_repetitions',
              'checksum', 'run_id', 'source_configuration_id', 'source_records']


def file_identity(value):
    """Use recorded identities, never stat today's possibly replaced binaries."""
    if not isinstance(value, dict):
        raise ValueError('results.json must contain recorded emulator/reference identities')
    # Human-readable mtime is redundant when the precise timestamp is available.
    identity = {key: value.get(key) for key in ('path', 'resolved_path', 'size')}
    identity['mtime_ns' if 'mtime_ns' in value else 'mtime'] = value.get(
        'mtime_ns', value.get('mtime'))
    return identity


def configuration_signature(metadata, record):
    """Exclude manifest selection, output/binary hashes, jobs and retry timeout."""
    build = metadata.get('build')
    if not isinstance(build, dict):
        raise ValueError('sibling results.json metadata.build is required for safe grouping')
    return {
        'build': {key: build.get(key) for key in ('compiler', 'gcc', 'flags', 'sources')},
        'emulator': file_identity(metadata.get('emulator')),
        'reference': file_identity(metadata.get('reference')),
        'outer_repetitions': record.get('outer_repetitions', metadata.get('outer_repetitions')),
        'timing': metadata.get('timing'),
    }


def timestamp(metadata, run_id):
    value = metadata.get('started_at', '')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Legacy runner timestamps have no zone. Treat them consistently without
        # allowing the reporting machine's timezone to change retry ordering.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp(), run_id)
    except (ValueError, TypeError, AttributeError):
        return (0, 0, run_id)


def successful(record):
    cycles = record.get('total_cycles')
    return (record.get('status') == 'passed' and isinstance(cycles, int)
            and not isinstance(cycles, bool) and cycles >= 0)


def merge_records(measurements):
    """Return deterministic selected records and a lossless retry audit."""
    paths = sorted(Path(measurements).glob('*/comparison-records.json'))
    if not paths:
        raise ValueError(f'no */comparison-records.json files under {measurements}')
    configurations = {}
    attempts = []
    for path in paths:
        records = json.loads(path.read_text())
        if not isinstance(records, list):
            raise ValueError(f'{path}: expected a JSON array of records')
        sibling = path.with_name('results.json')
        if not sibling.is_file():
            raise ValueError(f'{path}: sibling results.json is required for safe grouping')
        metadata = json.loads(sibling.read_text()).get('metadata', {})
        for index, original in enumerate(records):
            if not isinstance(original, dict) or not all(original.get(k) for k in ('suite', 'name')):
                raise ValueError(f'{path}: record {index} lacks suite/name')
            signature = configuration_signature(metadata, original)
            digest = hashlib.sha256(json.dumps(signature, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
            config_id = digest[:12]
            if config_id in configurations and configurations[config_id]['signature'] != signature:
                raise ValueError(f'configuration digest collision: {config_id}')
            flags = signature['build']['flags'] or []
            label = shlex.join(flags) if isinstance(flags, list) else str(flags)
            config = configurations.setdefault(config_id, {
                'configuration_id': config_id, 'sha256': digest, 'label': label,
                'signature': signature, 'source_configuration_ids': [],
            })
            original_id = original.get('configuration_id')
            if original_id not in config['source_configuration_ids']:
                config['source_configuration_ids'].append(original_id)
            record = dict(original)
            record.update(configuration_id=config_id, configuration_label=label,
                          source_configuration_id=original_id,
                          source_records=str(path.resolve()), source_record_index=index,
                          started_at=metadata.get('started_at'), selected=False)
            run_id = str(record.get('run_id', path.parent.name))
            record['run_id'] = run_id
            order = (*timestamp(metadata, run_id), str(path), index)
            attempts.append((order, record))
    attempts.sort(key=lambda item: item[0])
    groups = {}
    for _, record in attempts:
        key = (record['configuration_id'], record['suite'], record['name'])
        previous = groups.get(key)
        # A later failed retry must never replace a successful measurement.
        if previous is None or successful(record) or not successful(previous):
            groups[key] = record
    selected = []
    for key in sorted(groups):
        record = groups[key]
        record['selected'] = True
        selected.append(dict(record))
    return {'schema_version': 1,
            'selection_policy': 'latest successful attempt per normalized configuration/suite/name; '
                                'latest attempt if none succeeded; all attempts retained in audit',
            'configurations': [configurations[key] for key in sorted(configurations)],
            'records': selected, 'audit': [record for _, record in attempts]}


def markdown_cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def write_comparison(output, report):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    with (output / 'comparison.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction='ignore',
                                lineterminator='\n')
        writer.writeheader()
        for record in report['records']:
            flat = dict(record)
            if not successful(record):
                flat.update(total_cycles=None, cycles_per_outer_repetition=None)
            writer.writerow(flat)
    configs = report['configurations']
    records = {(r['suite'], r['name'], r['configuration_id']): r for r in report['records']}
    cases = sorted({(r['suite'], r['name']) for r in report['records']})
    lines = ['# Per-benchmark cycle comparison', '',
             'Values are raw **total_cycles** for successful measurements. Blank cells mean '
             'no successful measurement; they do not mean zero.', '',
             'This report makes no speedup claims and provides no scalar baseline. '
             'Different compiler flags, source pins, emulator/reference identities, repetition '
             'counts or timing protocols are separate configurations.', '',
             'The latest successful attempt wins over retries; if none succeeded, the latest '
             'failure is retained in the long table. Every attempt (including failures and '
             'superseded successes) is preserved in the JSON audit.', '',
             '## Configurations', '']
    for config in configs:
        build = config['signature']['build']
        lines.append(f"- **{config['configuration_id']}**: " + markdown_cell(
            f"{build.get('gcc') or ''}; {build.get('compiler') or ''}; {config['label']}"))
    lines += ['', '| Benchmark | ' + ' | '.join(c['configuration_id'] for c in configs) + ' |',
              '| --- | ' + ' | '.join('---:' for _ in configs) + ' |']
    for suite, name in cases:
        cells = [markdown_cell(f'{suite}/{name}')]
        for config in configs:
            record = records.get((suite, name, config['configuration_id']))
            cells.append(str(record['total_cycles']) if record and successful(record) else '')
        lines.append('| ' + ' | '.join(cells) + ' |')
    (output / 'comparison.md').write_text('\n'.join(lines) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--measurements', type=Path, default=ROOT / 'reports/measurements',
                        help='directory containing per-run comparison-records.json and results.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/cycle-comparison',
                        help='output directory (default: autovec/reports/cycle-comparison)')
    args = parser.parse_args(argv)
    try:
        report = merge_records(args.measurements)
        write_comparison(args.output, report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f"Compared {len(report['audit'])} attempts across {len(report['configurations'])} "
          f"configurations: {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
