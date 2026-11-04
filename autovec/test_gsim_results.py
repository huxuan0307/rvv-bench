"""Validate simulator evidence classification without running an emulator."""
from pathlib import Path
import tempfile
import unittest
from run_gsim import parse_case


class EvidenceTests(unittest.TestCase):
    def classify(self, marker='AV cycles=2048 checksum=1.0 iterations=1024', stderr='', timeout=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 's000.bin'
            binary.write_bytes(b'')
            (root / 'run-s000.log').write_text('HIT GOOD TRAP\n' + marker + '\n')
            (root / 'run-s000.err').write_text(stderr)
            return parse_case({'name': 's000', 'status': 'built', '_binary': binary}, root,
                              {'pass': {'s000'}, 'fail': set(), 'timeout': {'s000'} if timeout else set()})

    def test_valid(self):
        row = self.classify()
        self.assertEqual(row['status'], 'passed')
        self.assertEqual(row['cycles_per_outer_repetition'], 2.0)

    def test_timeout_overrides_success(self):
        self.assertEqual(self.classify(timeout=True)['status'], 'timeout')

    def test_stderr_failure(self):
        self.assertEqual(self.classify(stderr='ERROR difftest failed')['status'], 'failed')

    def test_missing_or_wrong_marker(self):
        for marker in ('', 'AV cycles=2048 checksum=1.0', 'AV cycles=2048 checksum=1.0 iterations=32'):
            with self.subTest(marker=marker):
                self.assertEqual(self.classify(marker=marker)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
