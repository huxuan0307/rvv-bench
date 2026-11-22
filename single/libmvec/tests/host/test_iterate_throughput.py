"""吞吐快速迭代流水线的主机回归：档位、稳态窗口选择与门限；不运行目标。"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'script'
sys.path.insert(0, str(SCRIPT))
import iterate_throughput as pipeline  # noqa: E402

HEADER = ['record_type', 'case', 'function', 'mode', 'n', 'k', 'r', 'phase',
          'cycles', 'window_cycles', 'timing_repeat', 'run_status',
          'case_status', 'measurement_valid']


def sample_rows(mode='auto_m1', repeat=20, n=128, k=1, cycles=None):
    cycles = cycles or [240_000] * 10
    rows = []
    for r in range(10):
        rows.append(dict(record_type='sample', case='perf-sin-' + mode,
                         function='sin', mode=mode, n=n, k=k, r=r,
                         phase='steady' if r >= 3 else 'warmup',
                         cycles=cycles[r], window_cycles=cycles[r],
                         timing_repeat=repeat, run_status='passed',
                         case_status='passed', measurement_valid='true'))
    return rows


def write_samples(rows, path):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, '') for key in HEADER})


class ProfileTests(unittest.TestCase):
    def test_fast_is_default(self):
        args = pipeline.parse_args(['--prefix', 'p', '--data-dir', 'd', '--emu', 'e',
                                    '--reference', 'r', '--dramsim3-ini', 'i',
                                    '--output', 'o'])
        self.assertEqual(args.profile, 'fast')
        self.assertEqual(pipeline.PROFILES['fast'], 'adaptive')
        self.assertEqual(pipeline.PROFILES['full'], 32)

    def test_pick_r_fills_window(self):
        # sin 标量约 92.85 周期/元素、exp 标量约 9.32：都落在 30k 窗口量级。
        self.assertEqual(pipeline.pick_r(128, 1, 92.85), 3)
        self.assertEqual(pipeline.pick_r(128, 1, 9.32), 26)
        # 极快单元格也不会超过协议上限 64。
        self.assertEqual(pipeline.pick_r(128, 1, 0.5), 64)

    def test_bump_r_when_window_short(self):
        # 实测窗口只有 18k、目标 30k：按比例放大并留 20% 余量。
        self.assertEqual(pipeline.bump_r(3, 18_000, 30_000), 6)
        self.assertEqual(pipeline.bump_r(1, 5_000, 30_000), 8)
        # 上限 64，且至少加一。
        self.assertEqual(pipeline.bump_r(64, 1_000, 30_000), 64)
        self.assertEqual(pipeline.bump_r(5, 0, 30_000), 10)

    def test_r_cache_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'r-cache.json'
            pipeline.save_cache(path, {'sin|auto_m1': 66.12, 'exp|scalar_fast': 9.32})
            loaded = pipeline.load_cache(path)
        self.assertAlmostEqual(loaded['sin|auto_m1'], 66.12)
        self.assertAlmostEqual(loaded['exp|scalar_fast'], 9.32)

    def test_missing_cache_is_empty(self):
        # 没有缓存文件时退回内置表，不能抛异常。
        self.assertEqual(pipeline.load_cache(None), {})
        self.assertEqual(pipeline.load_cache(Path('/nonexistent/r-cache.json')), {})

    def test_steady_window_rule(self):
        # 前 3 轮预热、后 7 轮 steady，与正式解析一致。
        self.assertFalse(pipeline.phase_steady(0))
        self.assertFalse(pipeline.phase_steady(2))
        self.assertTrue(pipeline.phase_steady(3))
        self.assertTrue(pipeline.phase_steady(9))


class AggregateTests(unittest.TestCase):
    def test_cycles_per_element_uses_steady_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'samples.csv'
            cycles = [1] * 3 + [240_000] * 6 + [240_060]
            write_samples(sample_rows(repeat=20, cycles=cycles), path)
            rows = pipeline.aggregate(path, 'sin')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['steady_windows'], 7)
        self.assertEqual(row['min_window_cycles'], 240_000)
        self.assertAlmostEqual(row['median_window_cycles'], 240_000)
        # 240000 / R20 / (128*1) = 93.75 周期/元素
        self.assertAlmostEqual(row['cycles_per_element'], 93.75)
        self.assertEqual(pipeline.gate(row), 'ok')

    def test_control_modes_are_skipped(self):
        rows = sample_rows()
        control = sample_rows(mode='rdcycle')
        for row in control:
            row['record_type'] = 'calibration'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'samples.csv'
            write_samples(rows + control, path)
            result = pipeline.aggregate(path, 'sin')
        self.assertEqual([row['mode'] for row in result], ['auto_m1'])

    def test_short_window_and_cv_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'samples.csv'
            write_samples(sample_rows(repeat=12, cycles=[18_000] * 10), path)
            row = pipeline.aggregate(path, 'sin')[0]
        self.assertIn('窗口', pipeline.gate(row))

    def test_failed_measurement_is_rejected(self):
        rows = sample_rows()
        rows[4]['measurement_valid'] = 'false'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'samples.csv'
            write_samples(rows, path)
            row = pipeline.aggregate(path, 'sin')[0]
        self.assertIn('未通过', pipeline.gate(row))


if __name__ == '__main__':
    unittest.main()
