#!/usr/bin/env python3
"""libmvec 工作流门禁 CLI：状态、冻结、审批、推进、报告委托。

用法：
    python3 cli.py <command> --task <task-dir> [options]

命令：
    init            初始化 <task>/data/workflow 状态表（不覆盖已有状态）
    freeze          冻结 G0/G1a/G1b 契约与环境键（--set/--file）
    artifact        登记证据 artifact（kind/path/gate/author/role）
    check           校验门禁（无 gate 参数=检查已推进门禁；--gate=就绪预检）
    record-review   记录独立审批（含 provider/model/session/event 与逐项判定）
    advance         推进门禁（fail-closed；需 --expect-revision 与 Lead 核验引用）
    status          输出状态 CSV
    report          委托 reporting.generate 生成新目录（不覆盖）
    import-data     委托 evidence.import_data 导入数据（返回生成路径，不自动登记）

约束：
- 状态根固定 <task>/data/workflow；evidence.py/reporting.py 固定与本文件同目录，
  不允许运行时替换。
- 只读写 CSV/MD；不执行外部命令、不启动仿真、不删除文件。
- 退出码：0=成功；1=门禁阻断；2=输入/工具错误。stdout 只输出 CSV，stderr 输出诊断。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import core


def _csv_out(header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(list(header))
    for row in rows:
        writer.writerow([str(cell) for cell in row])


def _parse_items(values: Sequence[str]) -> List[Tuple[str, str, str, str]]:
    items: List[Tuple[str, str, str, str]] = []
    for raw in values:
        parts = raw.split(":", 3)
        if len(parts) < 3:
            raise core.WorkflowError(
                f"--item 需要 id:judgement:reason[:evidence_id]，收到 {raw!r}")
        item_id, judgement, reason = parts[0], parts[1], parts[2]
        evidence_id = parts[3] if len(parts) > 3 else ""
        items.append((item_id, judgement, reason, evidence_id))
    return items


def _parse_freeze(values: Sequence[str], mode: str) -> List[Tuple[str, str, str]]:
    entries: List[Tuple[str, str, str]] = []
    for raw in values:
        if "=" not in raw:
            raise core.WorkflowError(
                f"--{mode} 需要 key=value，收到 {raw!r}")
        key, value = raw.split("=", 1)
        entries.append((mode, key, value))
    return entries


def cmd_init(task: core.Task, args: argparse.Namespace) -> int:
    result = task.init(author=args.author)
    _csv_out(("state_dir", "revision"),
             [(result["state_dir"], result["revision"])])
    return 0


def cmd_freeze(task: core.Task, args: argparse.Namespace) -> int:
    entries = _parse_freeze(args.set or [], "set")
    entries += _parse_freeze(args.file or [], "file")
    if not entries:
        raise core.WorkflowError("freeze 至少需要一个 --set 或 --file")
    result = task.freeze(entries, gate=args.gate, author=args.author)
    _csv_out(("freeze_id", "gate", "keys"),
             [(result["freeze_id"], result["gate"], " ".join(result["keys"]))])
    return 0


def cmd_artifact(task: core.Task, args: argparse.Namespace) -> int:
    result = task.artifact(
        kind=args.kind, path=args.path, gate=args.gate, author=args.author,
        role=args.role, artifact_id=args.artifact_id,
        supersedes=args.supersedes, note=args.note,
        source_sha256=args.source_sha256)
    _csv_out(("artifact_id", "gate", "kind", "path", "sha256"),
             [(result["artifact_id"], result["gate"], result["kind"],
               result["path"], result["sha256"])])
    return 0


def cmd_check(task: core.Task, args: argparse.Namespace) -> int:
    problems = task.check(gate=args.gate, evidence_only=args.evidence_only)
    _csv_out(("problem",), [(item,) for item in problems])
    return 0 if not problems else 1


def cmd_record_review(task: core.Task, args: argparse.Namespace) -> int:
    result = task.record_review(
        gate=args.gate, reviewer=args.reviewer, provider=args.provider,
        model=args.model, reviewer_role=args.role, scope=args.scope,
        verdict=args.verdict, session_id=args.session_id,
        event_from=args.event_from, event_to=args.event_to,
        items=_parse_items(args.item or []), note=args.note)
    _csv_out(("review_id", "gate", "target_digest"),
             [(result["review_id"], result["gate"], result["target_digest"])])
    return 0


def cmd_advance(task: core.Task, args: argparse.Namespace) -> int:
    row = task.advance(
        gate=args.gate, author=args.author,
        expect_revision=args.expect_revision,
        lead_verification=args.lead_verification, note=args.note)
    _csv_out(("seq", "gate", "status", "evidence_digest", "row_hash"),
             [(row["seq"], row["gate"], row["status"],
               row["evidence_digest"], row["row_hash"])])
    return 0


def cmd_status(task: core.Task, args: argparse.Namespace) -> int:
    rows = task.status()
    header = ("gate", "status", "attempt", "created_at", "author", "review_id",
              "lead_verified", "evidence_digest8", "upstream_digest8", "stale",
              "next_action")
    _csv_out(header, [[row[key] for key in header] for row in rows])
    return 0


def cmd_report(task: core.Task, args: argparse.Namespace) -> int:
    produced = task.report(Path(args.output))
    _csv_out(("produced",), [(item,) for item in produced])
    return 0


def cmd_import_data(task: core.Task, args: argparse.Namespace) -> int:
    produced = task.import_data(kind=args.kind, source=args.source,
                                output=args.output)
    _csv_out(("produced",), [(item,) for item in produced])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="libmvec 工作流门禁 CLI（CSV 状态、独立审批、失效传播）",
        epilog="退出码：0=成功；1=门禁阻断；2=输入/工具错误。"
               "状态根固定 <task>/data/workflow。")
    parser.add_argument("--version", action="version",
                        version=f"workflow {core.TOOL_VERSION} "
                                f"contract={core.CONTRACT_VERSION} "
                                f"schema={core.SCHEMA_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(name: str, help_text: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text, description=help_text)
        item.add_argument("--task", required=True,
                          help="任务目录（状态写入 <task>/data/workflow）")
        return item

    p = common("init", "初始化状态表")
    p.add_argument("--author", required=True, help="初始化人（agent/角色名）")

    p = common("freeze", "冻结契约/环境键")
    p.add_argument("--gate", required=True, choices=("G0", "G1a", "G1b"))
    p.add_argument("--author", required=True)
    p.add_argument("--set", action="append", default=[],
                   metavar="KEY=VALUE", help="直接冻结值（非 *_sha256 键）")
    p.add_argument("--file", action="append", default=[],
                   metavar="KEY=PATH", help="引用文件并冻结其 sha256（必填 *_sha256）")

    p = common("artifact", "登记证据 artifact")
    p.add_argument("--gate", required=True, choices=core.GATES)
    p.add_argument("--kind", required=True, choices=core.ALL_KINDS)
    p.add_argument("--path", required=True, help="任务相对路径")
    p.add_argument("--author", required=True)
    p.add_argument("--role", default="evidence_author",
                   choices=core.ARTIFACT_ROLES)
    p.add_argument("--artifact-id", default="",
                   help="可选；字母开头，仅字母数字_.-")
    p.add_argument("--supersedes", default="",
                   help="替代旧 artifact id（逗号分隔）；缺省时同 path+kind 的当前"
                        "登记自动被替代")
    p.add_argument("--source-sha256", default="")
    p.add_argument("--note", default="")

    p = common("check", "校验门禁")
    p.add_argument("--gate", default=None, choices=core.GATES,
                   help="省略时检查所有已推进门禁")
    p.add_argument("--evidence-only", action="store_true",
                   help="只做技术/证据检查，不含审批要求（record-review 前预检）")

    p = common("record-review", "记录独立审批")
    p.add_argument("--gate", required=True, choices=core.GATES)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--provider", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--role", required=True, choices=core.REVIEWER_ROLES)
    p.add_argument("--scope", required=True, help="审查范围（文件/矩阵/表格）")
    p.add_argument("--verdict", required=True, choices=core.VERDICTS)
    p.add_argument("--session-id", required=True)
    p.add_argument("--event-from", required=True, type=int)
    p.add_argument("--event-to", required=True, type=int)
    p.add_argument("--item", action="append", default=[],
                   metavar="ID:JUDGEMENT:REASON[:EVIDENCE_ID]",
                   help="逐项判定；judgement=pass|fail|na")
    p.add_argument("--note", default="")

    p = common("advance", "推进门禁（fail-closed）")
    p.add_argument("--gate", required=True, choices=core.GATES)
    p.add_argument("--author", required=True)
    p.add_argument("--expect-revision", required=True, type=int,
                   help="必须等于当前 state.seq 最大值（并发/重放保护）")
    p.add_argument("--lead-verification", required=True,
                   help="Lead 对独立审批的核验引用（消息/事件标识）")
    p.add_argument("--note", default="")

    common("status", "输出状态 CSV")

    p = common("report", "委托 reporting.generate 生成报告目录")
    p.add_argument("--output", required=True, help="任务内新目录（不得非空）")

    p = common("import-data", "委托 evidence.import_data 导入数据（适配器 kind）")
    kinds = core.Task.import_kinds()
    adapter_help = "evidence 适配器名"
    if kinds:
        adapter_help += "：" + ", ".join(kinds)
    p.add_argument("--kind", required=True, help=adapter_help)
    p.add_argument("--source", required=True, help="已有原始数据路径（evidence 解释）")
    p.add_argument("--output", required=True, help="输出路径（evidence 解释）")
    return parser


HANDLERS = {
    "init": cmd_init,
    "freeze": cmd_freeze,
    "artifact": cmd_artifact,
    "check": cmd_check,
    "record-review": cmd_record_review,
    "advance": cmd_advance,
    "status": cmd_status,
    "report": cmd_report,
    "import-data": cmd_import_data,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        task = core.Task.load(Path(args.task))
        return HANDLERS[args.command](task, args)
    except core.BlockedError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except core.WorkflowError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, csv.Error) as exc:
        # 损坏/不可读 CSV、UnicodeDecodeError（ValueError 子类）、IO 错误：
        # 属输入/工具错误，退出 2，避免与门禁阻断 1 混淆或 traceback。
        print(f"E-INPUT: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("E-INTERRUPTED: 用户中断", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
