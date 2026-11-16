#!/usr/bin/env python3
"""Compare observed rounding modes to MPFR-enclosed mathematical truth.

Input CSV (file or standard input) has the header
function,interface,lmul,sample_id,frm,x_bits,z_bits,y_bits,ref_bits. Each
(function,interface,lmul,sample_id) group must consist of five consecutive
rows, one per frm; all IDs in each (function,interface,lmul) block must be
contiguous. ref_bits is optional per group; unary z_bits must be empty. Bit
fields use exactly 8/16 hexadecimal digits, with an optional 0x prefix. A
final optional rne_ref_ulp column is validated but never used as truth.

Only host MPFR, Python's standard library, and the actual observed output bits
are used. No target execution, temporary file, or Python binary float is used.
The ctypes ABI is restricted to the validated standard LP64 MPFR/GMP ABI;
--mpfr-library is not a promise that arbitrary builds or ABIs are compatible.
The output is CSV on stdout; metadata and every reference mismatch go to stderr.
Set PYTHONDONTWRITEBYTECODE or run python3 -B in read-only environments.
"""
from __future__ import annotations

import argparse
import collections
import csv
import ctypes
import ctypes.util
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import gzip
import re
import sys
from pathlib import Path

FIELDS = ("function", "interface", "lmul", "sample_id", "frm", "x_bits", "z_bits", "y_bits", "ref_bits")
MODES = ("RNE", "RTZ", "RDN", "RUP", "RMM")
FUNCTIONS = ("sin", "cos", "log", "logf", "exp", "expf", "pow")
# Frozen experiment identities, not inputs inferred from the current directory.
UNIT = {
    "sin": ("trigvec-unit-data.h", 103084, "247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d", "trig_unit_input", None, "trig_unit_sin"),
    "cos": ("trigvec-unit-data.h", 103084, "247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d", "trig_unit_input", None, "trig_unit_cos"),
    "log": ("log-unit.h", 104965, "e07f8f882e82a1000fe2e785199bf0ebba2872e1f6a900decb0ccd2217b5e208", "unit_input", None, "unit_reference"),
    "logf": ("logf-unit.h", 101385, "f5f37d8873c667f8e3a0c8c3576c1c93535695b04069ee352d6f16fbbe7ce1d1", "unit_input", None, "unit_reference"),
    "exp": ("exp-unit.h", 102039, "86b1414a952b04f5e30f62ba78e441dfc11b51f83a3fa65889c84e354cf64274", "unit_input", None, "unit_reference"),
    "expf": ("expf-unit.h", 100608, "d70e24909b2b1d2203e5d382b9643a427f90cc58f2747232b0fc81b334538106", "unit_input", None, "unit_reference"),
    "pow": ("powvec_unit_data.h", 124238, "a4dbbb9daea603282f8f4df2734cf18e1075fc5adaff02c2a4fb7e5ff187898e", "powvec_unit_x", "powvec_unit_z", "powvec_unit_reference"),
}
PERF_HASH = {
    "sin": "b11fcecdf5ff37e6c480a7a7fe2d749c2e15884bd36b3e82bba9b91d32f5090e",
    "cos": "7e15440503cf2390d746f22770934be6b2c1fd18abe7d2308fa4bc19649ad82f",
    "log": "082b728a147dc0aa8a954fc9dcd4b8c9786691240f2a669c1d3a2248a966c5b3",
    "logf": "e4882a26a71fc1ede17d0fce2bd4dd7de066e0905d6f3e4ae2a28e696b8a3930",
    "exp": "ff3a201b449ae48cb1042075245e9d356602c255aecf34d4d31b4ad802115f25",
    "expf": "27e6ab96bab056ba8306820143465bf84d38ebe0d596a098c8543d9add598c7e",
    "pow": "209978106882fd781cf10e83f003d1bb644600e2460e607597bdb21f05a59978",
}
NUMERICAL_CATEGORIES = ("normal_range", "subnormal_range", "below_minsubnormal", "cross_minsubnormal", "exact_zero")
RNDD, RNDU, RNDN = 3, 2, 0  # MPFR's enum, NOT RISC-V frm encodings.


@dataclass(frozen=True)
class Format:
    width: int
    frac: int
    expbits: int
    bias: int

    @property
    def sign(self):
        return 1 << (self.width - 1)

    @property
    def expmask(self):
        return (1 << self.expbits) - 1

    @property
    def maxfinite(self):
        return ((self.expmask - 1) << self.frac) | ((1 << self.frac) - 1)

    @property
    def minnormal(self):
        return power2(1 - self.bias)

    @property
    def minsubnormal(self):
        return power2(1 - self.bias - self.frac)


B32 = Format(32, 23, 8, 127)
B64 = Format(64, 52, 11, 1023)


def power2(exponent):
    return Fraction(1 << exponent, 1) if exponent >= 0 else Fraction(1, 1 << -exponent)


def ieee_kind(bits, fmt):
    exp = (bits >> fmt.frac) & fmt.expmask
    mant = bits & ((1 << fmt.frac) - 1)
    if exp == fmt.expmask:
        return "nan" if mant else "inf"
    if exp == 0:
        return "subnormal" if mant else "zero"
    return "normal"


def ieee_detailed_kind(bits, fmt):
    kind = ieee_kind(bits, fmt)
    if kind == "nan":
        quiet = bool(bits & (1 << (fmt.frac - 1)))
        return "qnan" if quiet else "snan"
    if kind in ("zero", "inf", "subnormal"):
        return ("negative_" if bits & fmt.sign else "positive_") + kind
    return "negative_normal" if bits & fmt.sign else "positive_normal"


def ieee_fraction(bits, fmt):
    """Exact real represented by a finite IEEE bit pattern; zero loses its sign."""
    exp = (bits >> fmt.frac) & fmt.expmask
    mant = bits & ((1 << fmt.frac) - 1)
    if exp == fmt.expmask:
        raise ValueError("NaN or infinity has no finite fraction")
    if exp:
        mant |= 1 << fmt.frac
        exponent = exp - fmt.bias - fmt.frac
    else:
        exponent = 1 - fmt.bias - fmt.frac
    if bits & fmt.sign:
        mant = -mant
    return mant * power2(exponent)


def nearest_integer_even(n, d):
    q, r = divmod(n, d)
    twice = 2 * r
    return q + (twice > d or (twice == d and q & 1))


def floor_log2_fraction(value):
    assert value > 0
    n, d = value.numerator, value.denominator
    e = n.bit_length() - d.bit_length()
    return e if value >= power2(e) else e - 1


def round_rne_bits(value, fmt, zero_negative=False):
    """Round an exact Fraction to IEEE RNE, including overflow and subnormals."""
    sign = fmt.sign if value < 0 or (not value and zero_negative) else 0
    value = abs(value)
    if not value:
        return sign
    e = floor_log2_fraction(value)
    emin = 1 - fmt.bias
    emax = fmt.bias
    quantum = power2(max(e, emin) - fmt.frac)
    scaled = value / quantum
    q = nearest_integer_even(scaled.numerator, scaled.denominator)
    if e < emin:
        if q < (1 << fmt.frac):
            return sign | q
        return sign | (1 << fmt.frac)
    if q == 1 << (fmt.frac + 1):
        e += 1
        q >>= 1
    if e > emax:
        return sign | (fmt.expmask << fmt.frac)
    return sign | ((e + fmt.bias) << fmt.frac) | (q - (1 << fmt.frac))


class MpfrStruct(ctypes.Structure):
    # MPFR 4 ABI on this LP64 host: mpfr_prec_t/mpfr_exp_t are long,
    # mpfr_sign_t is int, and the final member is an mp_limb_t pointer.
    _fields_ = [("prec", ctypes.c_long), ("sign", ctypes.c_int),
                ("exp", ctypes.c_long), ("limbs", ctypes.c_void_p)]


class MPFR:
    def __init__(self, library=None):
        path = library or ctypes.util.find_library("mpfr")
        if not path:
            raise RuntimeError("libmpfr was not found")
        self.path = path
        self.lib = ctypes.CDLL(path)
        p = ctypes.POINTER(MpfrStruct)
        long, integer = ctypes.c_long, ctypes.c_int

        def bind(name, args, result):
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
            return function

        self.init = bind("mpfr_init2", [p, long], None)
        self.clear = bind("mpfr_clear", [p], None)
        self.set_str = bind("mpfr_set_str", [p, ctypes.c_char_p, integer, integer], integer)
        self.mul_2si = bind("mpfr_mul_2si", [p, p, long, integer], integer)
        self.set_zero = bind("mpfr_set_zero", [p, integer], None)
        self.get_str = bind("mpfr_get_str", [ctypes.c_void_p, ctypes.POINTER(long), integer,
                                            ctypes.c_size_t, p, integer], ctypes.c_void_p)
        self.free_str = bind("mpfr_free_str", [ctypes.c_void_p], None)
        self.nan_p = bind("mpfr_nan_p", [p], integer)
        self.inf_p = bind("mpfr_inf_p", [p], integer)
        self.zero_p = bind("mpfr_zero_p", [p], integer)
        self.signbit = bind("mpfr_signbit", [p], integer)
        self.get_exp = bind("mpfr_get_exp", [p], long)
        self.clear_flags = bind("mpfr_clear_flags", [], None)
        self.underflow_p = bind("mpfr_underflow_p", [], integer)
        self.overflow_p = bind("mpfr_overflow_p", [], integer)
        self.get_emin = bind("mpfr_get_emin", [], long)
        self.get_emax = bind("mpfr_get_emax", [], long)
        get_version = bind("mpfr_get_version", [], ctypes.c_char_p)
        self.version = get_version().decode("ascii")
        self.ops = {}
        for name in FUNCTIONS:
            symbol = "exp" if name == "expf" else "log" if name == "logf" else name
            args = [p, p, p, integer] if name == "pow" else [p, p, integer]
            self.ops[name] = bind("mpfr_" + symbol, args, integer)
        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(long) != 8:
            raise RuntimeError("ctypes MPFR binding requires an LP64 host")

    def new(self, precision):
        result = MpfrStruct()
        self.init(ctypes.byref(result), precision)
        return result

    def set_ieee(self, target, bits, fmt):
        exp = (bits >> fmt.frac) & fmt.expmask
        mant = bits & ((1 << fmt.frac) - 1)
        if exp == fmt.expmask:
            raise ValueError("set_ieee only accepts finite inputs")
        if not exp and not mant:
            self.set_zero(ctypes.byref(target), -1 if bits & fmt.sign else 1)
            return
        if exp:
            mant |= 1 << fmt.frac
            exponent = exp - fmt.bias - fmt.frac
        else:
            exponent = 1 - fmt.bias - fmt.frac
        if bits & fmt.sign:
            mant = -mant
        # set_str returns 0 on successful parsing, not exactness. The input
        # integer has at most 53 significant bits; target.prec is >=64, so
        # it is exactly representable regardless of the rounding direction.
        if target.prec < 64 or abs(mant).bit_length() > target.prec:
            raise RuntimeError("insufficient precision for exact input integer")
        if self.set_str(ctypes.byref(target), str(mant).encode("ascii"), 10, RNDN):
            raise RuntimeError("input integer string could not be parsed")
        if self.mul_2si(ctypes.byref(target), ctypes.byref(target), exponent, RNDN):
            raise RuntimeError("input scaling was not represented exactly")

    def as_fraction(self, value):
        if self.nan_p(ctypes.byref(value)) or self.inf_p(ctypes.byref(value)):
            raise ValueError("non-finite MPFR endpoint")
        if self.zero_p(ctypes.byref(value)):
            return Fraction(0)
        exponent = ctypes.c_long()
        ptr = self.get_str(None, ctypes.byref(exponent), 16, 0, ctypes.byref(value), RNDN)
        if not ptr:
            raise MemoryError("mpfr_get_str failed")
        try:
            digits = ctypes.string_at(ptr).decode("ascii")
        finally:
            self.free_str(ptr)
        negative = digits.startswith("-")
        digits = digits.lstrip("-")
        mant = int(digits, 16)
        if negative:
            mant = -mant
        # mpfr_get_str returns 0.<digits>_16 * 16**exponent exactly
        # for a power-of-two base and n=0 (enough digits to recover precision).
        return mant * power2(4 * (exponent.value - len(digits)))

    def bounds(self, function, xbits, zbits, fmt, precision):
        """Return ((lo,hi), signs, status); MPFR range flags are never ignored."""
        x, z, lower, upper = [self.new(precision) for _ in range(4)]
        values = (x, z, lower, upper)
        try:
            self.set_ieee(x, xbits, fmt)
            if function == "pow":
                self.set_ieee(z, zbits, fmt)
            results, flags = [], []
            for out, rnd in ((lower, RNDD), (upper, RNDU)):
                self.clear_flags()
                if function == "pow":
                    ternary = self.ops[function](ctypes.byref(out), ctypes.byref(x), ctypes.byref(z), rnd)
                else:
                    ternary = self.ops[function](ctypes.byref(out), ctypes.byref(x), rnd)
                flags.append((bool(self.underflow_p()), bool(self.overflow_p())))
                results.append(ternary)
            if any(f[0] for f in flags):
                return None, None, "mpfr_underflow"
            if any(f[1] for f in flags):
                return None, None, "mpfr_overflow"
            if self.nan_p(ctypes.byref(lower)) or self.nan_p(ctypes.byref(upper)):
                return None, None, "math_nan"
            if self.inf_p(ctypes.byref(lower)) or self.inf_p(ctypes.byref(upper)):
                return None, None, "math_inf"
            # MPFR can represent values far beyond IEEE, with exponents near
            # 2**30. Never materialize a Python integer with that many bits.
            # e > 4096 proves |v| > max finite binary64; e < -4096
            # proves |v| < minimum subnormal. These limits are conservative
            # for binary32 too. Mixed, opposite-sign or zero endpoints remain
            # unresolved; an exponent outside this bound never becomes a
            # Python shift of billions of bits.
            nonzero = [v for v in (lower, upper) if not self.zero_p(ctypes.byref(v))]
            exponents = [self.get_exp(ctypes.byref(v)) for v in nonzero]
            same_sign = (len(nonzero) == 2 and
                         bool(self.signbit(ctypes.byref(lower))) == bool(self.signbit(ctypes.byref(upper))))
            if exponents and max(exponents) > 4096:
                return None, None, ("extreme_large_guard" if same_sign and min(exponents) > 4096 else "cross_extreme_guard")
            if exponents and min(exponents) < -4096:
                return None, None, ("extreme_tiny_guard" if same_sign and max(exponents) < -4096 else "cross_extreme_guard")
            lo, hi = self.as_fraction(lower), self.as_fraction(upper)
            if lo > hi:
                raise RuntimeError("MPFR lower bound exceeds upper bound")
            signs = (bool(self.signbit(ctypes.byref(lower))), bool(self.signbit(ctypes.byref(upper))))
            return (lo, hi), signs, "ok"
        finally:
            for value in values:
                self.clear(ctypes.byref(value))


def target_class(bounds, fmt):
    lo, hi = bounds
    if lo == hi == 0:
        return "exact_zero"
    if lo <= 0 <= hi:
        return "cross_zero"
    abslo, abshi = (lo, hi) if lo > 0 else (-hi, -lo)
    maximum = ieee_fraction(fmt.maxfinite, fmt)
    if abslo > maximum:
        return "outside_target_range"
    if abshi > maximum:
        return "cross_target_range"
    if abshi < fmt.minsubnormal:
        return "below_minsubnormal"
    if abslo < fmt.minsubnormal <= abshi:
        return "cross_minsubnormal"
    if abshi < fmt.minnormal:
        return "subnormal_range"
    if abslo < fmt.minnormal <= abshi:
        return "cross_minnormal"
    return "normal_range"


def compare_closeness(a, b, bounds):
    """Prove whether a is closer than b to every real in [lo,hi]."""
    lo, hi = bounds
    if a == b:
        return "tie"
    midpoint = (a + b) / 2
    if lo == hi == midpoint:
        return "tie"
    if lo <= midpoint <= hi:
        return "unresolved"
    a_is_lower = a < b
    a_wins = (hi < midpoint) if a_is_lower else (lo > midpoint)
    return "closer" if a_wins else "worse"


def reference_status(bounds, signs, fmt, reference):
    if reference is None:
        return "absent"
    lo, hi = bounds
    left = round_rne_bits(lo, fmt, signs[0])
    right = round_rne_bits(hi, fmt, signs[1])
    if left != right:
        if left & ~fmt.sign == 0 and right & ~fmt.sign == 0:
            return "zero_sign_unresolved"
        return "unresolved"
    if left == reference:
        return "match"
    if left & ~fmt.sign == 0 and reference & ~fmt.sign == 0:
        return "zero_sign_mismatch"
    return "mismatch"


def ulp_error_bin(value, bounds, fmt):
    """A rigorously enclosed |output-truth|/spacing histogram category.

    Spacing is 2**(floor(log2(abs(truth)))-fracbits) for a normal
    true result, and minsubnormal for a subnormal true result. Boundaries
    use closed bins, assigning a value exactly at a cut to the lower bin.
    A truth interval touching two binades (even on an endpoint) is not
    assigned a spacing. A lower/upper error crossing a cut is unresolved.
    """
    lo, hi = bounds
    if lo == hi == 0:
        spacing = fmt.minsubnormal
    elif lo <= 0 <= hi:
        return "cross_zero"
    else:
        abslo, abshi = (lo, hi) if lo > 0 else (-hi, -lo)
        if abslo >= fmt.minnormal:
            elo, ehi = floor_log2_fraction(abslo), floor_log2_fraction(abshi)
            if elo != ehi:
                return "cross_binade"
            spacing = power2(elo - fmt.frac)
        elif abshi < fmt.minnormal:
            spacing = fmt.minsubnormal
        else:
            return "cross_binade"
    upper = max(abs(value - lo), abs(value - hi)) / spacing
    lower = (Fraction(0) if lo <= value <= hi else min(abs(value - lo), abs(value - hi))) / spacing
    if upper == 0:
        return "zero"
    cuts = ((Fraction(1, 2), "le_0.5"), (Fraction(1), "le_1"),
            (Fraction(2), "le_2"), (Fraction(4), "le_4"))
    previous = Fraction(0)
    for threshold, label in cuts:
        if lower > previous and upper <= threshold:
            return label
        previous = threshold
    if lower > 4:
        return "gt_4"
    return "unresolved"


def parse_bits(text, fmt, field, optional=False):
    if text == "" and optional:
        return None
    pattern = rf"(?:0[xX])?[0-9a-fA-F]{{{fmt.width // 4}}}"
    if not re.fullmatch(pattern, text):
        raise ValueError(f"{field} requires exactly {fmt.width // 4} hexadecimal digits")
    return int(text, 16)


def parse_mode(text):
    if text in ("0", "1", "2", "3", "4"):
        return MODES[int(text)]
    text = text.upper()
    if text not in MODES:
        raise ValueError("frm must be 0..4 or RNE/RTZ/RDN/RUP/RMM")
    return text


def groups(reader, expected):
    """Stream groups; reject gaps, duplicate IDs, missing/reopened blocks."""
    header = tuple(reader.fieldnames or ())
    if header not in (FIELDS, FIELDS + ("rne_ref_ulp",)):
        raise ValueError("CSV header must be " + ",".join(FIELDS) + " and optional rne_ref_ulp")
    seen_ids, closed_scopes = set(), set()
    current, scope, rows, corpus_count = None, None, {}, 0

    def check_block(block, count):
        if count != expected[block]:
            raise ValueError(f"scope {block}: expected {expected[block]} corpus IDs, got {count}")

    for lineno, row in enumerate(reader, 2):
        if None in row or any(row[k] is None for k in header):
            raise ValueError(f"line {lineno}: wrong field count")
        if "rne_ref_ulp" in row and row["rne_ref_ulp"] and not re.fullmatch(r"[0-9]+", row["rne_ref_ulp"]):
            raise ValueError(f"line {lineno}: rne_ref_ulp must be blank or an unsigned decimal integer")
        function = row["function"]
        if function not in FUNCTIONS or row["interface"] not in ("Nx", "array") or row["lmul"] not in ("1", "2") or not row["sample_id"]:
            raise ValueError(f"line {lineno}: invalid function/interface/lmul/sample_id")
        key = (function, row["interface"], row["lmul"], row["sample_id"])
        if key[:3] not in expected:
            raise ValueError(f"line {lineno}: unexpected scope {key[:3]}")
        sample = key[3]
        if re.fullmatch(r"C(?:0|[1-9][0-9]*)", sample):
            if int(sample[1:]) >= expected[key[:3]]:
                raise ValueError(f"group {key}: corpus ID out of range")
        elif not re.fullmatch(r"S(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*)", sample):
            raise ValueError(f"group {key}: invalid sample_id")
        if current is not None and key != current:
            if set(rows) != set(MODES):
                raise ValueError(f"group {current}: missing frm records")
            yield current, rows
            seen_ids.add(current[3])
            corpus_count += current[3].startswith("C")
            rows = {}
        next_scope = key[:3]
        if next_scope != scope:
            if scope is not None:
                check_block(scope, corpus_count)
                closed_scopes.add(scope)
                seen_ids.clear()
                corpus_count = 0
            if next_scope in closed_scopes:
                raise ValueError(f"scope {next_scope}: block repeated after closure")
            scope = next_scope
        if key != current and key[3] in seen_ids:
            raise ValueError(f"group {key}: duplicate or non-contiguous sample_id")
        current = key
        fmt = B32 if function in ("logf", "expf") else B64
        try:
            x = parse_bits(row["x_bits"], fmt, "x_bits")
            z = parse_bits(row["z_bits"], fmt, "z_bits", optional=function != "pow")
            if function != "pow" and z is not None:
                raise ValueError("unary z_bits must be empty")
            y = parse_bits(row["y_bits"], fmt, "y_bits")
            ref = parse_bits(row["ref_bits"], fmt, "ref_bits", optional=True)
            if sample.startswith("C") and ref is None:
                raise ValueError("corpus rows require ref_bits")
            if sample.startswith("S") and ref is not None:
                raise ValueError("semantic rows must not claim a frozen reference")
            mode = parse_mode(row["frm"])
        except ValueError as exc:
            raise ValueError(f"line {lineno}: {exc}") from exc
        if mode in rows:
            raise ValueError(f"group {key}: duplicate frm {mode}")
        if rows and (x, z, ref) != next(iter(rows.values()))[:3]:
            raise ValueError(f"group {key}: input or reference differs across frm")
        rows[mode] = (x, z, ref, y)
    if current is None:
        raise ValueError("empty input: no sample groups")
    if set(rows) != set(MODES):
        raise ValueError(f"group {current}: missing frm records")
    yield current, rows
    corpus_count += current[3].startswith("C")
    check_block(scope, corpus_count)
    closed_scopes.add(scope)
    if closed_scopes != set(expected):
        missing = sorted(set(expected) - closed_scopes)
        raise ValueError(f"missing expected scopes: {missing}")


def analyze_group(mpfr, key, rows, precisions):
    function = key[0]
    fmt = B32 if function in ("logf", "expf") else B64
    x, z, ref, _ = rows["RNE"]
    inputs = (x, z) if function == "pow" else (x,)
    kinds = [ieee_kind(b, fmt) for b in inputs]
    if "nan" in kinds:
        return "input_nan", "unresolved", {mode: "unresolved" for mode in MODES}, {mode: "unresolved" for mode in MODES}, 0
    if "inf" in kinds:
        return "input_inf", "unresolved", {mode: "unresolved" for mode in MODES}, {mode: "unresolved" for mode in MODES}, 0
    last = None
    for precision in precisions:
        bounds, signs, status = mpfr.bounds(function, x, z, fmt, precision)
        if status != "ok":
            return status, "unresolved", {mode: "unresolved" for mode in MODES}, {mode: "unresolved" for mode in MODES}, precision
        category = target_class(bounds, fmt)
        refstatus = reference_status(bounds, signs, fmt, ref)
        compared, errors = {}, {}
        baseline = rows["RNE"][3]
        baseline_kind = ieee_kind(baseline, fmt)
        finite_kinds = ("normal", "subnormal", "zero")
        numerical_categories = NUMERICAL_CATEGORIES
        for mode in MODES:
            got = rows[mode][3]
            kind = ieee_kind(got, fmt)
            if category not in numerical_categories or baseline_kind not in finite_kinds or kind not in finite_kinds:
                compared[mode] = "unresolved"
            else:
                compared[mode] = compare_closeness(ieee_fraction(got, fmt), ieee_fraction(baseline, fmt), bounds)
            if category in numerical_categories and kind in finite_kinds:
                errors[mode] = ulp_error_bin(ieee_fraction(got, fmt), bounds, fmt)
            else:
                errors[mode] = "unresolved"
        last = (category, refstatus, compared, errors, precision)
        refinable = category.startswith("cross_") or refstatus in ("unresolved", "zero_sign_unresolved")
        if category in numerical_categories and (any(v == "unresolved" for v in compared.values()) or any(v in ("unresolved", "cross_binade") for v in errors.values())):
            refinable = True
        if not refinable:
            break
    return last


def load_oracles(directory, profile, functions):
    """Load only explicitly supplied frozen headers, verifying their SHA."""
    result, cache = {}, {}
    for function in functions:
        if profile == "unit":
            name, count, digest, xname, zname, rname = UNIT[function]
        else:
            name, count, digest = function + ".h", 1024, PERF_HASH[function]
            xname, zname, rname = "input_bits", ("second_bits" if function == "pow" else None), "reference_bits"
        path = Path(directory) / name
        if path not in cache:
            raw = path.read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            if actual != digest:
                raise ValueError(f"frozen header SHA mismatch: {path}; got {actual}")
            text = raw.decode("ascii")
            arrays = {}
            pattern = r"static const uint(?:32|64)_t\s+(\w+)\[[^\]]+\]\s*=\s*\{(.*?)\};"
            for match in re.finditer(pattern, text, re.S):
                arrays[match[1]] = [int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]+)", match[2])]
            cache[path] = arrays
            print(f"# oracle={path} sha256={actual}", file=sys.stderr)
        arrays = cache[path]
        names = (xname, zname, rname)
        try:
            loaded = tuple(arrays[n] if n is not None else None for n in names)
        except KeyError as exc:
            raise ValueError(f"frozen header missing array {exc}") from exc
        if any(values is not None and len(values) != count for values in loaded):
            raise ValueError(f"frozen header array count mismatch for {function}")
        result[function] = loaded
    return result


def parse_selection(text, allowed, label):
    values = text.split(",")
    if not values or len(set(values)) != len(values) or any(value not in allowed for value in values):
        raise ValueError(f"invalid {label}; allowed: {','.join(allowed)}")
    return tuple(values)


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_reference_failure(stream, key, row, status):
    """Stream one complete, CSV-escaped corpus reference failure to stderr."""
    if status not in ("mismatch", "zero_sign_mismatch"):
        raise ValueError("invalid reference mismatch status")
    function, interface, lmul, sample_id = key
    x, z, reference, _ = row
    fmt = B32 if function in ("logf", "expf") else B64
    width = fmt.width // 4
    def bits(value):
        return "" if value is None else f"0x{value:0{width}x}"
    csv.writer(stream, lineterminator="\n").writerow((
        "REFERENCE_MISMATCH", function, interface, lmul, sample_id,
        status, bits(x), bits(z), bits(reference)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="input CSV or .csv.gz path; - for stdin without input SHA")
    parser.add_argument("--oracle-dir", required=True, help="explicit frozen oracle-headers or perf header directory")
    parser.add_argument("--profile", required=True, choices=("unit", "perf"), help="frozen input corpus identity")
    parser.add_argument("--functions", default=",".join(FUNCTIONS), help="expected comma-separated function names")
    parser.add_argument("--interfaces", default="Nx,array", help="expected interfaces, Nx,array")
    parser.add_argument("--lmuls", default="1,2", help="expected LMULs, 1,2")
    parser.add_argument("--mpfr-library", help="explicit MPFR library with validated standard LP64 MPFR/GMP ABI only")
    parser.add_argument("--precisions", default="512,1024,2048", help="increasing MPFR bit precisions (default: 512,1024,2048)")
    args = parser.parse_args(argv)
    try:
        precisions = tuple(int(v) for v in args.precisions.split(","))
        if not precisions or any(p < 64 or p > 16384 for p in precisions) or any(a >= b for a, b in zip(precisions, precisions[1:])):
            raise ValueError("precisions must strictly increase and each be in [64,16384]")
        functions = parse_selection(args.functions, FUNCTIONS, "functions")
        interfaces = parse_selection(args.interfaces, ("Nx", "array"), "interfaces")
        lmuls = parse_selection(args.lmuls, ("1", "2"), "LMULs")
        expected = {(f, interface, lmul): (UNIT[f][1] if args.profile == "unit" else 1024)
                    for f in functions for interface in interfaces for lmul in lmuls}
        oracles = load_oracles(args.oracle_dir, args.profile, functions)
        mpfr = MPFR(args.mpfr_library)
        input_hash = "stdin-unavailable" if args.input == "-" else digest_file(args.input)
        code_hash = digest_file(__file__)
        print(f"# mpfr_library={mpfr.path} mpfr_version={mpfr.version} emin={mpfr.get_emin()} emax={mpfr.get_emax()}", file=sys.stderr)
        print(f"# input_sha256={input_hash} code_sha256={code_hash} precisions={','.join(map(str, precisions))} bounds=RNDD,RNDU", file=sys.stderr)
        print(f"# profile={args.profile} functions={args.functions} interfaces={args.interfaces} lmuls={args.lmuls}", file=sys.stderr)
        print("# C=frozen_corpus; S=semantic_unvalidated_separate; comparisons=mode_vs_RNE_absolute_real_error", file=sys.stderr)
        print("# rne_ref_ulp is ignored; RMM is not MPFR_RNDA; zero signs counted separately", file=sys.stderr)
        print("# REFERENCE_MISMATCH columns: record,function,interface,lmul,sample_id,status,x_bits,z_bits,ref_bits", file=sys.stderr)
        stats = collections.Counter()
        ref_failures = 0
        if args.input == "-":
            inp = sys.stdin
        elif args.input.endswith(".gz"):
            inp = gzip.open(args.input, mode="rt", newline="", encoding="utf-8")
        else:
            inp = open(args.input, newline="", encoding="utf-8")
        try:
            reader = csv.DictReader(inp)
            for key, rows in groups(reader, expected):
                sample_kind = "corpus_C" if key[3].startswith("C") else "semantic_S_unvalidated"
                if sample_kind == "corpus_C":
                    index = int(key[3][1:])
                    xx, zz, rr = oracles[key[0]]
                    actual = rows["RNE"][:3]
                    want = (xx[index], zz[index] if zz is not None else None, rr[index])
                    if actual != want:
                        raise ValueError(f"{key}: x/z/ref differs from frozen header")
                category, refstatus, comparisons, errors, precision = analyze_group(mpfr, key, rows, precisions)
                if sample_kind == "corpus_C" and refstatus in ("mismatch", "zero_sign_mismatch"):
                    ref_failures += 1
                    write_reference_failure(sys.stderr, key, rows["RNE"], refstatus)
                group = key[:3] + (sample_kind,)
                for mode in MODES:
                    prefix = group + (mode,)
                    stats[prefix + ("samples", "total")] += 1
                    stats[prefix + ("truth", category)] += 1
                    stats[prefix + ("reference", refstatus)] += 1
                    x, z, _, got = rows[mode]
                    fmt = B32 if key[0] in ("logf", "expf") else B64
                    stats[prefix + ("input_x", ieee_detailed_kind(x, fmt))] += 1
                    if z is not None:
                        stats[prefix + ("input_z", ieee_detailed_kind(z, fmt))] += 1
                    stats[prefix + ("output", ieee_detailed_kind(got, fmt))] += 1
                    stats[prefix + ("comparison", comparisons[mode])] += 1
                    got_kind = ieee_kind(got, fmt)
                    baseline_kind = ieee_kind(rows["RNE"][3], fmt)
                    finite_truth_categories = NUMERICAL_CATEGORIES + ("outside_target_range", "cross_target_range", "cross_minnormal", "cross_zero")
                    if category in finite_truth_categories:
                        stats[prefix + ("finite_truth_output", got_kind)] += 1
                    else:
                        stats[prefix + ("finite_truth_output", "truth_not_proved_finite")] += 1
                    if category not in NUMERICAL_CATEGORIES:
                        reason = "truth_" + category
                    elif got_kind in ("inf", "nan"):
                        reason = "finite_truth_output_" + got_kind
                    elif baseline_kind in ("inf", "nan"):
                        reason = "finite_truth_rne_output_" + baseline_kind
                    elif comparisons[mode] == "unresolved":
                        reason = "midpoint_unresolved"
                    else:
                        reason = "comparable"
                    stats[prefix + ("comparison_eligibility", reason)] += 1
                    stats[prefix + ("absolute_error_ulp_bin", errors[mode])] += 1
                    stats[prefix + ("precision_bits", str(precision))] += 1
        finally:
            if inp is not sys.stdin:
                inp.close()
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(("function", "interface", "lmul", "sample_kind", "frm", "metric", "category", "count"))
        for key, count in sorted(stats.items()):
            writer.writerow((*key, count))
        print(f"# reference_mismatch_groups={ref_failures}; unresolved/special references are not a full verification", file=sys.stderr)
        return 1 if ref_failures else 0
    except (ValueError, OSError, RuntimeError, AttributeError) as exc:
        print(f"mpfr_accuracy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
