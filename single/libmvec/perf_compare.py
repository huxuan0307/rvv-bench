#!/usr/bin/env python3
"""严格比较已验收的 libmvec CSV：每配置十轮中的七个 steady 周期（按 R 归一）。"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from statistics import median

MODES = ("scalar_strict", "scalar_fast", "auto_m1", "auto_m2", "direct_m1",
         "direct_m2", "rdcycle", "empty", "copy")
TARGETS = frozenset(("auto_m1", "auto_m2", "direct_m1", "direct_m2"))
FIELDS = ("interface", "function", "n", "k", "mode", "timing_repeat",
          "before_median", "after_median", "change_pct", "before_min",
          "before_max", "after_min", "after_max", "checksum_equal")


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
            if cycles <= 0 or end - begin != cycles:
                raise ValueError(f"{path}: 周期字段不一致 {key}")
            repeat_text = row.get("timing_repeat") or "1"
            repeat = int(repeat_text)
            if repeat < 1:
                raise ValueError(f"{path}: 非法 timing_repeat {key}")
            found[key] = (Fraction(cycles, repeat), repeat, row["checksum"])
    missing = wanted - found.keys()
    if missing:
        raise ValueError(f"{path}: 缺少 {len(missing)} 条记录，示例 {sorted(missing)[0]}")
    groups = {}
    for k in batches:
        for mode in MODES:
            rows = [found[(n, k, mode, r)] for r in range(10)]
            repeats = {repeat for _, repeat, _ in rows}
            if len(repeats) != 1:
                raise ValueError(f"{path}: 同一配置的 timing_repeat 不一致 {n}/{k}/{mode}")
            if len({checksum for _, _, checksum in rows}) != 1:
                raise ValueError(f"{path}: 十轮 checksum 不一致 {n}/{k}/{mode}")
            repeat = repeats.pop()
            groups[(k, mode)] = ([cycles for cycles, _, _ in rows[3:]],
                                 rows[0][2], repeat)
    return groups


def compare(before, after, function: str, n: int, batches: tuple[int, ...], tolerance: Decimal):
    """输出逐接口每轮周期和仅基于性能的候选判断，不替代正确性/同源审计。"""
    if not before or before.keys() != after.keys():
        raise ValueError("前后接口标签必须相同，且至少一个")
    limit = Fraction(tolerance) / 100
    result = []
    improved = False
    regressed = False
    for interface in sorted(before):
        a = read_samples(before[interface], function, n, batches)
        b = read_samples(after[interface], function, n, batches)
        for k in batches:
            for mode in MODES:
                old, old_hash, old_repeat = a[(k, mode)]
                new, new_hash, new_repeat = b[(k, mode)]
                if old_repeat != new_repeat:
                    raise ValueError(
                        f"timing_repeat 不一致，不能直接比较 {function}/{n}/{k}/{mode}: "
                        f"{old_repeat} != {new_repeat}")
                old_med, new_med = median(old), median(new)
                change = 100 * float((new_med - old_med) / old_med)
                if mode.startswith("auto_") and new_med < old_med:
                    improved = True
                if mode in TARGETS and new_med > old_med * (1 + limit):
                    regressed = True
                result.append(dict(interface=interface, function=function, n=n, k=k,
                                   mode=mode, timing_repeat=old_repeat,
                                   before_median=_fraction(old_med),
                                   after_median=_fraction(new_med),
                                   change_pct=f"{change:.3f}",
                                   before_min=_fraction(min(old)),
                                   before_max=_fraction(max(old)),
                                   after_min=_fraction(min(new)),
                                   after_max=_fraction(max(new)),
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
    p.add_argument("--function", required=True, choices=("log", "exp", "expf", "logf", "pow", "sin", "cos"))
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
