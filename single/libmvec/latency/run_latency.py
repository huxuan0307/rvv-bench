#!/usr/bin/env python3
"""Run one libmvec latency binary on gsim (fast RTL model) or verilator.

Conventions are copied from the existing libmvec/P3b runners because they are
what the Xiaohsiang RTL harness expects:

* a fresh, timestamped run directory per invocation; an existing directory is
  never reused, and the directory name must carry ``YYYYMMDD-HHMMSS``;
* staged image copy plus double sha256 (source BIN and staged ``image.bin``);
* per-run isolated ``work/`` cwd and ``dram/`` output directory;
* separate stdout/stderr, an external ``timeout`` with SIGINT then SIGKILL;
* explicit seed (123456789), DiffTest reference and DRAMsim3 config, all hashed
  into ``run-metadata.txt`` together with the exact argv.

After the run the raw log is audited for GOOD TRAP, ``LAT_END`` status and
simulator error markers.  The exit status of this tool says whether the run
completed cleanly; it does not interpret cycle numbers (latency_parse.py does).

Example::

    python3 -B single/libmvec/latency/run_latency.py --kind gsim \\
        --bin /path/to/latency-calib.bin \\
        --output /path/to/20261117-020000-latency-gsim --timeout 1800s
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

EMU_ROOT = Path("/nfs/share/huxuan/emus")
REFMODEL_ROOT = Path("/nfs/share/huxuan/refmodel")
DEFAULT_REFERENCE = REFMODEL_ROOT / "riscv64-nemu-interpreter-so-fixvfredusum"
DEFAULT_DRAM = Path("/nfs/home/share/ci-workloads/DRAMsim3/configs/"
                    "xiangshan_DDR4_8Gb_x8_3200_2ch_xmp.ini")
DEFAULT_SEED = 123456789
STAMP_RE = re.compile(r"\d{8}-\d{6}")
ERROR_MARKERS = (
    "LAT_ERROR", "BAD TRAP", "abort", "Assertion", "Different", "DIFF",
    "panic", "ERROR!", "core dumped",
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_duration(text):
    m = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", str(text))
    if not m:
        raise argparse.ArgumentTypeError(f"bad duration: {text}")
    value = int(m.group(1))
    return value * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def latest_emu(kind):
    """Newest non-static emulator of the requested kind (largest build number)."""
    if not EMU_ROOT.is_dir():
        raise SystemExit(f"emulator root not found: {EMU_ROOT}")
    cands = []
    for path in EMU_ROOT.iterdir():
        name = path.name
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        if kind == "gsim":
            if not name.startswith("emu-gsim-") or "static" in name:
                continue
        else:
            if not name.startswith("emu-verilator-") or "static" in name:
                continue
        cands.append(path)
    if not cands:
        raise SystemExit(f"no {kind} emulator under {EMU_ROOT}")
    return max(cands, key=lambda p: p.stat().st_mtime)


def fail(message):
    sys.stderr.write(f"RUN_FAILED {message}\n")
    return 1


def audit_logs(stdout_text, stderr_text, rc, timed_out):
    """Check the raw log; returns (problems, summary dict)."""
    problems = []
    summary = {}
    m = re.search(r"Core 0:.*?(HIT GOOD TRAP|HIT BAD TRAP)",
                  stdout_text, re.S)
    summary["good_trap"] = bool(m and m.group(1) == "HIT GOOD TRAP")
    if not summary["good_trap"]:
        problems.append("no GOOD TRAP in stdout")
    if rc != 0:
        problems.append(f"emulator exit code {rc}")
    if timed_out:
        problems.append("timed out")

    header = re.search(r"^LAT_HEADER,(.+)$", stdout_text, re.M)
    if not header:
        problems.append("no LAT_HEADER (guest output missing)")
    else:
        summary["header"] = header.group(1)

    end = re.search(r"^LAT_END,status=(\w+),checks=(\d+),failed=(\d+)\s*$",
                    stdout_text, re.M)
    if not end:
        problems.append("no LAT_END (run truncated or guest crashed)")
    else:
        summary["end_status"] = end.group(1)
        summary["checks"] = int(end.group(2))
        summary["failed"] = int(end.group(3))
        if end.group(1) != "ok":
            problems.append(f"LAT_END status={end.group(1)}")
        if int(end.group(3)) != 0:
            problems.append(f"oracle failures: {end.group(3)}")

    summary["lat_windows"] = len(re.findall(r"^LAT,", stdout_text, re.M))
    summary["lat_checks"] = len(re.findall(r"^LAT_CHECK,", stdout_text, re.M))
    if summary["lat_windows"] == 0:
        problems.append("no LAT windows")

    hits = sorted({marker for marker in ERROR_MARKERS
                   if marker in stdout_text or marker in stderr_text})
    summary["error_markers"] = hits
    for marker in hits:
        if marker in ("Different", "DIFF", "BAD TRAP", "LAT_ERROR", "abort"):
            problems.append(f"error marker in log: {marker}")
    cause = re.search(r"cause\s*=\s*(\d+)", stdout_text + stderr_text)
    if cause:
        summary["cause"] = int(cause.group(1))
        problems.append(f"trap cause={cause.group(1)}")
    return problems, summary


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Run one libmvec latency BIN on gsim or verilator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--bin", required=True, help="latency-<tag>.bin to run")
    ap.add_argument("--output", required=True,
                    help="new run directory; must contain YYYYMMDD-HHMMSS")
    ap.add_argument("--kind", choices=["gsim", "verilator"], required=True)
    ap.add_argument("--emu", help="explicit emulator (default: newest of --kind)")
    ap.add_argument("--reference", default=str(DEFAULT_REFERENCE),
                    help="DiffTest reference library")
    ap.add_argument("--dramsim3-ini", default=str(DEFAULT_DRAM))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--timeout", default="7200s",
                    help="external timeout, e.g. 900s, 30m, 2h")
    ap.add_argument("--label", default="", help="suffix recorded in metadata")
    ap.add_argument("--allow-untimestamped", action="store_true",
                    help="accept an output directory without a timestamp")
    args = ap.parse_args(argv)

    timeout_s = parse_duration(args.timeout)
    bin_path = Path(args.bin).expanduser().resolve()
    if not bin_path.is_file():
        raise SystemExit(f"missing BIN: {bin_path}")
    outdir = Path(args.output).expanduser().resolve()
    if outdir.exists():
        raise SystemExit(f"run directory already exists: {outdir}")
    if not args.allow_untimestamped and not STAMP_RE.search(outdir.name):
        raise SystemExit(
            f"run directory must contain YYYYMMDD-HHMMSS: {outdir.name}")
    emu = Path(args.emu).expanduser().resolve() if args.emu else latest_emu(args.kind)
    for path, what in ((emu, "emulator"),
                       (Path(args.reference).expanduser(), "reference"),
                       (Path(args.dramsim3_ini).expanduser(), "dramsim3 ini")):
        if not path.exists():
            raise SystemExit(f"missing {what}: {path}")

    reference = Path(args.reference).expanduser().resolve()
    dram_ini = Path(args.dramsim3_ini).expanduser().resolve()

    (outdir / "work").mkdir(parents=True)
    (outdir / "dram").mkdir()
    staged = outdir / "image.bin"
    shutil.copy2(bin_path, staged)
    src_sha, staged_sha = sha256(bin_path), sha256(staged)
    if src_sha != staged_sha:
        raise SystemExit("staged image hash mismatch")
    (outdir / "image-sha256.txt").write_text(
        f"{src_sha}  {bin_path}\n{staged_sha}  {staged}\n")

    cmd = [str(emu), "--diff", str(reference), f"--seed={args.seed}",
           f"--dramsim3-ini={dram_ini}",
           f"--dramsim3-outdir={outdir / 'dram'}",
           "-i", str(staged)]
    (outdir / "command.txt").write_text(" ".join(cmd) + "\n")
    meta = [
        f"kind={args.kind}",
        f"label={args.label}",
        f"bin={bin_path}",
        f"bin_sha256={src_sha}",
        f"staged_sha256={staged_sha}",
        f"emu={emu}",
        f"emu_sha256={sha256(emu)}",
        f"reference={reference}",
        f"reference_sha256={sha256(reference)}",
        f"dramsim3_ini={dram_ini}",
        f"dramsim3_ini_sha256={sha256(dram_ini)}",
        f"seed={args.seed}",
        f"timeout_s={timeout_s}",
        f"cwd={outdir / 'work'}",
        f"command={' '.join(cmd)}",
        f"host={os.uname().nodename}",
    ]
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta.append(f"started_at={started}")
    (outdir / "run-metadata.txt").write_text("\n".join(meta) + "\n")

    timed_out = False
    with open(outdir / "stdout.log", "wb") as so, \
            open(outdir / "stderr.log", "wb") as se:
        try:
            proc = subprocess.run(
                ["timeout", "--signal=INT", "--kill-after=60",
                 str(timeout_s), *cmd],
                cwd=str(outdir / "work"), stdout=so, stderr=se)
            rc = proc.returncode
            timed_out = rc in (124, 130, 137)
        except KeyboardInterrupt:
            rc = 130
            timed_out = True
    (outdir / "exit-code.txt").write_text(f"{rc}\n")
    (outdir / "finished-at.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n")

    stdout_text = (outdir / "stdout.log").read_text(errors="replace")
    stderr_text = (outdir / "stderr.log").read_text(errors="replace")
    problems, summary = audit_logs(stdout_text, stderr_text, rc, timed_out)

    lines = [f"run={outdir}", f"kind={args.kind}", f"rc={rc}",
             f"timed_out={timed_out}"]
    for key in ("good_trap", "header", "end_status", "checks", "failed",
                "lat_windows", "lat_checks", "cause"):
        if key in summary:
            lines.append(f"{key}={summary[key]}")
    lines.append("error_markers=" + (",".join(summary["error_markers"]) or "none"))
    lines.append("status=" + ("ok" if not problems else "fail"))
    problems_out = [f"problem={p}" for p in problems]
    (outdir / "run-report.txt").write_text(
        "\n".join(lines + problems_out) + "\n")
    print("\n".join(lines + problems_out))
    if problems:
        return fail("; ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
