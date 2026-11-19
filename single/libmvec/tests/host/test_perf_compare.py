#!/usr/bin/env python3
"""覆盖周期收集器的完整性和性能门槛，不运行目标程序。"""
import csv
from decimal import Decimal
from fractions import Fraction
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "script"
sys.path.insert(0, str(SCRIPT))
try:
    import perf_compare as M
finally:
    sys.path.pop(0)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="perf-compare-", dir=os.environ["TEST_TMPDIR"]))
        self.base = self.make("base.csv")
        self.candidate = self.make("candidate.csv", overrides={"auto_m2": 90})

    def make(self, name, overrides=None, mutate=None, repeat=None, wire=None,
             default=100, derived=False, begin=1234):
        rows = []
        for k in (1, 4):
            for index, mode in enumerate(M.MODES):
                for r in range(10):
                    control = mode in M.CALIBRATION_MODES
                    cycles = (overrides or {}).get(mode, 3 if control else default)
                    row = dict(function="log", n=16, k=k, mode=mode, r=r,
                               record_type="calibration" if control else "sample",
                               run_status="passed", case_status="passed", measurement_valid="True",
                               phase=M.phase_of(r), order=(index-r) % len(M.MODES),
                               cycle_begin=begin, cycle_end=(begin+cycles) & M.U64_MAX,
                               cycles=cycles, checksum=42)
                    if wire is not None:
                        row["wire_format"] = wire
                    if repeat is not None:
                        row["timing_repeat"] = repeat
                    if derived:
                        row.update(window_cycles=cycles, window_ok="1")
                    if mutate:
                        row = mutate(row)
                    if row is not None:
                        rows.append(row)
        path = self.path / name
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def compare(self, candidate, base=None):
        return M.compare({"nx": base or self.base}, {"nx": candidate}, "log", 16, (1, 4), Decimal("5"))

    def test_steady_and_modes(self):
        rows, okay = self.compare(self.candidate)
        self.assertTrue(okay)
        self.assertEqual(len(rows), 18)
        selected = next(x for x in rows if x["k"] == 1 and x["mode"] == "auto_m2")
        self.assertEqual((selected["before_median"], selected["after_median"]), ("100.000", "90.000"))

    def test_threshold_inclusive(self):
        at_limit = self.make("limit.csv", overrides={"auto_m2": 90, "direct_m1": 105})
        above_limit = self.make("above.csv", overrides={"auto_m2": 90, "direct_m1": 106})
        self.assertTrue(self.compare(at_limit)[1])
        self.assertFalse(self.compare(above_limit)[1])

    def test_no_improvement_rejected(self):
        self.assertFalse(self.compare(self.base)[1])

    def test_missing_round_rejected(self):
        bad = self.make("missing.csv", mutate=lambda x: None if x["mode"] == "copy" and x["k"] == 1 and x["r"] == 9 else x)
        with self.assertRaisesRegex(ValueError, "缺少"):
            self.compare(bad)

    def test_duplicates_rejected(self):
        with self.base.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        bad = self.path / "duplicate.csv"
        with bad.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.compare(bad)

    def test_bad_status_rotation_or_checksum_rejected(self):
        for column, value in (("measurement_valid", "False"), ("order", 99),
                              ("run_status", "failed"), ("checksum", 7)):
            def mutate(row):
                if row["k"] == 1 and row["mode"] == "auto_m1" and row["r"] == 4:
                    row[column] = value
                return row
            with self.subTest(column=column), self.assertRaises(ValueError):
                self.compare(self.make(column + ".csv", mutate=mutate))

    def test_different_checksum_is_reported_not_silenced(self):
        changed = self.make("different.csv", overrides={"auto_m2": 90}, mutate=lambda r: dict(r, checksum=7))
        rows, okay = self.compare(changed)
        self.assertTrue(okay)
        self.assertTrue(all(x["checksum_equal"] == "no" for x in rows))

    def test_v3_without_derived_columns_and_with_wrapped_counter(self):
        repeated = self.make("v3-old-export.csv", wire="csv3", repeat=2, default=40_001,
                             begin=M.U64_MAX-7)
        groups = M.read_samples(repeated, "log", 16, (1, 4))
        steady, _, repeat, quality = groups[(1, "auto_m1")]
        self.assertEqual(repeat, 2)
        self.assertEqual(steady, [Fraction(40_001, 2)] * 7)
        self.assertEqual(quality["window_cycles"], 40_001)
        rows, _ = self.compare(repeated, repeated)
        control = next(r for r in rows if r["mode"] == "rdcycle")
        self.assertEqual((control["before_median"], control["window_ok"], control["verdict"]),
                         ("3.000", "not_applicable", "diagnostic"))

    def test_repeat_required_bounded_and_consistent(self):
        one = self.make("repeat-one.csv", wire="csv3", repeat=1, default=20_000)
        two = self.make("repeat-two.csv", wire="csv3", repeat=2, default=40_000)
        with self.assertRaisesRegex(ValueError, "timing_repeat"):
            self.compare(two, one)
        for repeat in (None, 0, 65):
            with self.subTest(repeat=repeat), self.assertRaisesRegex(ValueError, "timing_repeat"):
                self.compare(self.make(f"repeat-{repeat}.csv", wire="csv3", repeat=repeat), one)

    def test_legacy_data_has_no_modern_window_verdict(self):
        for wire in ("csv2", "kv1", None):
            path = self.make(f"legacy-{wire}.csv", wire=wire)
            rows, _ = self.compare(path, path)
            self.assertTrue(all(r["window_ok"] == "legacy_no_repeat" for r in rows
                                if r["mode"] not in M.CALIBRATION_MODES))

    def test_mixed_provenance_rejected(self):
        modern = self.make("modern.csv", wire="csv3", repeat=1, default=40_000)
        with self.assertRaisesRegex(ValueError, "wire_format"):
            self.compare(modern)
        mixed = self.make("mixed.csv", wire="csv3", repeat=1, default=40_000,
                          mutate=lambda r: dict(r, wire_format="csv2") if r["k"] == 4 else r)
        with self.assertRaisesRegex(ValueError, "wire_format"):
            self.compare(mixed, modern)

    def test_every_steady_window_must_pass_even_with_forged_flag(self):
        def short(row):
            if row["r"] == 9 and row["mode"] == "auto_m1":
                row.update(cycles=19_999, window_cycles=19_999, cycle_end=row["cycle_begin"] + 19_999)
            return row
        modern = self.make("at-min.csv", wire="csv3", repeat=1, default=20_000)
        bad = self.make("short.csv", wire="csv3", repeat=1, default=20_000,
                        derived=True, mutate=short)
        with self.assertRaisesRegex(ValueError, "window_too_short"):
            self.compare(bad, modern)
        self.compare(modern, modern)

    def test_cv_gate_rejects_noisy_cell(self):
        def noise(row):
            if row["r"] == 9 and row["mode"] == "auto_m1":
                row.update(cycles=24_000, cycle_end=row["cycle_begin"] + 24_000)
            return row
        modern = self.make("steady.csv", wire="csv3", repeat=1, default=20_000)
        bad = self.make("noisy.csv", wire="csv3", repeat=1, default=20_000, mutate=noise)
        with self.assertRaisesRegex(ValueError, "CV"):
            self.compare(bad, modern)

    def test_exact_tie_boundary(self):
        base = self.make("base-modern.csv", wire="csv3", repeat=1, default=25_000)
        for cycles, verdict in ((24_951, "tie"), (24_950, "better"), (25_050, "worse")):
            with self.subTest(cycles=cycles):
                variant = self.make(f"tie-{cycles}.csv", wire="csv3", repeat=1, default=25_000,
                                    overrides={"auto_m2": cycles})
                rows, _ = self.compare(variant, base)
                row = next(r for r in rows if r["mode"] == "auto_m2")
                self.assertEqual(row["verdict"], verdict)


if __name__ == "__main__":
    unittest.main()
