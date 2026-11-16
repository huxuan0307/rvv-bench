#!/usr/bin/env python3
"""Turn raw ``LAT`` windows into per-K medians and a cycles(K) fit.

Input is one or more raw stdout logs (or a run directory produced by
run_latency.py).  Nothing is averaged across phases: only the steady windows
(round >= 3 of 1 first + 2 warmup + 7 steady) feed the regression, while the
first/warmup rows stay in ``samples.csv`` so a reviewer can see the settling.

Outputs (all under --output-dir, all CSV, no JSON):

  samples.csv   one row per raw window, tagged with source file and line
  windows.csv   per (entry, K): n, median, mean, stdev, cv, min, max
  fit.csv       per entry: slope (cycles per chained call), intercept, R^2,
                worst relative residual, worst CV, K range, input source

The fit is ordinary least squares of median cycles against K.  K = 0 is the
empty window, so the intercept is fixed measurement overhead and the slope is
the only latency number: cycles per chained call at K >= 1.
"""

import argparse
import csv
import math
import re
import sys
from pathlib import Path

LAT_RE = re.compile(r"^LAT,(.*)$")
KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^,]*)")
PHASE_STEADY = "steady"
STEADY_FIRST_ROUND = 3


def parse_log(path):
    """Return (windows, headers, checks, ends, chains) for one raw log."""
    windows, headers, checks, ends, chains = [], [], [], [], []
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(),
                                  1):
        line = line.strip()
        if line.startswith("LAT_HEADER,"):
            headers.append(dict(KV_RE.findall(line[len("LAT_HEADER,"):]),
                                source=str(path), line=lineno))
            continue
        if line.startswith("LAT_CHECK,"):
            checks.append(dict(KV_RE.findall(line[len("LAT_CHECK,"):]),
                               source=str(path), line=lineno))
            continue
        if line.startswith("LAT_END,"):
            ends.append(dict(KV_RE.findall(line[len("LAT_END,"):]),
                             source=str(path), line=lineno))
            continue
        if line.startswith("LAT_CHAIN,"):
            chains.append(dict(KV_RE.findall(line[len("LAT_CHAIN,"):]),
                               source=str(path), line=lineno))
            continue
        m = LAT_RE.match(line)
        if not m:
            continue
        fields = dict(KV_RE.findall(m.group(1)))
        try:
            row = {
                "entry": fields["entry"],
                "k": int(fields["K"]),
                "round": int(fields["round"]) if "round" in fields else -1,
                "phase": fields.get("phase", "?"),
                "calls": int(fields.get("calls", "0")),
                "begin": int(fields["begin"]),
                "cycles": int(fields["cycles"]),
                "checksum": fields.get("checksum", ""),
                "chain_checksum": fields.get("chain_checksum", ""),
                "source": str(path),
                "line": lineno,
            }
        except (KeyError, ValueError):
            continue
        windows.append(row)
    return windows, headers, checks, ends, chains


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return float("nan")
    if n % 2:
        return float(ordered[n // 2])
    return 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])


def mean(values):
    return sum(values) / len(values) if values else float("nan")


def stdev(values):
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def robust_cv(values):
    """MAD-based coefficient of variation (0 when the bulk is identical).

    A steady phase can still contain one warm round (the indirect branch
    predictor needs a few iterations for cheap call chains).  The classic CV
    reports that one round as huge jitter; the scaled MAD reports the spread of
    the bulk.  Both stay in windows.csv, only the robust one gates.
    """
    if len(values) < 2:
        return 0.0
    med = median(values)
    if med == 0:
        return 0.0
    mad = median([abs(v - med) for v in values])
    return 1.4826 * mad / med


def linfit(xs, ys):
    """OLS y = a*x + b; returns (a, b, r2)."""
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return float("nan"), float("nan"), float("nan")
    a = sxy / sxx
    b = my - a * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    if ss_tot == 0:
        r2 = 1.0 if ss_res == 0 else 0.0
    else:
        r2 = 1.0 - ss_res / ss_tot
    return a, b, r2


def aggregate(windows, phase):
    """Group steady windows: (entry, k) -> list of cycles."""
    groups = {}
    for row in windows:
        if phase != "all" and row["phase"] != phase:
            continue
        groups.setdefault((row["entry"], row["k"]), []).append(row["cycles"])
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Parse raw LAT windows and fit cycles(K) per entry.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--log", action="append", default=[],
                    help="raw stdout log(s) containing LAT records")
    ap.add_argument("--run", action="append", default=[],
                    help="run directory (uses its stdout.log)")
    ap.add_argument("--output-dir", required=True,
                    help="new directory for samples.csv/windows.csv/fit.csv")
    ap.add_argument("--phase", default=PHASE_STEADY,
                    choices=["steady", "warmup", "first", "all"],
                    help="which phase feeds the medians and the fit")
    args = ap.parse_args(argv)

    logs = [Path(p) for p in args.log]
    for run in args.run:
        candidate = Path(run) / "stdout.log"
        if not candidate.is_file():
            raise SystemExit(f"missing stdout.log in run directory: {run}")
        logs.append(candidate)
    if not logs:
        raise SystemExit("no --log or --run given")
    outdir = Path(args.output_dir)
    if outdir.exists() and any(outdir.iterdir()):
        raise SystemExit(f"output directory already in use: {outdir}")
    outdir.mkdir(parents=True, exist_ok=True)

    windows, headers, checks, ends, chains = [], [], [], [], []
    for path in logs:
        if not path.is_file():
            raise SystemExit(f"missing log: {path}")
        w, h, c, e, ch = parse_log(path)
        windows.extend(w)
        headers.extend(h)
        checks.extend(c)
        ends.extend(e)
        chains.extend(ch)
    if not windows:
        raise SystemExit("no LAT records found")

    # Round order is implicit in the raw log: the LAT_ROUNDS windows of one
    # (entry, K) are printed in round order.  Materialise it so samples.csv can
    # be joined on (entry, k, round) and cross-checked against `phase`.
    seen = {}
    for row in windows:
        if row["round"] >= 0:
            continue
        key = (row["entry"], row["k"], str(row["source"]))
        row["round"] = seen.get(key, 0)
        seen[key] = row["round"] + 1

    selected = windows
    if args.phase != "all":
        selected = [w for w in windows if w["phase"] == args.phase]
    if not selected:
        raise SystemExit(f"no windows in phase {args.phase}")

    with open(outdir / "samples.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["entry", "k", "round", "phase", "calls", "begin",
                         "cycles", "checksum", "chain_checksum",
                         "run_usable", "source", "line"])
        end_ok = bool(ends) and all(e.get("status") == "ok" for e in ends)
        for row in windows:
            writer.writerow([row["entry"], row["k"], row["round"],
                             row["phase"], row["calls"], row["begin"],
                             row["cycles"], row["checksum"],
                             row["chain_checksum"],
                             "yes" if end_ok else "no",
                             row["source"], row["line"]])

    groups = aggregate(windows, args.phase)
    window_rows = []
    for (entry, k), cycles in sorted(groups.items()):
        med = median(cycles)
        sd = stdev(cycles)
        window_rows.append({
            "entry": entry, "k": k, "n": len(cycles),
            "median_cycles": med, "mean_cycles": mean(cycles),
            "stdev_cycles": sd, "cv": (sd / mean(cycles)) if mean(cycles) else 0.0,
            "rcv": robust_cv(cycles),
            "min_cycles": min(cycles), "max_cycles": max(cycles),
        })
    if chains:
        with open(outdir / "chains.csv", "w", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["entry", "k", "chain_checksum", "stable",
                                "source", "line"])
            writer.writeheader()
            for row in chains:
                writer.writerow({"entry": row.get("entry", ""),
                                 "k": row.get("K", ""),
                                 "chain_checksum": row.get("chain_checksum", ""),
                                 "stable": row.get("stable", ""),
                                 "source": row.get("source", ""),
                                 "line": row.get("line", "")})

    with open(outdir / "windows.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(window_rows[0].keys()))
        writer.writeheader()
        writer.writerows(window_rows)

    fit_rows = []
    for entry in sorted({row["entry"] for row in window_rows}):
        pts = [(row["k"], row["median_cycles"]) for row in window_rows
               if row["entry"] == entry]
        pts.sort()
        # K = 0 measures an empty window (no loop, no chain) and is a different
        # configuration: it is reported as an independent overhead point, not
        # used to anchor the chain-bearing fit.
        fit_pts = [(k, y) for k, y in pts if k > 0]
        k0 = next((y for k, y in pts if k == 0), float("nan"))
        xs = [k for k, _ in fit_pts]
        ys = [y for _, y in fit_pts]
        slope, intercept, r2 = linfit(xs, ys)
        _, _, r2_all = linfit([k for k, _ in pts], [y for _, y in pts])
        max_rel = 0.0
        max_abs = 0.0
        for k, y in fit_pts:
            fitted = slope * k + intercept
            resid = abs(y - fitted)
            max_abs = max(max_abs, resid)
            if fitted > 0:
                max_rel = max(max_rel, resid / fitted)
        cvs = [row["cv"] for row in window_rows
               if row["entry"] == entry and row["k"] > 0]
        rcvs = [row["rcv"] for row in window_rows
                if row["entry"] == entry and row["k"] > 0]
        chain_rows = [row for row in chains if row.get("entry") == entry]
        chain_stable = ("yes" if chain_rows
                        and all(row.get("stable") == "yes"
                                for row in chain_rows) else "no")
        fit_rows.append({
            "entry": entry,
            "n_k": len(pts),
            "k_min": min(xs),
            "k_max": max(xs),
            "slope_cycles_per_call": slope,
            "intercept_cycles": intercept,
            "r2": r2,
            "r2_all_k": r2_all,
            "max_rel_residual": max_rel,
            "max_abs_residual_cycles": max_abs,
            "k0_cycles": k0,
            "k0_minus_intercept": (k0 - intercept) if k0 == k0 else float("nan"),
            "cv_max": max(cvs) if cvs else 0.0,
            "rcv_max": max(rcvs) if rcvs else 0.0,
            "chain_stable": chain_stable,
            "phase": args.phase,
            "source": str(logs[0]),
        })
    with open(outdir / "fit.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fit_rows[0].keys()))
        writer.writeheader()
        writer.writerows(fit_rows)

    print(f"# phase={args.phase} windows={len(selected)} entries={len(fit_rows)}")
    print(f"{'entry':<20}{'slope':>10}{'intercept':>11}{'r2':>9}"
          f"{'resid%':>8}{'cv%':>7}{'nK':>3}")
    for row in fit_rows:
        print(f"{row['entry']:<20}{row['slope_cycles_per_call']:>10.3f}"
              f"{row['intercept_cycles']:>11.2f}{row['r2']:>9.5f}"
              f"{100 * row['max_rel_residual']:>8.3f}"
              f"{100 * row['cv_max']:>7.3f}{row['n_k']:>3}")
    print(f"# wrote {outdir / 'samples.csv'}")
    print(f"# wrote {outdir / 'windows.csv'}")
    print(f"# wrote {outdir / 'fit.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
