#!/usr/bin/env python3
"""覆盖周期收集器的完整性和性能门槛，不运行目标程序。"""
import csv
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "perf_compare.py"
SPEC = importlib.util.spec_from_file_location("perf_compare", SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="perf-compare-", dir=os.environ["TEST_TMPDIR"]))
        self.base = self.make("base.csv")
        self.candidate = self.make("candidate.csv", improvements={"auto_m2": 90})

    def make(self, name, improvements=None, mutate=None, repeat=1, cycles_scale=1):
        rows = []
        fields = ("function", "n", "k", "mode", "r", "record_type", "run_status", "case_status",
                  "measurement_valid", "phase", "order", "cycle_begin", "cycle_end", "cycles",
                  "timing_repeat", "checksum")
        for k in (1, 4):
            for index, mode in enumerate(M.MODES):
                for r in range(10):
                    cycles = (improvements or {}).get(mode, 100) * cycles_scale
                    row = dict(function="log", n=16, k=k, mode=mode, r=r,
                               record_type="sample" if index < 6 else "calibration",
                               run_status="passed", case_status="passed", measurement_valid="True",
                               phase=M.phase_of(r), order=(index-r) % len(M.MODES),
                               cycle_begin=1234, cycle_end=1234+cycles, cycles=cycles,
                               timing_repeat=repeat, checksum=42)
                    if mutate:
                        row = mutate(row)
                    if row is not None:
                        rows.append(row)
        path = self.path / name
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        return path

    def compare(self, candidate):
        return M.compare({"nx": self.base}, {"nx": candidate}, "log", 16, (1, 4), Decimal("5"))

    def test_steady_and_modes(self):
        rows, okay = self.compare(self.candidate)
        self.assertTrue(okay)
        self.assertEqual(len(rows), 18)
        selected = next(x for x in rows if x["k"] == 1 and x["mode"] == "auto_m2")
        self.assertEqual((selected["before_median"], selected["after_median"]), ("100.000", "90.000"))

    def test_threshold_inclusive(self):
        at_limit = self.make("limit.csv", improvements={"auto_m2": 90, "direct_m1": 105})
        above_limit = self.make("above.csv", improvements={"auto_m2": 90, "direct_m1": 106})
        self.assertTrue(self.compare(at_limit)[1])
        self.assertFalse(self.compare(above_limit)[1])

    def test_no_improvement_rejected(self):
        self.assertFalse(self.compare(self.base)[1])

    def test_missing_round_rejected(self):
        bad = self.make("missing.csv", mutate=lambda x: None if x["mode"] == "copy" and x["k"] == 1 and x["r"] == 9 else x)
        with self.assertRaisesRegex(ValueError, "缺少"):
            self.compare(bad)

    def test_duplicates_rejected(self):
        with self.base.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f)); fields = rows[0].keys()
        bad = self.path / "duplicate.csv"
        with bad.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.compare(bad)

    def test_bad_status_or_rotation_rejected(self):
        for column, value in (("measurement_valid", "False"), ("order", 99), ("run_status", "failed")):
            def mutate(row):
                if row["k"] == 1 and row["mode"] == "auto_m1" and row["r"] == 4:
                    row[column] = value
                return row
            with self.subTest(column=column):
                with self.assertRaises(ValueError):
                    self.compare(self.make(column + ".csv", mutate=mutate))

    def test_checksum_drift_rejected(self):
        def mutate(row):
            if row["r"] == 9:
                row["checksum"] = 7
            return row
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.compare(self.make("checksum.csv", mutate=mutate))

    def test_different_checksum_is_reported_not_silenced(self):
        changed = self.make("different.csv", improvements={"auto_m2": 90}, mutate=lambda r: dict(r, checksum=7))
        rows, okay = self.compare(changed)
        self.assertTrue(okay)
        self.assertTrue(all(x["checksum_equal"] == "no" for x in rows))


    def test_timing_repeat_reports_per_round(self):
        repeated = self.make("repeat2.csv", repeat=2, cycles_scale=2)
        groups = M.read_samples(repeated, "log", 16, (1, 4))
        steady, checksum, repeat = groups[(1, "auto_m1")]
        self.assertEqual(repeat, 2)
        self.assertEqual([float(value) for value in steady], [100.0] * 7)
        self.assertEqual(checksum, "42")

    def test_timing_repeat_mismatch_rejected(self):
        one = self.make("repeat1.csv")
        two = self.make("repeat2-mismatch.csv", repeat=2, cycles_scale=2)
        with self.assertRaisesRegex(ValueError, "timing_repeat"):
            M.compare({"nx": one}, {"nx": two}, "log", 16, (1, 4), Decimal("5"))

    def test_invalid_timing_repeat_rejected(self):
        with self.assertRaisesRegex(ValueError, "timing_repeat"):
            M.read_samples(self.make("repeat0.csv", repeat=0), "log", 16, (1, 4))


if __name__ == "__main__":
    unittest.main()
