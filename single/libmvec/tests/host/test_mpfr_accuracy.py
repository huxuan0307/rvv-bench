#!/usr/bin/env python3
"""Host-only tests: exact IEEE operations and MPFR interval comparisons."""
import contextlib
import csv
from fractions import Fraction
import gzip
import io
import os
from pathlib import Path
import random
import re
import struct
import unittest
from unittest import mock

import importlib.util
import sys
import tempfile
from unittest.mock import patch as _module_patch


def _load_script(name, relative):
    path = Path(__file__).resolve().parents[2] / 'script' / relative
    spec = importlib.util.spec_from_file_location('_libmvec_host_' + name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 检查需要临时模块登记，不修改 sys.path。
    with _module_patch.dict(sys.modules, {spec.name: module}):
        spec.loader.exec_module(module)
    return module

a = _load_script('mpfr_accuracy', 'accuracy/mpfr_accuracy.py')


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
        fixture = os.environ.get("LIBMVEC_PERF_ORACLE_DIR")
        if not fixture:
            raise unittest.SkipTest("set LIBMVEC_PERF_ORACLE_DIR to explicit frozen perf headers")
        cls.root = Path(fixture).resolve(strict=True)

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
        fixture = os.environ.get("LIBMVEC_PERF_ORACLE_DIR")
        if not fixture:
            raise unittest.SkipTest("set LIBMVEC_PERF_ORACLE_DIR to explicit frozen perf headers")
        cls.root = Path(fixture).resolve(strict=True)
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


class FakePointMPFR:
    """Minimal MPFR stand-in: returns scripted point_bounds results."""

    path = "fake-mpfr"
    version = "fake"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def point_bounds(self, function, x, z, fmt, precision):
        self.calls += 1
        return self.results[self.calls - 1]

    def get_emin(self):
        return -1073741823

    def get_emax(self):
        return 1073741823


class CertifyPointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.mpfr = a.MPFR()
        except (RuntimeError, OSError) as exc:
            raise unittest.SkipTest("host MPFR unavailable: %s" % exc)

    def certify(self, function, x, z, got, threshold, precisions=(512,), fmt=a.B64):
        return a.certify_point(self.mpfr, function, x, z, got, fmt,
                               precisions, Fraction(threshold))

    def test_frozen_quartet_rne_references(self):
        root = Path(__file__).resolve().parents[1] / 'accuracy' / 'oracles'
        oracles = a.load_oracles(root, 'unit', a.EXTRA_FUNCTIONS)
        fmt = a.B64
        for function, (first, second, reference) in oracles.items():
            self.assertEqual(len(reference), 1024)
            for index, (x, ref) in enumerate(zip(first, reference)):
                z = second[index] if second is not None else None
                with self.subTest(function=function, index=index, first=hex(x), second=z):
                    status, lo, hi, sign = self.mpfr.point_bounds(function, x, z, fmt, 512)
                    if status == 'math_nan':
                        # NaN sign/payload in the historical header is not a contract.
                        self.assertEqual(a.ieee_detailed_kind(ref, fmt), 'qnan')
                    elif status in ('math_zero', 'math_inf'):
                        magnitude = (fmt.expmask << fmt.frac) if status == 'math_inf' else 0
                        self.assertEqual(ref, magnitude | (fmt.sign if sign < 0 else 0))
                    else:
                        self.assertEqual(status, 'ok')
                        self.assertEqual(a.reference_status((lo, hi), (sign == -1,) * 2, fmt, ref),
                                         'match')

    def test_equal_threshold_passes(self):
        cert = self.certify("pow", 0x4000000000000000, 0xbff0000000000000,
                            0x3fe0000000000001, 1)
        # pow(2, -1) is exactly 0.5; a one-ULP error equals the threshold.
        self.assertEqual((cert.verdict, cert.upper), ("pass", Fraction(1)))
        self.assertEqual(cert.spacing, a.power2(-53))

    def test_encoding_step_distance_does_not_pass(self):
        step = a.power2(-52)
        truth = Fraction(1) + Fraction(3, 4) * step
        fake = FakePointMPFR([("ok", truth, truth, None)])
        # RNE(truth) is 1+1 step; got=1+2 steps is one encoding step from that
        # rounded reference but 1.25 ULP from the true value.
        cert = a.certify_point(fake, "log", 0x3ff0000000000000, None,
                               0x3ff0000000000002, a.B64, (256,), Fraction(1))
        self.assertEqual((cert.verdict, cert.upper), ("fail", Fraction(5, 4)))
        self.assertEqual(fake.calls, 1)

    def test_cross_precision_refines_to_pass(self):
        step = a.power2(-52)
        fake = FakePointMPFR([
            ("ok", Fraction(1) + step / 4, Fraction(1) + 3 * step / 2, None),
            ("ok", Fraction(1) + step / 4, Fraction(1) + 3 * step / 4, None),
        ])
        cert = a.certify_point(fake, "log", 0x3ff0000000000000, None,
                               0x3ff0000000000000, a.B64, (64, 128), Fraction(1))
        self.assertEqual((cert.verdict, cert.precision), ("pass", 128))
        self.assertEqual(cert.upper, Fraction(3, 4))
        self.assertEqual(fake.calls, 2)

    def test_log_special_values(self):
        qnan = 0x7ff8000000000000
        ninf = 0xfff0000000000000
        pinf = 0x7ff0000000000000
        one = 0x3ff0000000000000
        cases = [
            (0, ninf, "pass", "expected_inf"),                  # log(+0)
            (a.B64.sign, ninf, "pass", "expected_inf"),         # log(-0)
            (one, 0, "pass", "expected_zero"),                  # log(1) = +0
            (one, a.B64.sign, "fail", "expected_zero_mismatch"),
            (0xbff0000000000000, qnan, "pass", "expected_qnan"),  # log(-1)
            (ninf, qnan, "pass", "expected_qnan"),                # log(-Inf)
            (pinf, pinf, "pass", "expected_inf"),                 # log(+Inf)
            (qnan, qnan, "pass", "expected_qnan"),                # log(NaN)
        ]
        for x, got, verdict, category in cases:
            with self.subTest(x="%016x" % x, got="%016x" % got):
                cert = self.certify("log", x, None, got, 1)
                self.assertEqual((cert.verdict, cert.category), (verdict, category))
        bad = self.certify("log", 0x4000000000000000, None, pinf, 1)
        self.assertEqual((bad.verdict, bad.category),
                         ("fail", "finite_truth_nonfinite_output"))

    def test_atan2_signed_zero_and_pow_specials(self):
        qnan = 0x7ff8000000000000
        one = 0x3ff0000000000000
        self.assertEqual(self.certify("atan2", 0, 0, 0, 1).verdict, "pass")
        self.assertEqual(self.certify("atan2", 0, 0, a.B64.sign, 1).verdict, "fail")
        self.assertEqual(self.certify("atan2", a.B64.sign, 0, a.B64.sign, 1).verdict, "pass")
        self.assertEqual(self.certify("atan2", a.B64.sign, 0, 0, 1).verdict, "fail")
        # ISO C99 pow special cases come from MPFR, not a hand-written table.
        self.assertEqual(self.certify("pow", qnan, 0, one, 1).verdict, "pass")
        self.assertEqual(self.certify("pow", one, qnan, one, 1).verdict, "pass")
        snan = 0x7ff0000000000001
        for x, z in ((snan, 0), (one, snan)):
            self.assertEqual(self.certify("pow", x, z, qnan, 1).verdict, "pass")
            self.assertEqual(self.certify("pow", x, z, one, 1).verdict, "fail")

    def test_overflow_requires_rne_class_and_sign(self):
        inf = 0x7ff0000000000000
        for x in (a.round_rne_bits(Fraction(710), a.B64), a.B64.maxfinite):
            cert = self.certify("exp", x, None, inf, 1)
            self.assertEqual((cert.verdict, cert.category), ("pass", "rne_overflow"))
            for got in (a.B64.maxfinite, a.B64.sign | inf):
                self.assertEqual(self.certify("exp", x, None, got, 1).verdict, "fail")

    def test_tiny_finite_keeps_numerical_ulp(self):
        tiny = a.round_rne_bits(Fraction(-800), a.B64)  # exp(-800) -> +0 in f64 RNE
        self.assertEqual(self.certify("exp", tiny, None, 0, 1).verdict, "pass")
        self.assertEqual(self.certify("exp", tiny, None, 0, 0).verdict, "fail")
        self.assertEqual(self.certify("exp", tiny, None, a.B64.sign, 1).verdict, "fail")
        self.assertEqual(self.certify("exp", tiny, None, 1, 1).verdict, "pass")
        self.assertEqual(self.certify("exp", tiny, None, 1, Fraction(1, 2)).verdict, "fail")
        mpfr_tiny = a.B64.sign | a.B64.maxfinite  # Below MPFR's default emin too.
        cert = self.certify("exp", mpfr_tiny, None, 0, Fraction(1, 2))
        self.assertEqual((cert.verdict, cert.category), ("pass", "ulp"))
        self.assertGreater(cert.upper, 0)
        self.assertNotEqual(self.certify("exp", mpfr_tiny, None, 0, 0).verdict, "pass")

    def test_overflow_straddling_interval_refines(self):
        spacing = a.power2(971)
        midpoint = a.power2(1024) - spacing / 2
        fake = FakePointMPFR([("ok", midpoint - spacing / 4, midpoint + spacing / 4, 1),
                              ("ok", midpoint, midpoint, 1)])
        cert = a.certify_point(fake, "exp", 0, None, 0x7ff0000000000000,
                               a.B64, (64, 128), Fraction(1))
        self.assertEqual((cert.verdict, cert.category, cert.precision),
                         ("pass", "rne_overflow", 128))

    def test_one_encoding_step_can_exceed_one_true_ulp(self):
        one = 0x3ff0000000000000
        truth = 1 - a.power2(-53) / 4
        self.assertEqual(a.round_rne_bits(truth, a.B64), one)
        fake = FakePointMPFR([("ok", truth, truth, 1)])
        cert = a.certify_point(fake, "exp", 0, None, one + 1, a.B64,
                               (512,), Fraction(1))
        self.assertEqual((cert.verdict, cert.upper, cert.lower),
                         ("fail", Fraction(9, 4), Fraction(9, 4)))

    def test_other_function_and_binary32_specials(self):
        qnan = 0x7ff8000000000000
        pinf = 0x7ff0000000000000
        ninf = 0xfff0000000000000
        one = 0x3ff0000000000000
        self.assertEqual(self.certify("sin", 0, None, 0, 1).category, "expected_zero")
        self.assertEqual(self.certify("sin", a.B64.sign, None, a.B64.sign, 1).category,
                         "expected_zero")
        self.assertEqual(self.certify("sin", pinf, None, qnan, 1).category, "expected_qnan")
        self.assertEqual(self.certify("cos", 0, None, one, 1).category, "ulp")
        self.assertEqual(self.certify("exp", ninf, None, 0, 1).category, "expected_zero")
        self.assertEqual(self.certify("exp", pinf, None, pinf, 1).category, "expected_inf")
        # binary32 path: same policy, 8-hex input/output fields.
        self.assertEqual(self.certify("logf", 0, None, 0xff800000, 1,
                                      fmt=a.B32).category, "expected_inf")
        self.assertEqual(self.certify("logf", 0x3f800000, None, 0, 1,
                                      fmt=a.B32).category, "expected_zero")
        self.assertEqual(self.certify("logf", 0xbf800000, None, 0x7fc00000, 1,
                                      fmt=a.B32).category, "expected_qnan")

    def test_cross_binade_uses_conservative_spacing(self):
        # Truth interval spans 1.0: spacing below is 2**-53, above 2**-52.
        below = a.power2(-53)
        fake = FakePointMPFR([("ok", Fraction(1) - below / 4,
                               Fraction(1) + below, None)])
        cert = a.certify_point(fake, "log", 0x3ff0000000000000, None,
                               0x3ff0000000000000, a.B64, (256,), Fraction(1))
        # |1 - truth| <= one smaller-spacing ULP, so the threshold is met even
        # though the truth interval crosses the binade boundary.
        self.assertEqual((cert.verdict, cert.upper), ("pass", Fraction(1)))
        self.assertEqual(cert.spacing, below)

    def test_gate_corpus_index_mapping(self):
        self.assertEqual(a.gate_corpus_index(0, 1024, 2), 0)
        self.assertEqual(a.gate_corpus_index(1, 1024, 2), 512)
        with self.assertRaises(ValueError):
            a.gate_corpus_index(0, 1024, 0)
        with self.assertRaises(ValueError):
            a.gate_corpus_index(2, 1024, 2)


class GenerateHeadersTest(unittest.TestCase):
    """Host-only checks for the quartet header generator in mpfr_accuracy.py."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.mpfr = a.MPFR()
        except (RuntimeError, OSError) as exc:
            raise unittest.SkipTest("host MPFR unavailable: %s" % exc)

    def setUp(self):
        base = os.environ.get("TEST_TMPDIR")
        if base and "build" in Path(base).resolve().parts:
            raise AssertionError("TEST_TMPDIR must not be under a build path")
        self.tmp = tempfile.TemporaryDirectory(prefix="mpfr-gen-", dir=base)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_rows(self, rows, name="corpus.csv", fieldnames=None):
        path = self.root / name
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames or a.CORPUS_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_raw(self, text, name="corpus.csv"):
        path = self.root / name
        path.write_text(text)
        return path

    def array(self, text, name):
        match = re.search(r"static const uint64_t\s+" + name + r"\[[^\]]+\]\s*=\s*\{(.*?)\};", text, re.S)
        self.assertIsNotNone(match, name)
        return [int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]+)ULL", match.group(1))]

    def rows(self):
        return [
            dict(function="atan", idx="0", **{"class": "special"}, y_bits="",
                 x_bits="0000000000000000"),
            dict(function="atan", idx="1", **{"class": "random"}, y_bits="",
                 x_bits="3ff0000000000000"),
            dict(function="atan", idx="2", **{"class": "special"}, y_bits="",
                 x_bits="7ff0000000000000"),
            dict(function="atan2", idx="0", **{"class": "special"},
                 y_bits="0000000000000000", x_bits="bff0000000000000"),
            dict(function="atan2", idx="1", **{"class": "random"},
                 y_bits="3ff0000000000000", x_bits="4000000000000000"),
        ]

    def test_format_selection_and_atan2_operand_order(self):
        corpus = self.write_rows(self.rows())
        out = self.root / "headers"
        results = a.generate_frozen_headers(corpus, out, count=2,
                                            functions=("atan", "atan2"),
                                            precisions=(512, 1024),
                                            mpfr=self.mpfr)
        self.assertEqual([name for name, _ in results.items()], ["atan", "atan2"])
        atan_path, atan2_path = out / "atan.h", out / "atan2.h"
        self.assertTrue(atan_path.is_file() and atan2_path.is_file())
        atan_text = atan_path.read_text(encoding="ascii")
        atan2_text = atan2_path.read_text(encoding="ascii")
        self.assertIn("Generated by gen_frozen_headers.py; do not edit by hand.", atan_text)
        self.assertIn("#define DATA_COUNT 2", atan_text)
        # Special rows come first in CSV order, then random rows.
        self.assertEqual(self.array(atan_text, "input_bits"),
                         [0x0000000000000000, 0x7ff0000000000000])
        self.assertEqual(self.array(atan_text, "reference_bits"),
                         [0x0000000000000000, 0x3ff921fb54442d18])
        # atan2 input_bits is y; second_bits is x.  The first special is
        # atan2(+0, -1) == +pi, not atan2(-1, +0) == -pi/2.
        self.assertEqual(self.array(atan2_text, "input_bits"),
                         [0x0000000000000000, 0x3ff0000000000000])
        self.assertEqual(self.array(atan2_text, "second_bits"),
                         [0xbff0000000000000, 0x4000000000000000])
        references = self.array(atan2_text, "reference_bits")
        self.assertEqual(references[0], 0x400921fb54442d18)
        self.assertNotEqual(references[0], 0xbff921fb54442d18)
        self.assertEqual(results["atan"][0], 2)
        self.assertEqual(results["atan2"][0], 2)
        self.assertEqual(results["atan"][1], a.digest_file(atan_path))
        self.assertEqual(results["atan2"][1], a.digest_file(atan2_path))

    def test_reference_refines_until_directed_endpoints_agree(self):
        class Refining:
            calls = 0

            def point_bounds(self, function, first, second, fmt, precision):
                self.calls += 1
                if self.calls == 1:
                    return ("ok", Fraction(1), Fraction(1) + Fraction(5, 1 << 53), None)
                return ("ok", Fraction(1), Fraction(1) + Fraction(1, 1 << 53), None)

        fake = Refining()
        bits = a.rne_point_bits(fake, "atan", 0, None, a.B64, (512, 1024))
        self.assertEqual(bits, 0x3ff0000000000000)
        self.assertEqual(fake.calls, 2)

    def test_special_reference_representation(self):
        corpus = self.write_rows([
            dict(function="atan", idx="0", **{"class": "special"}, y_bits="",
                 x_bits="7ff8000000000000"),
            dict(function="atan", idx="1", **{"class": "special"}, y_bits="",
                 x_bits="8000000000000000"),
        ])
        out = self.root / "special-headers"
        a.generate_frozen_headers(corpus, out, count=2, functions=("atan",),
                                  precisions=(512,), mpfr=self.mpfr)
        references = self.array((out / "atan.h").read_text(encoding="ascii"),
                                "reference_bits")
        # NaN sign is not a mathematical contract, but the historical quartet
        # headers use negative quiet NaN; signed zero is preserved exactly.
        self.assertEqual(references, [0xfff8000000000000, 0x8000000000000000])

    def test_invalid_corpus_is_rejected_before_output(self):
        out = self.root / "headers"
        wrong_header = self.write_raw("function,idx,class,y_bits\natan,0,special,0\n")
        with self.assertRaises(ValueError):
            a.generate_frozen_headers(wrong_header, out, count=1, functions=("atan",),
                                      mpfr=self.mpfr)
        self.assertFalse(out.exists())
        rows = self.rows()
        variants = []
        variants.append([dict(rows[0], function="sin")])
        variants.append([dict(rows[0], **{"class": "unknown"})])
        variants.append([dict(rows[0], x_bits="0x00000000000000000")])
        variants.append([rows[0], rows[0]])
        variants.append([dict(rows[3], y_bits="")])
        for index, bad_rows in enumerate(variants):
            with self.subTest(index=index):
                path = self.write_rows(bad_rows, name=f"bad-{index}.csv")
                with self.assertRaises(ValueError):
                    a.generate_frozen_headers(path, out, count=1, functions=("atan",),
                                              mpfr=self.mpfr)
        good = self.write_rows(self.rows())
        with self.assertRaises(ValueError):
            a.generate_frozen_headers(good, out, count=99, functions=("atan",),
                                      mpfr=self.mpfr)
        self.assertFalse(out.exists())

    def test_generation_does_not_overwrite_existing_headers(self):
        corpus = self.write_rows(self.rows())
        out = self.root / "headers"
        out.mkdir()
        pinned = out / "atan.h"
        pinned.write_bytes(b"pinned\n")
        with self.assertRaises(FileExistsError):
            a.generate_frozen_headers(corpus, out, count=1, functions=("atan",),
                                      mpfr=self.mpfr)
        self.assertEqual(pinned.read_bytes(), b"pinned\n")

    def test_ambiguous_reference_fails_closed_without_files(self):
        class Ambiguous:
            def point_bounds(self, function, first, second, fmt, precision):
                return ("ok", Fraction(1), Fraction(1) + Fraction(5, 1 << 53), None)

        good = self.write_rows([dict(self.rows()[1])])
        out = self.root / "ambiguous"
        with self.assertRaisesRegex(RuntimeError, "ambiguous RNE reference"):
            a.generate_frozen_headers(good, out, count=1, functions=("atan",),
                                      precisions=(512, 1024), mpfr=Ambiguous())
        self.assertFalse(out.exists())

    def test_cli_uses_explicit_inputs_and_reports_hash(self):
        corpus = self.write_rows([dict(self.rows()[1])])
        out = self.root / "cli-headers"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = a.main(["--inputs", str(corpus), "--outdir", str(out),
                         "--count", "1", "--functions", "atan",
                         "--precisions", "512,1024"])
        self.assertEqual(rc, 0, stderr.getvalue())
        self.assertIn("atan.h count=1 sha256=", stdout.getvalue())
        self.assertIn("does not regenerate the historical corpus", stderr.getvalue())
        path = out / "atan.h"
        self.assertTrue(path.is_file())
        self.assertIn("Generated by gen_frozen_headers.py; do not edit by hand.",
                      path.read_text(encoding="ascii"))
        self.assertEqual(self.array(path.read_text(encoding="ascii"), "reference_bits"),
                         [0x3fe921fb54442d18])


class GateParserTest(unittest.TestCase):
    def csv_rows(self, modes=("RNE",), sample_id="C0"):
        return [dict(function="sin", interface="Nx", lmul="1",
                     sample_id=sample_id, frm=mode,
                     x_bits="403a1ce76a5bc65c", z_bits="",
                     y_bits="3fea942fd12cb9c5", ref_bits="3fea942fd12cb9c5")
                for mode in modes]

    def parse(self, rows, expected, modes=("RNE",)):
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=a.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return list(a.groups(csv.DictReader(io.StringIO(out.getvalue())),
                             expected, modes=modes))

    def test_rne_only_and_missing_rne(self):
        expected = {("sin", "Nx", "1"): 1}
        self.assertEqual(len(self.parse(self.csv_rows(), expected)), 1)
        with self.assertRaisesRegex(ValueError, "not expected in this run"):
            self.parse(self.csv_rows(("RTZ",)), expected)
        with self.assertRaisesRegex(ValueError, "expected 2 corpus IDs"):
            self.parse(self.csv_rows(), {("sin", "Nx", "1"): 2})


class GateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def make_rows(self, samples, got_bits=0x3ff0000000000000):
        rows = []
        for i in range(samples):
            rows.append(dict(function="atan", interface="Nx", lmul="1",
                             sample_id="C%d" % i, frm="RNE",
                             x_bits="3ff0000000000000", z_bits="",
                             y_bits="3ff0000000000000", ref_bits="3ff0000000000000"))
        rows.append(dict(function="atan", interface="Nx", lmul="1",
                         sample_id="S0:0", frm="RNE", x_bits="3ff0000000000000",
                         z_bits="", y_bits="%016x" % got_bits, ref_bits=""))
        return rows

    def invoke(self, rows, samples=2, extra=None, fake=None):
        out, err = io.StringIO(), io.StringIO()
        total = a.UNIT["atan"][1]
        arrays = {"atan": ([0x3ff0000000000000] * total, None,
                           [0x3ff0000000000000] * total)}
        fake = fake or FakePointMPFR([("ok", Fraction(1), Fraction(1), None)] * 100)
        args = ["-", "--oracle-dir", self.tmp.name, "--profile", "unit",
                "--functions", "atan", "--interfaces", "Nx", "--lmuls", "1",
                "--precisions", "256", "--samples", str(samples),
                "--max-ulp", "1"]
        if extra:
            args += extra
        with mock.patch.object(a, "load_oracles", return_value=arrays), \
             mock.patch.object(a, "MPFR", return_value=fake), \
             mock.patch.object(sys, "stdin", io.StringIO(csv_text(rows))), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = a.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_gate_summary_schema_and_exit(self):
        rc, out, err = self.invoke(self.make_rows(2))
        self.assertEqual(rc, 0, err)
        table = list(csv.DictReader(io.StringIO(out)))
        self.assertEqual(tuple(table[0].keys()), a.GATE_FIELDS)
        corpus = [r for r in table if r["sample_kind"] == "corpus_C"][0]
        semantic = [r for r in table if r["sample_kind"] == "semantic_S_unvalidated"][0]
        self.assertEqual((corpus["samples"], corpus["pass"], corpus["fail"],
                          corpus["unresolved"], corpus["status"]),
                         ("2", "2", "0", "0", "pass"))
        self.assertEqual(corpus["threshold_ulp"], "1")
        self.assertEqual(corpus["frozen_ref_equal"], "2")
        self.assertEqual(semantic["samples"], "1")
        self.assertEqual(semantic["pass"], "1")
        self.assertIn("# gate:", err)

    def test_gate_fail_is_nonzero(self):
        fake = FakePointMPFR([("ok", Fraction(2), Fraction(2), None)] * 100)
        rc, out, err = self.invoke(self.make_rows(2), fake=fake)
        self.assertEqual(rc, 1)
        table = list(csv.DictReader(io.StringIO(out)))
        corpus = [r for r in table if r["sample_kind"] == "corpus_C"][0]
        self.assertEqual((corpus["fail"], corpus["status"]), ("2", "fail"))
        self.assertEqual(corpus["worst_sample_id"], "C0")
        self.assertTrue(corpus["worst_upper_ulp"])
        self.assertIn("GATE_NONPASS", err)

    def test_receipt_requires_bound_lanes_and_functions(self):
        root = Path(self.tmp.name)
        lanes = root / "lanes.csv.gz"
        lanes.write_bytes(gzip.compress(csv_text(self.make_rows(2)).encode()))
        sha = a.digest_file(lanes)
        complete = root / "COMPLETE.csv"
        complete.write_text(f"role,value\nrun_role,candidate\nfunctions,atan\nlanes_sha256,{sha}\n")
        self.assertEqual(a.capture_identity(complete, str(lanes), sha, ("atan",)),
                         a.digest_file(complete))
        for path, observed, functions in (("-", sha, ("atan",)),
                                           (str(lanes), "0"*64, ("atan",)),
                                           (str(lanes), sha, ("acos",))):
            with self.subTest(path=path, observed=observed, functions=functions), self.assertRaises(ValueError):
                a.capture_identity(complete, path, observed, functions)

    def test_gate_exact_threshold_and_invalid_fraction_exit(self):
        rc, out, err = self.invoke(self.make_rows(2), extra=["--max-ulp", "3.5"])
        self.assertEqual(rc, 0, err)
        self.assertEqual({r['threshold_ulp'] for r in csv.DictReader(io.StringIO(out))}, {"7/2"})
        rc, out, err = self.invoke(self.make_rows(2), extra=["--max-ulp", "1/0"])
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("mpfr_accuracy:", err)

    def test_gate_coverage_and_flag_validation(self):
        rc, out, err = self.invoke(self.make_rows(2), extra=["--interfaces", "Nx,array"])
        self.assertEqual(rc, 2)
        self.assertIn("missing expected scope", err)
        rc, out, err = self.invoke(self.make_rows(2), samples=2000)
        self.assertEqual(rc, 2)
        self.assertIn("exceeds frozen total", err)
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            a.main(["-", "--oracle-dir", self.tmp.name, "--profile", "unit",
                    "--functions", "atan", "--samples", "2"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
