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

RNE truth gate mode (--samples N --max-ulp T, both required, --profile unit)
judges each RNE observation against the actual MPFR directed enclosure of the
mathematical value instead of the frozen rounded reference.  Corpus sample
C<i> maps to frozen header index i * total // N, so a 1G-budget subsample is
deterministic and still identity-checked; S samples are this capture's
special/boundary lanes and are judged per lane too.  The frozen reference is
kept only as input identity and as one diagnostic column.  The gate summary
CSV goes to stdout; per-lane non-pass records go to stderr. Use --capture-complete with
rounding_accuracy.py analyze output to bind the gate to the validated capture.

`--generate-headers` is a separate, host-only mode that rebuilds the quartet
(atan/acos/log10/atan2) frozen headers from a caller-supplied frozen corpus CSV
with the historical columns function,idx,class,y_bits,x_bits. It uses the same
point_bounds()/RNE convergence path as the truth gate and never synthesizes the
historical corpus or claims to have regenerated it.
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
# Historical quartet-generator corpus schema: idx/class are identity and
# selection metadata; y_bits is the first atan2 operand, x_bits the second.
CORPUS_FIELDS = ("function", "idx", "class", "y_bits", "x_bits")
MODES = ("RNE", "RTZ", "RDN", "RUP", "RMM")
FUNCTIONS = ("sin", "cos", "log", "logf", "exp", "expf", "pow")
# The MPFR oracle covers these four functions too (b013a71 extension).  They
# are opt-in selections: the default --functions stays the legacy seven so
# existing CLI behavior and oracle directories remain unchanged.
EXTRA_FUNCTIONS = ("atan", "acos", "log10", "atan2")
ALL_FUNCTIONS = FUNCTIONS + EXTRA_FUNCTIONS
# Two-real-operand functions: z_bits is the second operand (pow base/exp,
# atan2 y/x).
BINARY = ("pow", "atan2")
# Frozen experiment identities, not inputs inferred from the current directory.
UNIT = {
    "sin": ("trigvec-unit-data.h", 103084, "247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d", "trig_unit_input", None, "trig_unit_sin"),
    "cos": ("trigvec-unit-data.h", 103084, "247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d", "trig_unit_input", None, "trig_unit_cos"),
    "log": ("log-unit.h", 104965, "e07f8f882e82a1000fe2e785199bf0ebba2872e1f6a900decb0ccd2217b5e208", "unit_input", None, "unit_reference"),
    "logf": ("logf-unit.h", 101385, "f5f37d8873c667f8e3a0c8c3576c1c93535695b04069ee352d6f16fbbe7ce1d1", "unit_input", None, "unit_reference"),
    "exp": ("exp-unit.h", 102039, "86b1414a952b04f5e30f62ba78e441dfc11b51f83a3fa65889c84e354cf64274", "unit_input", None, "unit_reference"),
    "expf": ("expf-unit.h", 100608, "d70e24909b2b1d2203e5d382b9643a427f90cc58f2747232b0fc81b334538106", "unit_input", None, "unit_reference"),
    "pow": ("powvec_unit_data.h", 124238, "a4dbbb9daea603282f8f4df2734cf18e1075fc5adaff02c2a4fb7e5ff187898e", "powvec_unit_x", "powvec_unit_z", "powvec_unit_reference"),
    "atan": ("atan.h", 1024, "8a04a576598909c1212e54395e285f770477d13af41d055a17fd47188945469f", "input_bits", None, "reference_bits"),
    "acos": ("acos.h", 1024, "8db89b881800aa8de5c6ed36382cd0247aee1fc7d1eb3ea78ced9057e6222b28", "input_bits", None, "reference_bits"),
    "log10": ("log10.h", 1024, "65edb80043ef74474bdb5bac3ab1f8cda4d1e1be0fbf0ca2bebbf0d2941523a6", "input_bits", None, "reference_bits"),
    "atan2": ("atan2.h", 1024, "3f49c37d21c34814ff107d5440897a474975008da84c006db4ba01ad8298eb00", "input_bits", "second_bits", "reference_bits"),
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
        self.set_nan = bind("mpfr_set_nan", [p], None)
        self.set_inf = bind("mpfr_set_inf", [p, integer], None)
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
        for name in ALL_FUNCTIONS:
            symbol = "exp" if name == "expf" else "log" if name == "logf" else name
            args = [p, p, p, integer] if name in BINARY else [p, p, integer]
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
            if function in BINARY:
                self.set_ieee(z, zbits, fmt)
            results, flags = [], []
            for out, rnd in ((lower, RNDD), (upper, RNDU)):
                self.clear_flags()
                if function in BINARY:
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

    def set_ieee_all(self, target, bits, fmt):
        """Set any IEEE value, including NaN and infinity, exactly into MPFR."""
        exp = (bits >> fmt.frac) & fmt.expmask
        mant = bits & ((1 << fmt.frac) - 1)
        if exp == fmt.expmask:
            if mant:
                self.set_nan(ctypes.byref(target))
            else:
                self.set_inf(ctypes.byref(target), -1 if bits & fmt.sign else 1)
            return
        self.set_ieee(target, bits, fmt)

    def point_bounds(self, function, xbits, zbits, fmt, precision):
        """Enclose the exact result with directed MPFR and bounded Fractions.

        A tiny, nonzero MPFR endpoint is rounded *outward* for serialization,
        never relabeled as exact zero. Target overflow requires proof from the
        endpoint nearer zero, not merely a large outward endpoint or MPFR flag.
        """
        x, z, lower, upper = [self.new(precision) for _ in range(4)]
        values = (x, z, lower, upper)
        try:
            self.set_ieee_all(x, xbits, fmt)
            if function in BINARY:
                self.set_ieee_all(z, zbits, fmt)
            operands = (x, z) if function in BINARY else (x,)
            flags = []
            ends = (lower, upper)
            for out, rnd in zip(ends, (RNDD, RNDU)):
                self.clear_flags()
                self.ops[function](ctypes.byref(out), *(ctypes.byref(v) for v in operands), rnd)
                flags.append((bool(self.underflow_p()), bool(self.overflow_p())))
            signs = tuple(-1 if self.signbit(ctypes.byref(v)) else 1 for v in ends)
            sign = signs[0] if signs[0] == signs[1] else None
            nan = tuple(bool(self.nan_p(ctypes.byref(v))) for v in ends)
            inf = tuple(bool(self.inf_p(ctypes.byref(v))) for v in ends)
            zero = tuple(bool(self.zero_p(ctypes.byref(v))) for v in ends)
            underflow, overflow = (any(f[i] for f in flags) for i in (0, 1))
            if any(nan):
                status = "math_nan" if all(nan) and not (underflow or overflow) else "mpfr_nan_unresolved"
                return status, None, None, None
            if all(inf) and sign and not (underflow or overflow):
                return "math_inf", None, None, sign
            if all(zero):
                status = "math_zero" if sign and not (underflow or overflow) else "zero_sign_unresolved"
                return status, None, None, sign
            exponents = tuple(None if i or z else self.get_exp(ctypes.byref(v))
                              for v, i, z in zip(ends, inf, zero))
            near = exponents[0 if sign == 1 else 1]
            # Both IEEE formats are far inside this guard. Do not construct
            # integers with bit counts proportional to MPFR's exponent range.
            if sign and near is not None and near > 4096:
                return "target_overflow", None, None, sign
            if any(inf) or overflow or any(e is not None and e > 4096 for e in exponents):
                return "overflow_enclosure_unresolved", None, None, None
            if underflow and self.get_emin() >= -4096:
                return "mpfr_underflow_range_unresolved", None, None, None
            tiny = power2(-4096) if any(e is not None and e < -4096 for e in exponents) else None
            bounds = []
            for i, (v, e) in enumerate(zip(ends, exponents)):
                if e is not None and e < -4096:
                    # Lower negative and upper positive endpoints move away
                    # from zero; the other two move to zero.
                    bounds.append(signs[i] * tiny if signs[i] == (-1, 1)[i] else Fraction(0))
                else:
                    bounds.append(self.as_fraction(v))
            lo, hi = bounds
            if lo > hi:
                raise RuntimeError("MPFR lower bound exceeds upper bound")
            return "ok", lo, hi, sign
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


def ulp_interval(value, bounds, fmt):
    """Exact spacing and closed |value-truth| bounds in ULP.

    Returns (status, spacing, upper, lower) where status is "ok" only when
    the whole truth interval shares one spacing.  Spacing is
    2**(floor(log2(|truth|))-fracbits) for a normal truth and minsubnormal
    for subnormal/below-min-subnormal or exact-zero truth.  An interval
    touching zero or two binades has no single spacing and returns
    "cross_zero"/"cross_binade".
    """
    lo, hi = bounds
    if lo == hi == 0:
        spacing = fmt.minsubnormal
    elif lo <= 0 <= hi:
        return "cross_zero", None, None, None
    else:
        abslo, abshi = (lo, hi) if lo > 0 else (-hi, -lo)
        if abslo >= fmt.minnormal:
            elo, ehi = floor_log2_fraction(abslo), floor_log2_fraction(abshi)
            if elo != ehi:
                return "cross_binade", None, None, None
            spacing = power2(elo - fmt.frac)
        elif abshi < fmt.minnormal:
            spacing = fmt.minsubnormal
        else:
            return "cross_binade", None, None, None
    upper = max(abs(value - lo), abs(value - hi)) / spacing
    lower = (Fraction(0) if lo <= value <= hi else min(abs(value - lo), abs(value - hi))) / spacing
    return "ok", spacing, upper, lower


def ulp_error_bin(value, bounds, fmt):
    """A rigorously enclosed |output-truth|/spacing histogram category.

    Boundaries use closed bins, assigning a value exactly at a cut to the
    lower bin.  A truth interval touching two binades (even on an endpoint)
    is not assigned a spacing.  A lower/upper error crossing a cut is
    unresolved.
    """
    state, spacing, upper, lower = ulp_interval(value, bounds, fmt)
    if state != "ok":
        return state
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


def certify_interval(value, bounds, fmt):
    """Bound ULP error even across zero, binades or the subnormal boundary."""
    lo, hi = bounds
    abslo = min(abs(lo), abs(hi)) if (lo > 0 or hi < 0) else Fraction(0)
    abshi = max(abs(lo), abs(hi))

    def spacing(magnitude):
        if magnitude < fmt.minnormal:
            return fmt.minsubnormal
        return power2(floor_log2_fraction(magnitude) - fmt.frac)

    smallest, largest = spacing(abslo), spacing(abshi)
    upper = max(abs(value - lo), abs(value - hi)) / smallest
    lower = (Fraction(0) if lo <= value <= hi
             else min(abs(value - lo), abs(value - hi)) / largest)
    return smallest, upper, lower


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


def groups(reader, expected, modes=MODES):
    """Stream groups; reject gaps, duplicate IDs, missing/reopened blocks.

    ``modes`` is the exact frm set expected per sample; the legacy observation
    mode uses all five, the RNE truth gate uses only ("RNE",).
    """
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
        if function not in ALL_FUNCTIONS or row["interface"] not in ("Nx", "array") or row["lmul"] not in ("1", "2") or not row["sample_id"]:
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
            if set(rows) != set(modes):
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
            z = parse_bits(row["z_bits"], fmt, "z_bits", optional=function not in BINARY)
            if function not in BINARY and z is not None:
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
        if mode not in modes:
            raise ValueError(f"line {lineno}: frm {mode} is not expected in this run")
        if mode in rows:
            raise ValueError(f"group {key}: duplicate frm {mode}")
        if rows and (x, z, ref) != next(iter(rows.values()))[:3]:
            raise ValueError(f"group {key}: input or reference differs across frm")
        rows[mode] = (x, z, ref, y)
    if current is None:
        raise ValueError("empty input: no sample groups")
    if set(rows) != set(modes):
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
    inputs = (x, z) if function in BINARY else (x,)
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
            if function not in PERF_HASH:
                raise ValueError(f"profile perf has no pinned frozen header for {function}")
            name, count, digest = function + ".h", 1024, PERF_HASH[function]
            xname, zname, rname = "input_bits", ("second_bits" if function in BINARY else None), "reference_bits"
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

@dataclass(frozen=True)
class Certification:
    """One RNE truth verdict with exact Fraction bounds for the worst case."""
    verdict: str                 # "pass" | "fail" | "unresolved"
    category: str
    upper: object = None         # exact Fraction |value - truth|/spacing bound
    lower: object = None
    spacing: object = None
    expected_bits: object = None
    precision: object = None


GATE_FIELDS = ("function", "interface", "lmul", "sample_kind", "samples",
               "threshold_ulp", "pass", "fail", "unresolved", "status", "bounded_ulp_lanes",
               "frozen_ref_equal", "worst_sample_id", "worst_verdict",
               "worst_category", "worst_upper_ulp", "worst_x_bits",
               "worst_z_bits", "worst_got_bits")


def gate_corpus_index(sample_index, total, samples):
    """Deterministic 1G-budget subsample: C<i> -> frozen index i*total//N."""
    if not 0 < samples <= total:
        raise ValueError(f"--samples must satisfy 0 < N <= {total}, got {samples}")
    if not 0 <= sample_index < samples:
        raise ValueError(f"corpus sample index {sample_index} outside 0..{samples - 1}")
    return sample_index * total // samples


def _bits_text(value, fmt):
    return "" if value is None else f"0x{value:0{fmt.width // 4}x}"


def certify_point(mpfr, function, x, z, got, fmt, precisions, threshold):
    """Certify one RNE output against the MPFR-directed truth enclosure.

    The frozen rounded reference is never consulted: the verdict uses the real
    value interval only (plus ISO C99 special-value semantics as implemented by
    MPFR).  "pass" means the exact upper bound is <= threshold; "fail" means
    the lower bound already exceeds threshold or a non-finite/sign expectation
    is violated; "unresolved" stays fail-closed.  Equal threshold passes.
    """
    if function not in ALL_FUNCTIONS:
        return Certification("unresolved", "unsupported_function")
    threshold = Fraction(threshold)
    if threshold < 0:
        raise ValueError("threshold must be non-negative")
    got_kind = ieee_kind(got, fmt)
    got_detail = ieee_detailed_kind(got, fmt)
    # MPFR has one NaN class; do not apply pow's quiet-NaN exceptions to sNaN.
    inputs = (x, z) if function in BINARY else (x,)
    if any(ieee_detailed_kind(b, fmt) == "snan" for b in inputs):
        return Certification("pass" if got_detail == "qnan" else "fail", "snan_requires_qnan")
    last = Certification("unresolved", "no_precision")
    inf_bits = fmt.expmask << fmt.frac
    overflow_edge = power2(fmt.bias + 1) - power2(fmt.bias - fmt.frac - 1)
    for precision in precisions:
        status, lo, hi, sign = mpfr.point_bounds(function, x, z, fmt, precision)
        if status == "math_nan":
            if got_kind == "nan" and got_detail == "qnan":
                return Certification("pass", "expected_qnan", precision=precision)
            return Certification("fail", "expected_qnan_got_" + got_detail, precision=precision)
        if status == "math_inf" or status == "target_overflow" or (
                status == "ok" and (lo >= overflow_edge or hi <= -overflow_edge)):
            negative = sign == -1 if status != "ok" else hi < 0
            expected = (fmt.sign if negative else 0) | inf_bits
            category = "expected_inf" if status == "math_inf" else "rne_overflow"
            verdict = "pass" if got == expected else "fail"
            return Certification(verdict, category, expected_bits=expected, precision=precision)
        if status == "math_zero":
            expected = fmt.sign if sign < 0 else 0
            if got == expected:
                return Certification("pass", "expected_zero", expected_bits=expected, precision=precision)
            return Certification("fail", "expected_zero_mismatch", expected_bits=expected, precision=precision)
        if status != "ok":
            last = Certification("unresolved", status, precision=precision)
            continue
        if lo <= -overflow_edge or hi >= overflow_edge:
            last = Certification("unresolved", "rne_overflow_boundary", precision=precision)
            continue
        if got_kind in ("nan", "inf"):
            return Certification("fail", "finite_truth_nonfinite_output", precision=precision)
        if got_kind == "zero":
            truth_sign = 1 if lo > 0 else -1 if hi < 0 else sign
            if truth_sign is None:
                last = Certification("unresolved", "zero_sign_unresolved", precision=precision)
                continue
            if (truth_sign < 0) != bool(got & fmt.sign):
                return Certification("fail", "zero_sign_mismatch", precision=precision)
        spacing, upper, lower = certify_interval(ieee_fraction(got, fmt), (lo, hi), fmt)
        if lower > threshold:
            return Certification("fail", "ulp", upper, lower, spacing, precision=precision)
        if upper <= threshold:
            return Certification("pass", "ulp", upper, lower, spacing, precision=precision)
        last = Certification("unresolved", "threshold_straddle", upper, lower, spacing, precision=precision)
    return last


def gate_analyse(reader, expected, oracles, mpfr, samples, threshold, precisions):
    """Run the RNE-only truth gate and write its summary CSV to stdout."""
    stats = {}
    expected_scopes = set(expected)

    def bucket(scope, kind):
        return stats.setdefault((scope, kind), dict(
            samples=0, pass_=0, fail=0, unresolved=0,
            frozen_equal=0, bounded=0, worst=None))

    for key, rows in groups(reader, expected, modes=("RNE",)):
        function, interface, lmul, sample_id = key
        fmt = B32 if function in ("logf", "expf") else B64
        x, z, ref, got = rows["RNE"]
        kind = "corpus_C" if sample_id.startswith("C") else "semantic_S_unvalidated"
        if kind == "corpus_C":
            total = UNIT[function][1]
            index = gate_corpus_index(int(sample_id[1:]), total, samples)
            xx, zz, rr = oracles[function]
            want = (xx[index], zz[index] if zz is not None else None, rr[index])
            if (x, z, ref) != want:
                raise ValueError(f"{key}: x/z/ref differs from frozen header")
        cert = certify_point(mpfr, function, x, z, got, fmt, precisions, threshold)
        entry = bucket(key[:3], kind)
        entry["samples"] += 1
        if cert.verdict == "pass":
            entry["pass_"] += 1
        elif cert.verdict == "fail":
            entry["fail"] += 1
        else:
            entry["unresolved"] += 1
        if kind == "corpus_C" and got == ref:
            entry["frozen_equal"] += 1
        if cert.upper is not None:
            entry["bounded"] += 1
            if entry["worst"] is None or cert.upper > entry["worst"][0]:
                entry["worst"] = (cert.upper, sample_id, cert, x, z, got)
        if cert.verdict != "pass":
            csv.writer(sys.stderr, lineterminator="\n").writerow((
                "GATE_NONPASS", function, interface, lmul, sample_id, kind,
                cert.verdict, cert.category,
                "" if cert.lower is None else str(cert.lower),
                "" if cert.upper is None else str(cert.upper),
                _bits_text(x, fmt), _bits_text(z, fmt), _bits_text(got, fmt),
                _bits_text(cert.expected_bits, fmt), cert.precision))
    actual_scopes = {key[0] for key in stats}
    if actual_scopes != expected_scopes:
        raise ValueError("coverage: missing expected scope(s): "
                         + ", ".join(map(str, sorted(expected_scopes - actual_scopes))))
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(GATE_FIELDS)
    total_fail = total_unresolved = 0
    for (scope, kind) in sorted(stats):
        entry = stats[(scope, kind)]
        status = "fail" if entry["fail"] else ("unresolved" if entry["unresolved"] else "pass")
        total_fail += entry["fail"]
        total_unresolved += entry["unresolved"]
        worst = entry["worst"]
        if worst is None:
            wid = verdict = category = upper = wx = wz = wg = ""
        else:
            _, sample_id, cert, x, z, got = worst
            fmt = B32 if scope[0] in ("logf", "expf") else B64
            wid, verdict, category = sample_id, cert.verdict, cert.category
            upper = "" if cert.upper is None else str(cert.upper)
            wx, wz, wg = _bits_text(x, fmt), _bits_text(z, fmt), _bits_text(got, fmt)
        writer.writerow((scope[0], scope[1], scope[2], kind, entry["samples"],
                         str(threshold), entry["pass_"], entry["fail"],
                         entry["unresolved"], status, entry["bounded"],
                         entry["frozen_equal"] if kind == "corpus_C" else "",
                         wid, verdict, category, upper, wx, wz, wg))
    print(f"# gate: samples={samples} max_ulp={threshold} fail={total_fail} "
          f"unresolved={total_unresolved}", file=sys.stderr)
    return 1 if (total_fail or total_unresolved) else 0



def capture_identity(manifest, path, lane_sha, functions):
    """Bind this input to an existing validated capture, not arbitrary S rows."""
    if path == "-" or Path(path).resolve() != (manifest.parent / "lanes.csv.gz").resolve():
        raise ValueError("--capture-complete requires lanes.csv.gz beside the capture manifest")
    with manifest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["role", "value"]:
            raise ValueError("invalid capture completion header")
        rows = list(reader)
    info = {r["role"]: r["value"] for r in rows}
    if (len(rows) != len(info) or any(None in r or None in r.values() for r in rows)
            or info.get("lanes_sha256") != lane_sha
            or set(info.get("functions", "").split(",")) != set(functions)
            or info.get("run_role") not in ("baseline", "candidate")):
        raise ValueError("capture completion identity or function set mismatch")
    return digest_file(manifest)


def _corpus_bits(text, field, line, required=True):
    """Parse one 64-bit hexadecimal corpus field with strict fail-closed rules."""
    if text is None:
        raise ValueError(f"corpus line {line}: missing {field}")
    if text != text.strip():
        raise ValueError(f"corpus line {line}: {field} has surrounding whitespace")
    if not text:
        if required:
            raise ValueError(f"corpus line {line}: empty {field}")
        return None
    digits = text[2:] if text[:2].lower() == "0x" else text
    if not re.fullmatch(r"[0-9a-fA-F]{1,16}", digits):
        raise ValueError(f"corpus line {line}: {field} must be 1..16 hex digits")
    return int(digits, 16)


def load_frozen_corpus(path):
    """Load the historical quartet-generator CSV without inferring any data.

    The exact header is function,idx,class,y_bits,x_bits.  Rows keep CSV order
    (the generator later puts special rows first and then random rows); unknown
    functions/classes, duplicate per-function indices, malformed bit fields,
    missing atan2 operands and row-shape errors are rejected rather than
    silently skipped.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"corpus CSV does not exist: {path}")
    corpus = {function: [] for function in EXTRA_FUNCTIONS}
    seen = set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        header = tuple(reader.fieldnames or ())
        if header != CORPUS_FIELDS:
            raise ValueError("corpus header must be " + ",".join(CORPUS_FIELDS))
        for line, row in enumerate(reader, 2):
            if None in row or any(row.get(field) is None for field in CORPUS_FIELDS):
                raise ValueError(f"corpus line {line}: malformed row")
            function = row["function"]
            if function not in EXTRA_FUNCTIONS:
                raise ValueError(f"corpus line {line}: unsupported function {function!r}")
            kind = row["class"]
            if kind not in ("special", "random"):
                raise ValueError(f"corpus line {line}: class must be special or random")
            index_text = row["idx"]
            if not re.fullmatch(r"[0-9]+", index_text):
                raise ValueError(f"corpus line {line}: idx must be an unsigned decimal integer")
            index = int(index_text)
            key = (function, index)
            if key in seen:
                raise ValueError(f"corpus line {line}: duplicate idx {index} for {function}")
            seen.add(key)
            second = _corpus_bits(row["y_bits"], "y_bits", line,
                                  required=function == "atan2")
            first = _corpus_bits(row["x_bits"], "x_bits", line)
            corpus[function].append({"idx": index, "class": kind,
                                     "x": first, "y": second})
    return corpus


def select_frozen_rows(rows, count):
    """Keep every special row, then random rows in CSV order, and slice count."""
    if not isinstance(count, int) or count <= 0:
        raise ValueError("--count must be a positive integer")
    special = [row for row in rows if row["class"] == "special"]
    random = [row for row in rows if row["class"] == "random"]
    chosen = special + random
    if len(chosen) < count:
        raise ValueError(f"corpus has {len(chosen)} usable rows, need {count}")
    return chosen[:count]


def rne_point_bits(mpfr, function, first, second, fmt, precisions):
    """Return the correctly rounded RNE reference under directed MPFR bounds.

    This reuses MPFR.point_bounds() for the true-value enclosure and only
    accepts a precision where both exact endpoints round to the same IEEE
    value.  Special values are reduced to a deterministic representation
    (the historical negative qNaN for math_nan, signed infinity/zero
    otherwise); unresolved or unsupported intervals raise instead of guessing.
    """
    if function not in EXTRA_FUNCTIONS:
        raise ValueError(f"unsupported oracle function: {function}")
    precisions = tuple(precisions)
    if (not precisions or any(not isinstance(p, int) or p < 64 or p > 16384
                              for p in precisions)
            or any(left >= right for left, right in zip(precisions, precisions[1:]))):
        raise ValueError("precisions must strictly increase and each be in [64,16384]")
    # Historical quartet headers use the negative quiet NaN produced by the
    # original mpfr_get_d round-trip; NaN sign is not a mathematical contract,
    # but preserving it keeps regenerated finite/special arrays comparable.
    qnan = fmt.sign | (fmt.expmask << fmt.frac) | (1 << (fmt.frac - 1))
    infinity = fmt.expmask << fmt.frac
    last = "no precision tried"
    for precision in precisions:
        status, lo, hi, sign = mpfr.point_bounds(function, first, second, fmt, precision)
        if status == "math_nan":
            return qnan
        if status in ("math_inf", "target_overflow"):
            if sign is None:
                last = f"{status} without a signed endpoint at {precision} bits"
                continue
            return (fmt.sign if sign == -1 else 0) | infinity
        if status == "math_zero":
            if sign is None:
                last = f"math_zero without a signed endpoint at {precision} bits"
                continue
            return fmt.sign if sign < 0 else 0
        if status != "ok":
            last = f"{status} at {precision} bits"
            continue
        zero_negative = sign == -1
        left = round_rne_bits(lo, fmt, zero_negative=zero_negative)
        right = round_rne_bits(hi, fmt, zero_negative=zero_negative)
        if left == right:
            return left
        last = f"directed endpoints round differently at {precision} bits"
    raise RuntimeError(f"ambiguous RNE reference for {function} "
                       f"first={first:#x} second={second if second is None else hex(second)}: {last}")


def _header_array(values):
    parts = [f"0x{value:016x}ULL" for value in values]
    chunks = [", ".join(parts[i:i + 4]) for i in range(0, len(parts), 4)]
    return ",\n  ".join(chunks)


def render_frozen_header(function, samples, references):
    """Render the historical quartet header layout from explicit samples."""
    if len(samples) != len(references):
        raise ValueError("sample/reference count mismatch")
    binary = function == "atan2"
    lines = [f"/* Frozen MPFR-512 directed-interval oracle corpus for {function}.",
             " * Generated by gen_frozen_headers.py; do not edit by hand.",
             " * input_bits = first operand (y for atan2);"
             + (" second_bits = x;" if binary else "")
             + " reference_bits = MPFR RNE.",
             " */",
             "#include <stdint.h>",
             f"#define DATA_COUNT {len(samples)}",
             "static const uint64_t input_bits[DATA_COUNT] = {"]
    lines.append(_header_array([sample[0] for sample in samples]))
    lines[-1] += "};"
    if binary:
        lines.append("static const uint64_t second_bits[DATA_COUNT] = {")
        lines.append(_header_array([sample[1] for sample in samples]))
        lines[-1] += "};"
    lines.append("static const uint64_t reference_bits[DATA_COUNT] = {")
    lines.append(_header_array(references))
    lines[-1] += "};"
    return "\n".join(lines) + "\n"


def generate_frozen_headers(inputs, outdir, count=1024, functions=EXTRA_FUNCTIONS,
                            precisions=(512, 1024, 2048), mpfr=None):
    """Generate quartet headers from a caller-supplied frozen corpus CSV.

    All references are computed and validated before any output is written;
    an ambiguous/failed reference leaves no partially generated headers from
    this call.  Nothing is synthesized when the CSV is absent.  Return
    ``{function: (count, sha256)}`` for the caller to pin explicitly.
    """
    if isinstance(functions, str):
        functions = parse_selection(functions, EXTRA_FUNCTIONS, "functions")
    else:
        functions = tuple(functions)
    if not functions or len(set(functions)) != len(functions):
        raise ValueError("functions must be a non-empty unique selection")
    for function in functions:
        if function not in EXTRA_FUNCTIONS:
            raise ValueError(f"unsupported oracle function: {function}")
    if isinstance(precisions, str):
        precisions = tuple(int(value) for value in precisions.split(","))
    else:
        precisions = tuple(precisions)
    if (not precisions or any(not isinstance(p, int) or p < 64 or p > 16384
                              for p in precisions)
            or any(left >= right for left, right in zip(precisions, precisions[1:]))):
        raise ValueError("precisions must strictly increase and each be in [64,16384]")
    corpus = load_frozen_corpus(inputs)
    selected = {function: select_frozen_rows(corpus[function], count)
                for function in functions}
    if mpfr is None:
        mpfr = MPFR()
    files = {}
    for function in functions:
        binary = function == "atan2"
        samples, references = [], []
        for row in selected[function]:
            if binary:
                first, second = row["y"], row["x"]
            else:
                first, second = row["x"], None
            if binary and second is None:
                raise ValueError(f"corpus line for {function} lacks y_bits")
            samples.append((first, second) if binary else (first,))
            references.append(rne_point_bits(mpfr, function, first, second, B64, precisions))
        files[function] = (len(samples),
                           render_frozen_header(function, samples, references))
    outdir = Path(outdir)
    outdir.mkdir(parents=True)
    results = {}
    for function, (sample_count, text) in files.items():
        path = outdir / f"{function}.h"
        data = text.encode("ascii")
        with path.open("xb") as stream:
            stream.write(data)
        results[function] = (sample_count, hashlib.sha256(data).hexdigest())
    return results


def generate_headers_cli(argv):
    """CLI for --generate-headers; kept separate to leave analyzer parsing intact."""
    parser = argparse.ArgumentParser(
        prog="mpfr_accuracy.py --generate-headers",
        description="Generate atan/acos/log10/atan2 frozen oracle headers from a "
                    "caller-supplied function,idx,class,y_bits,x_bits CSV. "
                    "This does not recreate the historical corpus.")
    parser.add_argument("--generate-headers", action="store_true",
                        help="explicit alias; --outdir alone also selects this mode")
    parser.add_argument("--inputs", required=True, type=Path,
                        help="caller-supplied frozen corpus CSV (no default)")
    parser.add_argument("--outdir", required=True, type=Path,
                        help="new directory; refuses to overwrite existing headers")
    parser.add_argument("--count", type=int, default=1024)
    parser.add_argument("--functions", default=",".join(EXTRA_FUNCTIONS))
    parser.add_argument("--precisions", default="512,1024,2048")
    parser.add_argument("--mpfr-library")
    args = parser.parse_args(argv)
    try:
        functions = parse_selection(args.functions, EXTRA_FUNCTIONS, "functions")
        precisions = tuple(int(value) for value in args.precisions.split(","))
        if (not precisions or any(p < 64 or p > 16384 for p in precisions)
                or any(left >= right for left, right in zip(precisions, precisions[1:]))):
            raise ValueError("precisions must strictly increase and each be in [64,16384]")
        mpfr = MPFR(args.mpfr_library)
        print(f"# MPFR {mpfr.version}", flush=True)
        print("# source=caller-supplied CSV; this mode does not regenerate the historical corpus",
              file=sys.stderr, flush=True)
        results = generate_frozen_headers(args.inputs, args.outdir, args.count,
                                          functions, precisions, mpfr=mpfr)
    except (ValueError, OSError, RuntimeError, AttributeError, EOFError) as exc:
        print(f"mpfr_accuracy: {exc}", file=sys.stderr)
        return 2
    for function, (sample_count, digest) in results.items():
        print(f"{function}.h count={sample_count} sha256={digest}", flush=True)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # The historical generator used --inputs/--outdir without a mode flag;
    # accept both spellings while keeping the analyzer parser untouched.
    if any(arg == "--generate-headers" or arg == "--outdir"
           or arg.startswith("--outdir=") for arg in argv):
        return generate_headers_cli(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="input CSV or .csv.gz path; - for stdin without input SHA")
    parser.add_argument("--oracle-dir", required=True, help="explicit frozen oracle-headers or perf header directory")
    parser.add_argument("--profile", required=True, choices=("unit", "perf"), help="frozen input corpus identity")
    parser.add_argument("--functions", default=",".join(FUNCTIONS), help="expected comma-separated function names (default: legacy seven)")
    parser.add_argument("--interfaces", default="Nx,array", help="expected interfaces, Nx,array")
    parser.add_argument("--lmuls", default="1,2", help="expected LMULs, 1,2")
    parser.add_argument("--mpfr-library", help="explicit MPFR library with validated standard LP64 MPFR/GMP ABI only")
    parser.add_argument("--precisions", default="512,1024,2048", help="increasing MPFR bit precisions (default: 512,1024,2048)")
    parser.add_argument("--samples", type=int,
                        help="RNE truth gate: samples per scope; C<i> maps to frozen index i*total//N (requires --max-ulp and --profile unit)")
    parser.add_argument("--max-ulp",
                        help="RNE truth gate: maximum exact absolute ULP error, e.g. 1 or 0.5 (requires --samples)")
    parser.add_argument("--capture-complete", type=Path,
                        help="verify lane SHA against rounding_accuracy.py analyze's COMPLETE.csv (RNE gate only)")
    args = parser.parse_args(argv)
    gate_mode = args.samples is not None or args.max_ulp is not None
    if (args.samples is None) != (args.max_ulp is None):
        parser.error("--samples and --max-ulp must be given together")
    if args.capture_complete and not gate_mode:
        parser.error("--capture-complete requires RNE gate mode")
    try:
        precisions = tuple(int(v) for v in args.precisions.split(","))
        if not precisions or any(p < 64 or p > 16384 for p in precisions) or any(a >= b for a, b in zip(precisions, precisions[1:])):
            raise ValueError("precisions must strictly increase and each be in [64,16384]")
        functions = parse_selection(args.functions, ALL_FUNCTIONS, "functions")
        interfaces = parse_selection(args.interfaces, ("Nx", "array"), "interfaces")
        lmuls = parse_selection(args.lmuls, ("1", "2"), "LMULs")
        threshold = None
        if gate_mode:
            if args.profile != "unit":
                raise ValueError("gate mode (--samples/--max-ulp) requires --profile unit")
            threshold = Fraction(args.max_ulp)
            if threshold < 0:
                raise ValueError("--max-ulp must be non-negative")
            if args.samples <= 0:
                raise ValueError("--samples must be positive")
            for function in functions:
                if args.samples > UNIT[function][1]:
                    raise ValueError(f"--samples {args.samples} exceeds frozen total "
                                     f"{UNIT[function][1]} for {function}")
            expected = {(f, interface, lmul): args.samples
                        for f in functions for interface in interfaces for lmul in lmuls}
        else:
            expected = {(f, interface, lmul): (UNIT[f][1] if args.profile == "unit" else 1024)
                        for f in functions for interface in interfaces for lmul in lmuls}
        oracles = load_oracles(args.oracle_dir, args.profile, functions)
        mpfr = MPFR(args.mpfr_library)
        input_hash = "stdin-unavailable" if args.input == "-" else digest_file(args.input)
        code_hash = digest_file(__file__)
        complete_hash = (capture_identity(args.capture_complete, args.input, input_hash, functions)
                         if args.capture_complete else "unverified")
        print(f"# mpfr_library={mpfr.path} mpfr_version={mpfr.version} emin={mpfr.get_emin()} emax={mpfr.get_emax()}", file=sys.stderr)
        print(f"# input_sha256={input_hash} code_sha256={code_hash} precisions={','.join(map(str, precisions))} bounds=RNDD,RNDU", file=sys.stderr)
        if gate_mode:
            print(f"# gate=RNE profile={args.profile} samples={args.samples} max_ulp={threshold} "
                  f"functions={args.functions} interfaces={args.interfaces} lmuls={args.lmuls}", file=sys.stderr)
            print("# C<i> maps to frozen header index i*total//N; frozen ref_bits is identity + one diagnostic column; verdict uses MPFR truth bounds", file=sys.stderr)
            print(f"# capture_complete_sha256={complete_hash}; S lanes are judged per lane; S coverage requires checked capture", file=sys.stderr)
            print("# GATE_NONPASS columns: record,function,interface,lmul,sample_id,sample_kind,verdict,category,lower,upper,x_bits,z_bits,got_bits,expected_bits,precision", file=sys.stderr)
        else:
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
            if gate_mode:
                return gate_analyse(reader, expected, oracles, mpfr,
                                    args.samples, threshold, precisions)
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
    except (ValueError, OSError, RuntimeError, AttributeError, EOFError, ZeroDivisionError) as exc:
        print(f"mpfr_accuracy: {exc}", file=sys.stderr)
        return 2 if gate_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
