#!/usr/bin/env python3
"""Verify double log semantics extracted for the ABI gate."""
from pathlib import Path
import unittest


class LogSemanticTests(unittest.TestCase):
    def test_double_table_contains_dispatch_edges_and_special_values(self):
        source = (Path(__file__).parent / 'tests/newmath/unit/logvec_safe_unit.c').read_text()
        # 与门禁采用相同double分支选择，防止误取前面的float表。
        table = source.split('#else\nstatic const bits_t special[]={', 1)[1].split('};', 1)[0]
        values = {int(token.strip().removesuffix('ULL'), 0)
                  for token in table.replace('\n', '').split(',') if token.strip()}
        for edge in (0x3fee000000000000, 0x3ff1090000000000):
            self.assertTrue({edge - 1, edge, edge + 1} <= values)
        self.assertTrue({0, 0x8000000000000000, 0x7ff0000000000000,
                         0x7ff0000000000001, 0x7fefffffffffffff} <= values)


if __name__ == '__main__':
    unittest.main()
