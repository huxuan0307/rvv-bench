#!/usr/bin/env python3
"""Host-only tests: exact IEEE operations and MPFR interval comparisons."""
import contextlib
import csv
from fractions import Fraction
import gzip
import io
from pathlib import Path
import random
import re
import struct
import unittest
from unittest import mock

import mpfr_accuracy as a


class ExactTest(unittest.TestCase):
    def test_ieee_round_trip(self):
        rng = random.Random(123456789)
        for fmt in (a.B32, a.B64):
            fixed = [0, fmt.sign, 1, fmt.sign | 1, 1 << fmt.frac,
                     fmt.maxfinite, fmt.sign | fmt.maxfinite]
            for bits in fixed + [rng.getrandbits(fmt.width) for _ in range(2000)]:
                if a.ieee_kind(bits, fmt) in ("nan", "inf"):
                    continue
                result = a.round_rne_bits(a.ieee_fraction(bits, fmt), fmt, bool(bits & fmt.sign))
                self.assertEqual(result, bits)

    def test_ties_subnormals_binade_and_overflow(self):
        for fmt in (a.B32, a.B64):
            sign = fmt.sign
            step = a.power2(-fmt.frac)
            one = a.ieee_fraction(fmt.bias << fmt.frac, fmt)
            onebits = fmt.bias << fmt.frac
            self.assertEqual(a.round_rne_bits(one + step/2, fmt), onebits)
            self.assertEqual(a.round_rne_bits(one + 3*step/2, fmt), onebits + 2)
            self.assertEqual(a.round_rne_bits(fmt.minsubnormal/2, fmt), 0)
            self.assertEqual(a.round_rne_bits(-fmt.minsubnormal/2, fmt), sign)
            self.assertEqual(a.round_rne_bits(3*fmt.minsubnormal/2, fmt), 2)
            largest = a.ieee_fraction(fmt.maxfinite, fmt)
            overflow_ulp = a.power2(fmt.bias - fmt.frac)
            self.assertEqual(a.round_rne_bits(largest + overflow_ulp/2, fmt), fmt.expmask << fmt.frac)
            self.assertEqual(a.round_rne_bits(largest + overflow_ulp/2 - overflow_ulp/4, fmt), fmt.maxfinite)

    def test_midpoint_not_encoding_distance(self):
        # RNE output at 1, the other at 1+ULP.  If true value is just above
        # the midpoint, the second output is strictly closer despite its
        # distance of one encoding step from the RNE output.
        lo, hi = Fraction(51, 100), Fraction(52, 100)
        self.assertEqual(a.compare_closeness(Fraction(1), Fraction(0), (lo, hi)), "closer")
        self.assertEqual(a.compare_closeness(Fraction(0), Fraction(1), (lo, hi)), "worse")
        self.assertEqual(a.compare_closeness(Fraction(0), Fraction(1), (Fraction(1, 2), Fraction(1, 2))), "tie")
        self.assertEqual(a.compare_closeness(Fraction(0), Fraction(1), (Fraction(49,100), Fraction(51,100))), "unresolved")
        self.assertEqual(a.compare_closeness(Fraction(1), Fraction(1), (lo, hi)), "tie")

    def test_ulp_intervals_and_binades(self):
        # A double ULP at truth=1 is 2**-52.
        s = a.power2(-52)
        self.assertEqual(a.ulp_error_bin(Fraction(1), (Fraction(1)+s/8, Fraction(1)+s/4), a.B64), "le_0.5")
        self.assertEqual(a.ulp_error_bin(Fraction(1), (Fraction(1)+3*s/4, Fraction(1)+s), a.B64), "le_1")
        self.assertEqual(a.ulp_error_bin(Fraction(1), (Fraction(1)+s/4, Fraction(1)+3*s/4), a.B64), "unresolved")
        self.assertEqual(a.ulp_error_bin(Fraction(1), (Fraction(1)-s/8, Fraction(1)+s/8), a.B64), "cross_binade")
        self.assertEqual(a.ulp_error_bin(Fraction(1), (Fraction(1), Fraction(1)), a.B64), "zero")
        tiny = a.B64.minsubnormal
        self.assertEqual(a.ulp_error_bin(tiny, (tiny*Fraction(9,8), tiny*Fraction(5,4)), a.B64), "le_0.5")

    def test_ref_signed_zero(self):
        self.assertEqual(a.reference_status((Fraction(0), Fraction(0)), (True, True), a.B64, 0), "zero_sign_mismatch")
        self.assertEqual(a.reference_status((Fraction(0), Fraction(0)), (True, False), a.B64, 0), "zero_sign_unresolved")


class MpfrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mpfr = a.MPFR()
        cls.root = Path(__file__).resolve().parents[5] / "inputs" / "perf"

    def test_nested_and_real_frozen_references(self):
        for name in a.FUNCTIONS:
            with self.subTest(name=name):
                text = (self.root / (name + ".h")).read_text(encoding="ascii")
                def first(array):
                    match = re.search(r"static const uint(?:32|64)_t\s+" + array + r"\[[^\]]+\]\s*=\s*\{(.*?)\};", text, re.S)
                    return int(re.search(r"0x([0-9a-fA-F]+)", match[1])[1], 16)
                x, z, ref = (first(k) for k in ("input_bits", "second_bits", "reference_bits"))
                fmt = a.B32 if name in ("logf", "expf") else a.B64
                b256, _, s256 = self.mpfr.bounds(name, x, z, fmt, 256)
                b512, signs, s512 = self.mpfr.bounds(name, x, z, fmt, 512)
                self.assertEqual((s256, s512), ("ok", "ok"))
                self.assertLessEqual(b256[0], b512[0])
                self.assertLessEqual(b512[1], b256[1])
                self.assertEqual(a.reference_status(b512, signs, fmt, ref), "match")

    def test_trig_largest_finite(self):
        x = 0x7fefffffffffffff
        for name in ("sin", "cos"):
            lo, _, _ = self.mpfr.bounds(name, x, None, a.B64, 256)
            hi, signs, status = self.mpfr.bounds(name, x, None, a.B64, 512)
            self.assertEqual(status, "ok")
            self.assertLessEqual(lo[0], hi[0])
            self.assertLessEqual(hi[1], lo[1])
            self.assertNotEqual(a.reference_status(hi, signs, a.B64, 0), "unresolved")

    def test_pow_negative_noninteger_and_overflow(self):
        _, _, status = self.mpfr.bounds("pow", 0xbff0000000000000, 0x3fe0000000000000, a.B64, 512)
        self.assertEqual(status, "math_nan")
        b, _, status = self.mpfr.bounds("pow", 0x4000000000000000, 0x4090000000000000, a.B64, 512)
        self.assertEqual(status, "ok")
        self.assertEqual(a.target_class(b, a.B64), "outside_target_range")

    def test_mpfr_range_extremes_do_not_materialize_huge_integer(self):
        for x, want in ((0x7fefffffffffffff, "mpfr_overflow"), (0xffefffffffffffff, "mpfr_underflow")):
            bounds, _, status = self.mpfr.bounds("exp", x, None, a.B64, 512)
            self.assertIsNone(bounds)
            self.assertEqual(status, want)
        bounds, _, status = self.mpfr.bounds("exp", 0x40e86a0000000000, None, a.B64, 512) # exp(50000)
        self.assertIsNone(bounds)
        self.assertEqual(status, "extreme_large_guard")
        bounds, _, status = self.mpfr.bounds("exp", 0xc0e86a0000000000, None, a.B64, 512) # exp(-50000)
        self.assertIsNone(bounds)
        self.assertEqual(status, "extreme_tiny_guard")
        cases = {mode: (0xc0e86a0000000000, None, None, 0) for mode in a.MODES}
        result = a.analyze_group(self.mpfr, ("exp", "Nx", "1", "S0:0"), cases, (512,))
        self.assertEqual((result[0], result[2]["RTZ"]), ("extreme_tiny_guard", "unresolved"))

    def test_defined_zero_inputs_and_zero_output_accuracy(self):
        for name, x, z, expected_category in (
            ("sin", 0x8000000000000000, None, "exact_zero"),
            ("cos", 0, None, "normal_range"),
            ("exp", 0, None, "normal_range"),
            ("pow", 0x4000000000000000, 0, "normal_range"),
            ("log", 0x3ff0000000000000, None, "exact_zero"),
        ):
            with self.subTest(name=name):
                # Deliberately make RNE output 1 and RTZ output 0.
                cases = {mode: (x, z, None, 0x3ff0000000000000) for mode in a.MODES}
                cases["RTZ"] = (x, z, None, 0)
                result = a.analyze_group(self.mpfr, (name, "Nx", "1", "S0:0"), cases, (512,))
                self.assertEqual(result[0], expected_category)
                self.assertEqual(result[2]["RTZ"], "closer" if expected_category == "exact_zero" else "worse")
        self.assertEqual(a.ieee_detailed_kind(0, a.B64), "positive_zero")
        self.assertEqual(a.ieee_detailed_kind(a.B64.sign, a.B64), "negative_zero")
        self.assertEqual(a.ulp_error_bin(Fraction(0), (Fraction(0), Fraction(0)), a.B64), "zero")

    def test_sin_minsub_vs_zero(self):
        # sin(minsub) is very slightly below minsub; at 512 bits the
        # directed enclosure already proves it exceeds half a minsub.
        cases = {mode: (1, None, None, 0) for mode in a.MODES}
        cases["RTZ"] = (1, None, None, 1)
        category, _, comparison, _, _ = a.analyze_group(self.mpfr, ("sin", "Nx", "1", "S0:0"), cases, (512,))
        self.assertIn(category, ("below_minsubnormal", "cross_minsubnormal"))
        self.assertEqual(comparison["RTZ"], "closer")

    def test_input_exact_minsubnormal(self):
        value = self.mpfr.new(512)
        try:
            for fmt in (a.B32, a.B64):
                for bits in (1, fmt.sign | 1, fmt.maxfinite, fmt.sign | fmt.maxfinite):
                    self.mpfr.set_ieee(value, bits, fmt)
                    self.assertEqual(self.mpfr.as_fraction(value), a.ieee_fraction(bits, fmt))
        finally:
            self.mpfr.clear(value)


def csv_text(records):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=a.FIELDS)
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue()


def rows():
    return [dict(function="sin", interface="Nx", lmul="1", sample_id="C0", frm=str(i),
                 x_bits="403a1ce76a5bc65c", z_bits="", y_bits="3fea942fd12cb9c5",
                 ref_bits="3fea942fd12cb9c5") for i in range(5)]


class CsvTest(unittest.TestCase):
    def parse(self, data, expected=None):
        expected = expected or {("sin", "Nx", "1"): 1}
        return list(a.groups(csv.DictReader(io.StringIO(csv_text(data))), expected))

    def test_valid_and_missing_or_duplicate(self):
        group = self.parse(rows())
        self.assertEqual(len(group), 1)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.parse(rows()[:-1])
        with self.assertRaisesRegex(ValueError, "duplicate frm"):
            self.parse(rows()+[rows()[0]])
        with self.assertRaisesRegex(ValueError, "duplicate or non-contiguous"):
            self.parse(rows()+[dict(r, sample_id="C1") for r in rows()]+rows(), {("sin", "Nx", "1"): 2})

    def test_missing_ids_and_blocks(self):
        with self.assertRaisesRegex(ValueError, "empty input"):
            self.parse([])
        with self.assertRaisesRegex(ValueError, "expected 2 corpus IDs"):
            self.parse(rows(), {("sin", "Nx", "1"): 2})
        with self.assertRaisesRegex(ValueError, "out of range"):
            self.parse([dict(r, sample_id="C1") for r in rows()])
        with self.assertRaisesRegex(ValueError, "missing expected scopes"):
            self.parse(rows(), {("sin", "Nx", "1"): 1, ("sin", "array", "1"): 1})
        with self.assertRaisesRegex(ValueError, "block repeated"):
            self.parse(rows()+[dict(r, interface="array") for r in rows()]+rows(), {("sin", "Nx", "1"): 1, ("sin", "array", "1"): 1})
        # Semantic rows do not fill a corpus hole.
        semantic = [dict(r, sample_id="S0:0", ref_bits="") for r in rows()]
        with self.assertRaisesRegex(ValueError, "expected 2 corpus IDs"):
            self.parse(rows()+semantic, {("sin", "Nx", "1"): 2})

    def test_optional_legacy_ulp_column_validated_only(self):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=a.FIELDS + ("rne_ref_ulp",))
        writer.writeheader()
        writer.writerows([dict(row, rne_ref_ulp="123") for row in rows()])
        groups = list(a.groups(csv.DictReader(io.StringIO(output.getvalue())), {("sin", "Nx", "1"): 1}))
        self.assertEqual(len(groups), 1)
        text = output.getvalue().replace(",123", ",-1")
        with self.assertRaisesRegex(ValueError, "unsigned decimal"):
            list(a.groups(csv.DictReader(io.StringIO(text)), {("sin", "Nx", "1"): 1}))

    def test_reference_and_input_consistency(self):
        r = rows()
        r[1]["x_bits"] = "403a1ce76a5bc65d"
        with self.assertRaisesRegex(ValueError, "differs"):
            self.parse(r)
        r = rows()
        r[0]["ref_bits"] = "3fea942fd12cb9c6"
        with self.assertRaisesRegex(ValueError, "differs"):
            self.parse(r)
        r = rows()
        r[0]["z_bits"] = "0000000000000000"
        with self.assertRaisesRegex(ValueError, "unary"):
            self.parse(r)

    def test_end_to_end_analysis(self):
        key, group = self.parse(rows())[0]
        category, ref, comparisons, errors, precision = a.analyze_group(a.MPFR(), key, group, (256, 512))
        self.assertEqual((category, ref, precision), ("normal_range", "match", 256))
        self.assertEqual(set(comparisons.values()), {"tie"})
        self.assertEqual(set(errors.values()), {"le_0.5"})


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[5] / "inputs" / "perf"
        with contextlib.redirect_stderr(io.StringIO()):
            x, _, ref = a.load_oracles(cls.root, "perf", ("sin",))["sin"]
        data = []
        for i, (xx, rr) in enumerate(zip(x, ref)):
            for rm in range(5):
                data.append(dict(function="sin", interface="Nx", lmul="1", sample_id=f"C{i}", frm=str(rm),
                                 x_bits=f"{xx:016x}", z_bits="", y_bits=f"{rr:016x}", ref_bits=f"{rr:016x}"))
        # Synthetic outputs: all five modes copy the frozen RNE reference.
        # This tests plumbing, not target rounding-mode behavior.
        data.extend(dict(row, sample_id="S0:0", x_bits="0000000000000000", y_bits="7ff0000000000000", ref_bits="") for row in rows())
        cls.text = csv_text(data)
        cls.args = ["-", "--oracle-dir", str(cls.root), "--profile", "perf", "--functions", "sin",
                    "--interfaces", "Nx", "--lmuls", "1", "--precisions", "256"]

    def invoke(self, text, args=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = a.main(args or self.args)
        return rc, out.getvalue(), err.getvalue()

    def test_complete_synthetic_and_separate_semantic(self):
        rc, out, err = self.invoke(self.text)
        self.assertEqual(rc, 0, err)
        result = list(csv.DictReader(io.StringIO(out)))
        def count(kind, mode, metric, category):
            return sum(int(row["count"]) for row in result if (row["sample_kind"], row["frm"], row["metric"], row["category"]) == (kind, mode, metric, category))
        self.assertEqual(count("corpus_C", "RNE", "samples", "total"), 1024)
        self.assertEqual(count("semantic_S_unvalidated", "RNE", "samples", "total"), 1)
        self.assertEqual(count("semantic_S_unvalidated", "RNE", "finite_truth_output", "inf"), 1)
        self.assertEqual(count("semantic_S_unvalidated", "RNE", "comparison_eligibility", "finite_truth_output_inf"), 1)
        self.assertEqual(count("corpus_C", "RNE", "reference", "match"), 1024)
        self.assertIn("input_sha256=stdin-unavailable", err)

    def test_corrupt_corpus_and_missing_group_fail_closed(self):
        rc, out, err = self.invoke(self.text.replace("403a1ce76a5bc65c", "403a1ce76a5bc65d", 5))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("differs from frozen header", err)
        truncated = "\n".join(self.text.splitlines()[:-10]) + "\n"
        rc, out, err = self.invoke(truncated)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("expected 1024 corpus IDs", err)

    def test_mpfr_reference_mismatch_is_nonzero_with_summary(self):
        fake = mock.MagicMock()
        fake.version, fake.path = "test", "test"
        fake.bounds.return_value = ((Fraction(2), Fraction(2)), (False, False), "ok")
        with mock.patch.object(a, "MPFR", return_value=fake):
            rc, out, err = self.invoke(self.text)
        self.assertEqual(rc, 1)
        self.assertIn(",reference,mismatch,", out)
        self.assertIn("reference_mismatch_groups=1024", err)
        failures = [record for record in csv.reader(io.StringIO(err)) if record and record[0] == "REFERENCE_MISMATCH"]
        self.assertEqual(len(failures), 1024)
        self.assertEqual([int(record[4][1:]) for record in failures], list(range(1024)))
        reference_rows = [record for record in csv.DictReader(io.StringIO(self.text)) if record["sample_id"].startswith("C") and record["frm"] == "0"]
        for failure, original in zip(failures, reference_rows):
            self.assertEqual(failure[:6], ["REFERENCE_MISMATCH", "sin", "Nx", "1", original["sample_id"], "mismatch"])
            self.assertEqual(failure[6:], ["0x" + original["x_bits"], "", "0x" + original["ref_bits"]])

    def test_zero_sign_failure_has_complete_identity(self):
        output = io.StringIO(newline="")
        a.write_reference_failure(output, ("pow", "array", "2", "C42"),
                                  (0x8000000000000000, 0x4008000000000000, 0, 0x3ff0000000000000),
                                  "zero_sign_mismatch")
        self.assertEqual(next(csv.reader(io.StringIO(output.getvalue()))),
                         ["REFERENCE_MISMATCH", "pow", "array", "2", "C42", "zero_sign_mismatch",
                          "0x8000000000000000", "0x4008000000000000", "0x0000000000000000"])

    def test_zero_sign_reference_mismatch_causes_failure(self):
        actual = a.reference_status
        calls = iter(range(1025))
        def signs(bounds, sign, fmt, reference):
            return "zero_sign_mismatch" if next(calls) == 0 else actual(bounds, sign, fmt, reference)
        with mock.patch.object(a, "reference_status", side_effect=signs):
            rc, out, err = self.invoke(self.text)
        self.assertEqual(rc, 1)
        self.assertIn(",reference,zero_sign_mismatch,1", out)
        failures = [record for record in csv.reader(io.StringIO(err)) if record and record[0] == "REFERENCE_MISMATCH"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1:6], ["sin", "Nx", "1", "C0", "zero_sign_mismatch"])
        self.assertIn("reference_mismatch_groups=1", err)

    def test_gzip_open_path_uses_raw_identity(self):
        # In-memory gzip: no temporary file. The file SHA reader is mocked;
        # exercise the real gzip decoder and the CLI's .gz dispatch.
        compressed = gzip.compress(self.text.encode())
        def gzip_open(*args, **kwargs):
            return io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(compressed)), encoding="utf-8", newline="")
        args = ["fake.csv.gz", *self.args[1:]]
        with mock.patch.object(a.gzip, "open", side_effect=gzip_open), mock.patch.object(a, "digest_file", return_value="mock-sha"):
            rc, out, err = self.invoke("", args)
        self.assertEqual(rc, 0, err)
        self.assertIn("input_sha256=mock-sha", err)
        self.assertIn("corpus_C", out)


if __name__ == "__main__":
    unittest.main()
