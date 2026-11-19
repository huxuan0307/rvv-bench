#!/usr/bin/env python3
"""严格比较已验收的 libmvec CSV：每配置十轮中的七个 steady 周期（按 R 归一）。

CSV v3 按原始 cycles 核对稳态窗口下限和 CV，控制组只做诊断。
历史表格不套用新门槛，且不能与 v3 混用。低于 0.2% 的差值记为 tie；
单次运行的比较只用于筛选，不替代同配置的独立复跑。
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from statistics import fmean, median, stdev

from runner_common import (CALIBRATION_MODES, CV_MAX, MAX_TIMING_REPEAT,
                           MIN_STEADY, PERF_FUNCTIONS, U64_MAX, WINDOW_MIN)

MODES = ("scalar_strict", "scalar_fast", "auto_m1", "auto_m2", "direct_m1",
         "direct_m2", *CALIBRATION_MODES)
TARGETS = frozenset(("auto_m1", "auto_m2", "direct_m1", "direct_m2"))
TIE_MAX = Fraction(1, 500)
FIELDS = ("interface", "function", "n", "k", "mode", "timing_repeat",
          "before_median", "after_median", "change_pct", "before_min",
          "before_max", "after_min", "after_max", "before_window_cycles",
          "after_window_cycles", "before_cv", "after_cv", "window_ok",
          "verdict", "checksum_equal")


def phase_of(r: int) -> str:
    return "first" if r == 0 else "warmup" if r < 3 else "steady"


def _fraction(value: Fraction) -> str:
    """Render a per-round cycle count for the comparison CSV (3 decimals)."""
    return f"{float(value):.3f}"


def read_samples(path: Path, function: str, n: int, batches: tuple[int, ...]):
    """检查九模式、十轮、轮换、周期及状态后返回各组 steady 每轮周期。"""
    wanted = {(n, k, mode, r) for k in batches for mode in MODES for r in range(10)}
    found = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row.get("function") != function:
                continue
            size, k = int(row["n"]), int(row["k"])
            if size != n or k not in batches:
                continue
            mode, r = row["mode"], int(row["r"])
            key = (size, k, mode, r)
            if key not in wanted:
                raise ValueError(f"{path}: 非法记录 {key}")
            if key in found:
                raise ValueError(f"{path}: 重复记录 {key}")
            expected_type = "sample" if mode in MODES[:6] else "calibration"
            if row.get("record_type") != expected_type:
                raise ValueError(f"{path}: 错误记录类别 {key}")
            if row.get("run_status") != "passed" or row.get("case_status") != "passed":
                raise ValueError(f"{path}: 运行或 case 未通过 {key}")
            if row.get("measurement_valid", "").lower() != "true":
                raise ValueError(f"{path}: 无效测量 {key}")
            if row["phase"] != phase_of(r):
                raise ValueError(f"{path}: 阶段不匹配 {key}")
            if int(row["order"]) != (MODES.index(mode) - r) % len(MODES):
                raise ValueError(f"{path}: 九模式轮转错误 {key}")
            begin, end, cycles = int(row["cycle_begin"]), int(row["cycle_end"]), int(row["cycles"])
            if (not all(0 <= value <= U64_MAX for value in (begin, end, cycles))
                    or (expected_type == "sample" and cycles == 0)
                    or ((end - begin) & U64_MAX) != cycles):
                raise ValueError(f"{path}: 周期字段不一致 {key}")
            wire = row.get("wire_format", "unversioned")
            if wire not in ("csv3", "csv2", "kv1", "unversioned"):
                raise ValueError(f"{path}: 非法 wire_format {key}")
            repeat_text = row.get("timing_repeat")
            if wire == "csv3" and not repeat_text:
                raise ValueError(f"{path}: csv3 缺少 timing_repeat {key}")
            repeat = int(repeat_text or "1")
            if not 1 <= repeat <= MAX_TIMING_REPEAT or (wire != "csv3" and repeat != 1):
                raise ValueError(f"{path}: 非法 timing_repeat {key}")
            raw_window = row.get("window_cycles")
            if raw_window not in (None, "") and int(raw_window) != cycles:
                raise ValueError(f"{path}: window_cycles 与原始周期不符 {key}")
            normalized = Fraction(cycles, repeat if expected_type == "sample" else 1)
            found[key] = (normalized, repeat, row["checksum"], cycles, wire)
    missing = wanted - found.keys()
    if missing:
        raise ValueError(f"{path}: 缺少 {len(missing)} 条记录，示例 {sorted(missing)[0]}")
    repeats = {row[1] for row in found.values()}
    if len(repeats) != 1:
        raise ValueError(f"{path}: 混用 timing_repeat")
    wires = {row[4] for row in found.values()}
    if len(wires) != 1:
        raise ValueError(f"{path}: 混用 wire_format")
    repeat, wire = repeats.pop(), wires.pop()
    groups = {}
    for k in batches:
        for mode in MODES:
            rows = [found[(n, k, mode, r)] for r in range(10)]
            if len({row[2] for row in rows}) != 1:
                raise ValueError(f"{path}: 十轮 checksum 不一致 {n}/{k}/{mode}")
            steady = [row[0] for row in rows[3:]]
            raw_windows = [row[3] for row in rows[3:]]
            mean = fmean(raw_windows)
            quality = {
                "wire": wire,
                "standard": wire == "csv3",
                "window_ok": min(raw_windows) >= WINDOW_MIN if wire == "csv3" else None,
                "window_cycles": median(raw_windows),
                "min_window_cycles": min(raw_windows),
                "rounds": len(steady),
                "cv": stdev(raw_windows) / mean if mean else None,
            }
            groups[(k, mode)] = (steady, rows[0][2], repeat, quality)
    return groups


def compare(before, after, function: str, n: int, batches: tuple[int, ...], tolerance: Decimal):
    """输出逐接口每轮周期和仅基于性能的候选判断，不替代正确性/同源审计。"""
    if not before or before.keys() != after.keys():
        raise ValueError("前后接口标签必须相同，且至少一个")
    limit = Fraction(tolerance) / 100
    result = []
    improved = False
    regressed = False
    wire_format = None
    for interface in sorted(before):
        a = read_samples(before[interface], function, n, batches)
        b = read_samples(after[interface], function, n, batches)
        for k in batches:
            for mode in MODES:
                old, old_hash, old_repeat, old_q = a[(k, mode)]
                new, new_hash, new_repeat, new_q = b[(k, mode)]
                if old_repeat != new_repeat:
                    raise ValueError(
                        f"timing_repeat 不一致，不能直接比较 {function}/{n}/{k}/{mode}: "
                        f"{old_repeat} != {new_repeat}")
                if old_q["wire"] != new_q["wire"]:
                    raise ValueError(f"前后 wire_format 不一致 {function}/{n}/{k}/{mode}")
                if wire_format is None:
                    wire_format = old_q["wire"]
                elif old_q["wire"] != wire_format:
                    raise ValueError("不同接口不能混用 wire_format")
                control = mode in CALIBRATION_MODES
                if old_q["standard"] and not control:
                    for label, q in (("before", old_q), ("after", new_q)):
                        if q["rounds"] < MIN_STEADY or not q["window_ok"]:
                            raise ValueError(
                                f"window_too_short: {label} {function}/{n}/{k}/{mode} "
                                f"min_window_cycles={q['min_window_cycles']} < {WINDOW_MIN}")
                        if q["cv"] is None or q["cv"] > CV_MAX:
                            raise ValueError(f"CV {q['cv']} 超出门槛 {CV_MAX}: "
                                             f"{label} {function}/{n}/{k}/{mode}")
                old_med, new_med = median(old), median(new)
                delta = (new_med - old_med) / old_med if old_med else None
                change = f"{100 * float(delta):.3f}" if delta is not None else ""
                if control:
                    verdict = "diagnostic"
                    window_ok = "not_applicable"
                else:
                    verdict = "tie" if abs(delta) < TIE_MAX else "better" if delta < 0 else "worse"
                    window_ok = "yes" if old_q["standard"] else "legacy_no_repeat"
                    if mode.startswith("auto_") and verdict == "better":
                        improved = True
                    if mode in TARGETS and delta > limit:
                        regressed = True
                result.append(dict(interface=interface, function=function, n=n, k=k,
                                   mode=mode, timing_repeat=old_repeat,
                                   before_median=_fraction(old_med),
                                   after_median=_fraction(new_med),
                                   change_pct=change,
                                   before_min=_fraction(min(old)),
                                   before_max=_fraction(max(old)),
                                   after_min=_fraction(min(new)),
                                   after_max=_fraction(max(new)),
                                   before_window_cycles=old_q["window_cycles"],
                                   after_window_cycles=new_q["window_cycles"],
                                   before_cv=("" if old_q["cv"] is None
                                              else f"{old_q['cv']:.6f}"),
                                   after_cv=("" if new_q["cv"] is None
                                             else f"{new_q['cv']:.6f}"),
                                   window_ok=window_ok, verdict=verdict,
                                   checksum_equal="yes" if old_hash == new_hash else "no"))
    return result, improved and not regressed


def labeled(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("接口参数必须为 标签=CSV文件")
        label, filename = value.split("=", 1)
        if not label or label in result or not filename:
            raise ValueError("接口标签不得为空或重复")
        result[label] = Path(filename)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog="不校验编译器、镜像、输入、RTL 或完整正确性；使用前须另行核对身份。")
    p.add_argument("--before", action="append", required=True, metavar="接口=CSV")
    p.add_argument("--after", action="append", required=True, metavar="接口=CSV")
    p.add_argument("--function", required=True, choices=PERF_FUNCTIONS)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--k", type=int, nargs="+", required=True)
    p.add_argument("--max-regression", type=Decimal, default=Decimal("5"))
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.n <= 0 or not args.k or min(args.k) <= 0 or len(set(args.k)) != len(args.k):
        p.error("n、k 必须为正且 k 不能重复")
    if not args.max_regression.is_finite() or not 0 <= args.max_regression < 100:
        p.error("最大回退百分比必须位于 [0,100)")
    try:
        rows, accepted = compare(labeled(args.before), labeled(args.after), args.function,
                                 args.n, tuple(args.k), args.max_regression)
        with args.output.open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    except (ValueError, OSError, KeyError) as e:
        p.exit(2, f"性能汇总失败: {e}\n")
    print("性能筛选:", "通过" if accepted else "未通过", "（仍须核对产物身份与完整正确性）")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
