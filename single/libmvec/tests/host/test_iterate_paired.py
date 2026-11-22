"""快速迭代流水线的主机回归：档位参数、门限判定与汇总口径；不运行目标。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / 'script' / 'latency'
sys.path.insert(0, str(SCRIPT))
import iterate_paired as pipeline  # noqa: E402


class ProfileTests(unittest.TestCase):
    def test_fast_keeps_protocol_windows(self):
        # fast 只压窗口长度，稳态窗口数与协议门限不变；R 交给构建器自校准。
        fast = pipeline.PROFILES['fast']
        self.assertEqual(fast['rounds'], 8)
        self.assertEqual(fast['k'], '1')
        self.assertEqual(fast['streams'], '1')
        self.assertEqual(fast['timing_repeat'], 'auto')

    def test_full_matches_accepted_protocol(self):
        full = pipeline.PROFILES['full']
        self.assertEqual(full['rounds'], 8)
        self.assertEqual(full['k'], '1,2')
        self.assertEqual(full['timing_repeat'], 'auto')

    def test_profile_flags_are_forwarded(self):
        flags = pipeline.profile_flags('fast')
        self.assertEqual(flags[flags.index('--paired-k') + 1], '1')
        self.assertEqual(flags[flags.index('--rounds') + 1], '8')
        self.assertEqual(flags[flags.index('--timing-repeat') + 1], 'auto')
        full = pipeline.profile_flags('full')
        self.assertEqual(full[full.index('--timing-repeat') + 1], 'auto')
        self.assertEqual(full[full.index('--paired-k') + 1], '1,2')


def row(**overrides):
    base = dict(function='sin', probe='m1_auto_unmasked', case='sin|m1|auto|unmasked',
                K='1', R='6', steady_windows='5', min_window_cycles='34819',
                median_window_cycles='34820.0',
                median_cycles_per_call='90.67708333333333',
                sample_cv='0.0017', quality='eligible')
    base.update(overrides)
    return base


class GateTests(unittest.TestCase):
    def test_eligible_row_passes(self):
        self.assertEqual(pipeline.gate(row()), 'ok')

    def test_short_window_is_rejected(self):
        # R=4 档实测 exp 的窗口只有 17.2k，必须被门限拦住。
        self.assertIn('窗口', pipeline.gate(row(min_window_cycles='17211')))

    def test_quality_flag_is_rejected(self):
        self.assertIn('quality', pipeline.gate(row(quality='short-window')))

    def test_cv_is_rejected(self):
        self.assertIn('CV', pipeline.gate(row(sample_cv='0.03')))

    def test_steady_window_floor(self):
        self.assertIn('稳态窗口', pipeline.gate(row(steady_windows='3')))


if __name__ == '__main__':
    unittest.main()
