#!/usr/bin/env python3
"""libmvec 工作流门禁核心：状态、证据索引、独立审批与失效传播。

设计约束（Lead 批准口径）
- 状态只落 CSV（meta/state/artifacts/freeze/reviews/review_items），不产生 JSON。
- state.csv 追加写，row_hash 链自校验；证据摘要覆盖当前有效 artifacts、最新 freeze、
  本门禁标准证据表、上游绑定与工具源码 hash，不引用 state 自身（无循环）。
- 所有状态突变持 `<task>/data/workflow/.lock` 排它锁；advance 在锁内重读 revision。
- fail-closed：缺项、空值、重复、未知值、自审、N/A 无理由、checklist 未覆盖、
  证据/工具/上游变化、未初始化、符号链接越界全部阻断。
- 不执行外部命令、不启动仿真、不删除文件。
- 退出码：0 成功；1 门禁阻断；2 输入/工具错误（见 cli.py）。

对外接口
    Task.load(task_dir)
    Task.init(author)
    Task.freeze(entries, gate, author)
    Task.artifact(kind, path, gate, author, role, artifact_id="", supersedes="", ...)
    Task.check(gate=None) -> list[str]
    Task.record_review(...)
    Task.advance(gate, author, expect_revision, lead_verification)
    Task.status() -> list[dict]
    Task.report(output_dir)
    Task.import_data(kind, source, output, author, gate)
    Task.require_gate(gate)
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import importlib.util
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 版本与常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1"
CONTRACT_VERSION = "libmvec-gates-1"
TOOL_VERSION = "1"

GATES: Tuple[str, ...] = ("G0", "G1a", "G1b", "G2", "G3", "G4", "G5", "G6")
GATE_ORDER: Dict[str, int] = {g: i for i, g in enumerate(GATES)}
GATE_TITLES: Dict[str, str] = {
    "G0": "契约、范围与预注册矩阵冻结",
    "G1a": "设计与测量协议冻结",
    "G1b": "同源基线与环境冻结（先导验证）",
    "G2": "候选预筛",
    "G3": "最终正确性与 ABI",
    "G4": "最终性能",
    "G5": "数据与报告",
    "G6": "交付与审批冻结",
}
GATE_UPSTREAM: Dict[str, Optional[str]] = {
    "G0": None, "G1a": "G0", "G1b": "G1a", "G2": "G1b",
    "G3": "G2", "G4": "G3", "G5": "G4", "G6": "G5",
}

GATE_EVIDENCE_KINDS: Dict[str, Tuple[str, ...]] = {
    "G0": ("contract", "target_matrix"),
    "G1a": ("design", "measurement_protocol"),
    "G1b": ("environment", "baseline"),
    "G2": ("screening",),
    "G3": ("coverage",),
    "G4": ("runs", "measurements"),
    "G5": ("claims", "report"),
    "G6": ("delivery",),
}

# 标准证据表（放在 task/data/workflow/，由 evidence.py 读取与内容校验）
STANDARD_TABLES: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "G0": (("cases.csv", "target_matrix"),),
    "G3": (("coverage.csv", "coverage"),),
    "G4": (("runs.csv", "runs"), ("measurements.csv", "measurements")),
    "G5": (("claims.csv", "claims"),),
}

# 需要 evidence.check_task 的门禁（按 gate 范围校验，不提前阻断早期门禁）
GATES_NEEDING_EVIDENCE = frozenset(("G0", "G1a", "G1b", "G2", "G3", "G4", "G5", "G6"))

ALL_KINDS: Tuple[str, ...] = (
    "contract", "design", "measurement_protocol", "target_matrix",
    "environment", "tool", "baseline", "screening", "coverage", "runs",
    "measurements", "claims", "report", "delivery", "validation", "raw",
    "source", "bug", "review", "code", "data", "log", "process", "build",
    "consumer_lto", "consumer_lto_sdk", "consumer_lto_bundle",
)
PROCESS_KINDS = frozenset(("log", "process", "build"))
ARTIFACT_ROLES: Tuple[str, ...] = (
    "code_author", "evidence_author", "data_import", "report_author",
    "delivery", "environment",
)
REVIEWER_ROLES: Tuple[str, ...] = ("code", "semantic", "data", "process")

CASE_STATUS: Tuple[str, ...] = (
    "pass", "fail", "skip", "not_run", "not_applicable")
JUDGEMENTS: Tuple[str, ...] = ("pass", "fail", "na")
VERDICTS: Tuple[str, ...] = ("approve", "request_changes", "blocked")

# 每门禁最少独立（非作者、不同 reviewer、不同角色）approve 数
REQUIRED_REVIEWERS: Dict[str, int] = {
    "G0": 1, "G1a": 1, "G1b": 1, "G2": 1,
    "G3": 2, "G4": 1, "G5": 2, "G6": 2,
}

# 首版 checklist 编号（稳定 ID）；若 data/workflow/requirements.csv 存在（G0 登记）
# 则以其中 gate,item_id 覆盖，实现契约驱动的完整覆盖要求。
# 编号与 docs/checklist.md 一致（单源；requirements.csv 只能追加，不能删减默认项）
REQUIRED_ITEMS: Dict[str, Tuple[str, ...]] = {
    "G0": ("G0-01", "G0-02", "G0-03", "G0-04"),
    "G1a": ("G1A-01", "G1A-02", "G1A-03"),
    "G1b": ("G1B-01", "G1B-02", "G1B-03", "G1B-04"),
    "G2": ("G2-01", "G2-02", "G2-03"),
    "G3": ("G3-01", "G3-02", "G3-03", "G3-04", "G3-05"),
    "G4": ("G4-01", "G4-02", "G4-03", "G4-04", "G4-05"),
    "G5": ("G5-01", "G5-02", "G5-03", "G5-04"),
    "G6": ("G6-01", "G6-02", "G6-03"),
}

FREEZE_KEYS: Dict[str, Tuple[str, ...]] = {
    "G0": ("contract_sha256", "scope_sha256", "target_matrix_sha256"),
    "G1a": ("design_sha256", "protocol_sha256"),
    "G1b": ("repo", "commit", "diff_sha256", "toolchain", "emu", "refmodel",
            "inputs_sha256", "oracle_sha256", "seed"),
}
FREEZE_KEY_GATE: Dict[str, str] = {
    key: gate for gate, keys in FREEZE_KEYS.items() for key in keys
}
FILE_ONLY_FREEZE = frozenset((
    "contract_sha256", "scope_sha256", "target_matrix_sha256",
    "design_sha256", "protocol_sha256", "inputs_sha256", "oracle_sha256",
))
SHA_OR_FILE_FREEZE = frozenset(("diff_sha256",))  # 干净树用空 blob sha256
KEY_ARTIFACT_KIND: Dict[str, str] = {
    "contract_sha256": "contract",
    "target_matrix_sha256": "target_matrix",
    "design_sha256": "design",
    "protocol_sha256": "measurement_protocol",
}

LAYOUT_FILES = frozenset((
    "plan.md", "archive.md", "roadmap.md", "progress.md", "idea.md",
    "changelog.md", "ref.md", ".layout-allow",
))
LAYOUT_DIRS = frozenset((
    "tmp", "log", "script", "data", "report", "bug", "build",
    "build-recipes", "worktree",
))

STATE_SUBDIR = Path("data") / "workflow"
TOOL_FILES = ("core.py", "cli.py", "evidence.py", "reporting.py")
VALID_REF_COLUMNS = frozenset((
    "case_id", "claim_id", "run_id", "sample_id", "item_id", "artifact_id",
))

META_HEADER = ("key", "value")
STATE_HEADER = (
    "seq", "gate", "status", "attempt", "created_at", "author",
    "upstream_digest", "evidence_digest", "review_id", "review_binding", "freeze_id",
    "lead_verification", "note", "prev_hash", "row_hash",
)
ARTIFACT_HEADER = (
    "artifact_id", "gate", "kind", "path", "sha256", "bytes", "author",
    "role", "created_at", "source_sha256", "supersedes", "note",
)
FREEZE_HEADER = (
    "freeze_id", "key", "value", "sha256", "source", "tool_version",
    "contract_version", "created_at", "author", "note",
)
REVIEW_HEADER = (
    "review_id", "gate", "reviewer", "provider", "model", "reviewer_role",
    "author_scope", "target_digest", "verdict", "session_id",
    "event_seq_from", "event_seq_to", "lead_verified", "lead_verifier",
    "created_at", "revision", "note",
)
REVIEW_ITEM_HEADER = (
    "review_id", "item_id", "judgement", "reason", "evidence_id")

TABLES: Dict[str, Tuple[str, ...]] = {
    "meta": META_HEADER,
    "state": STATE_HEADER,
    "artifacts": ARTIFACT_HEADER,
    "freeze": FREEZE_HEADER,
    "reviews": REVIEW_HEADER,
    "review_items": REVIEW_ITEM_HEADER,
}

GENESIS_HASH = "0" * 64


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class WorkflowError(Exception):
    """输入、工具或模块错误：退出码 2。"""
    exit_code = 2
    code = "E-INPUT"


class BlockedError(WorkflowError):
    """门禁不满足：退出码 1。"""
    exit_code = 1
    code = "E-BLOCKED"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def canonical(value: Any) -> str:
    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b:1" if value else "b:0"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, str):
        return f"s:{len(value)}:{value}"
    if isinstance(value, (list, tuple)):
        return "l[" + "".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        return "d{" + "".join(
            f"{canonical(str(k))}={canonical(v)}" for k, v in sorted(value.items())
        ) + "}"
    raise WorkflowError(f"无法规范化类型 {type(value).__name__}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_time(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorkflowError(f"时间格式非法: {text!r}（{exc}）") from exc
    if value.tzinfo is None:
        raise WorkflowError(f"时间缺少时区: {text!r}")
    return value


def format_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise WorkflowError("时间必须带时区")
    return value.isoformat(timespec="seconds")


def _read_table(path: Path, header: Sequence[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        try:
            got = next(reader)
        except StopIteration as exc:
            raise WorkflowError(f"{path.name}: 空文件，缺少表头") from exc
        if got != list(header):
            raise WorkflowError(
                f"{path.name}: 表头不匹配；期望 {list(header)}，实际 {got}")
        for lineno, record in enumerate(reader, start=2):
            if not record:
                raise WorkflowError(f"{path.name}:{lineno}: 空行不被接受")
            if len(record) != len(header):
                raise WorkflowError(
                    f"{path.name}:{lineno}: 列数 {len(record)} != {len(header)}")
            rows.append(dict(zip(header, record)))
    return rows


def _write_header(path: Path, header: Sequence[str]) -> None:
    if path.exists():
        raise WorkflowError(f"{path.name}: 已存在，拒绝覆盖")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerow(list(header))
    os.replace(tmp, path)


def _append_row(path: Path, header: Sequence[str], row: Dict[str, str]) -> None:
    if set(row) != set(header):
        raise WorkflowError(
            f"{path.name}: 行字段不匹配；缺 {sorted(set(header) - set(row))} "
            f"多 {sorted(set(row) - set(header))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([row[key] for key in header])
        stream.flush()
        os.fsync(stream.fileno())


def _text(value: Any, field: str) -> str:
    text = ("" if value is None else str(value)).strip()
    if not text:
        raise WorkflowError(f"{field} 不能为空")
    return text


def _enum(value: str, allowed: Sequence[str], field: str) -> str:
    text = ("" if value is None else str(value)).strip()
    if text not in allowed:
        raise WorkflowError(f"{field}={text!r} 非法；允许 {list(allowed)}")
    return text


def _load_module(path: Path) -> Any:
    if not path.is_file():
        return None
    name = "libmvec_workflow_" + path.stem + "_" + sha256_text(str(path))[:8]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class Task:
    """一个任务的 workflow 状态。状态根固定为 <task>/data/workflow。"""

    def __init__(self, task_dir: Path) -> None:
        self.dir = Path(task_dir).resolve()
        self.state = self.dir / STATE_SUBDIR
        self._evidence_path = Path(__file__).resolve().with_name("evidence.py")
        self._reporting_path = Path(__file__).resolve().with_name("reporting.py")
        self._cache: Dict[str, Any] = {}
        self._assert_state_paths()

    @property
    def root(self) -> Path:
        return self.dir

    @classmethod
    def load(cls, task_dir: Path) -> "Task":
        task = cls(task_dir)
        if not task.dir.is_dir():
            raise WorkflowError(f"任务目录不存在: {task.dir}")
        return task

    # -- 路径边界（输入路径约束，不做安全对抗假设） -------------------
    def _assert_state_paths(self) -> None:
        """状态目录及其 CSV/锁文件解析后必须仍在任务目录内。

        在目录尚未创建时也要检查（data/ 可能是逃逸符号链接）；存在的状态文件
        不允许本身是符号链接，防止读写落到任务外。
        """
        resolved = self.state.resolve()
        if resolved != self.dir and self.dir not in resolved.parents:
            raise WorkflowError(
                f"状态目录解析后越出任务目录: {self.state} -> {resolved}")
        for path in (self.state / ".lock",) + tuple(
                self.state / f"{name}.csv" for name in TABLES):
            if path.is_symlink():
                raise WorkflowError(f"状态文件不得是符号链接: {path}")
            if path.exists() and self.dir not in path.resolve().parents:
                raise WorkflowError(
                    f"状态文件解析后越出任务目录: {path} -> {path.resolve()}")

    # -- 锁 -----------------------------------------------------------
    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._assert_state_paths()
        self.state.mkdir(parents=True, exist_ok=True)
        lock_path = self.state / ".lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # -- 路径 ---------------------------------------------------------
    def path(self, rel: str) -> Path:
        return (self.dir / rel).resolve()

    def _resolve_rel(self, path_text: str, kind: str) -> str:
        raw = _text(path_text, "path")
        candidate = Path(raw)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            rel = Path(os.path.normpath(raw)).as_posix()
            if rel == ".." or rel.startswith("../"):
                raise WorkflowError(f"path 越出任务目录: {raw}")
            target = (self.dir / rel).resolve()
        if target != self.dir and self.dir not in target.parents:
            raise WorkflowError(
                f"path 解析后越出任务目录（含符号链接）: {raw} -> {target}")
        rel = target.relative_to(self.dir).as_posix()
        if kind not in PROCESS_KINDS and rel.split("/")[0] in ("tmp", "log"):
            raise WorkflowError(
                f"E-ARTIFACT-PROCESS-PATH: kind={kind} 不得指向 {rel}")
        return rel

    def _table(self, name: str) -> Path:
        return self.state / f"{name}.csv"

    def _std_table(self, filename: str) -> Path:
        return self.state / filename

    # -- 读取 ---------------------------------------------------------
    def read(self, name: str) -> List[Dict[str, str]]:
        self._assert_state_paths()
        if name not in TABLES:
            raise WorkflowError(f"未知表: {name}")
        return _read_table(self._table(name), TABLES[name])

    def current_artifacts(self) -> List[Dict[str, str]]:
        rows = self.read("artifacts")
        superseded = {r["supersedes"] for r in rows if r["supersedes"]}
        for row in rows:
            for old in row["supersedes"].split(","):
                if old:
                    superseded.add(old)
        return [r for r in rows if r["artifact_id"] not in superseded]

    def artifacts(self, gate: Optional[str] = None) -> List[Dict[str, str]]:
        rows = self.current_artifacts()
        return [r for r in rows if gate is None or r["gate"] == gate]

    def freeze_rows(self, gate: Optional[str] = None) -> List[Dict[str, str]]:
        rows = self.read("freeze")
        if gate is None:
            return rows
        allowed = set(FREEZE_KEYS.get(gate, ()))
        return [r for r in rows if r["key"] in allowed]

    def reviews(self, gate: Optional[str] = None) -> List[Dict[str, str]]:
        return [r for r in self.read("reviews")
                if gate is None or r["gate"] == gate]

    def review_items(self, review_id: str) -> List[Dict[str, str]]:
        return [r for r in self.read("review_items")
                if r["review_id"] == review_id]

    def state_rows(self) -> List[Dict[str, str]]:
        return self.read("state")

    # -- meta ---------------------------------------------------------
    def meta(self) -> Dict[str, str]:
        rows = self.read("meta")
        keys = [row["key"] for row in rows]
        if len(keys) != len(set(keys)):
            raise WorkflowError("E-DUPLICATE: meta.csv 出现重复键")
        return {row["key"]: row["value"] for row in rows}

    def _meta_problems(self) -> List[str]:
        if not self._table("meta").is_file():
            return ["E-STATE-MISSING: 未初始化（先运行 init）"]
        problems: List[str] = []
        meta = self.meta()
        for key, expected in (
            ("schema_version", SCHEMA_VERSION),
            ("contract_version", CONTRACT_VERSION),
            ("tool_version", TOOL_VERSION),
        ):
            actual = meta.get(key, "")
            if not actual:
                problems.append(f"E-META-EMPTY: meta.{key} 缺失")
            elif actual != expected:
                problems.append(
                    f"E-META-VERSION: meta.{key}={actual} 与当前 {expected} 不匹配")
        task_path = meta.get("task_path", "")
        if task_path and task_path != str(self.dir):
            problems.append(
                f"E-META-TASK-PATH: meta.task_path={task_path} 与当前 {self.dir} "
                "不符（禁止复制任务目录后沿用状态）")
        return problems

    def _require_init(self) -> None:
        self._assert_state_paths()
        if not self._table("meta").is_file():
            raise WorkflowError(f"E-STATE-MISSING: 未初始化: {self.state}")

    # -- init ---------------------------------------------------------
    def init(self, author: str) -> Dict[str, str]:
        author = _text(author, "author")
        with self._lock():
            self._assert_state_paths()
            if self.state.exists() and any(
                    p.name != ".lock" for p in self.state.iterdir()):
                raise BlockedError(f"E-INIT-EXISTS: 状态目录非空 {self.state}")
            for name, header in TABLES.items():
                _write_header(self._table(name), header)
            moment = format_time(now_local())
            for key, value in (
                ("schema_version", SCHEMA_VERSION),
                ("contract_version", CONTRACT_VERSION),
                ("tool_version", TOOL_VERSION),
                ("task_path", str(self.dir)),
                ("created_at", moment),
                ("created_by", author),
            ):
                _append_row(self._table("meta"), META_HEADER,
                            {"key": key, "value": value})
        return {"state_dir": str(self.state), "revision": "0"}

    # -- revision / state 链 -----------------------------------------
    def revision(self) -> int:
        rows = self.state_rows()
        if not rows:
            return 0
        try:
            return max(int(row["seq"]) for row in rows)
        except ValueError as exc:
            raise WorkflowError(f"state.seq 非整数: {exc}") from exc

    def gate_row(self, gate: str) -> Optional[Dict[str, str]]:
        rows = [r for r in self.state_rows() if r["gate"] == gate]
        return rows[-1] if rows else None

    def gate_status(self, gate: str) -> Optional[str]:
        row = self.gate_row(gate)
        return row["status"] if row else None

    def _state_chain_problems(self) -> List[str]:
        problems: List[str] = []
        rows = self.state_rows()
        previous = GENESIS_HASH
        seen_seq = set()
        for index, row in enumerate(rows, start=2):
            if row["seq"] in seen_seq:
                problems.append(f"E-DUPLICATE: state.seq 重复 {row['seq']}")
            seen_seq.add(row["seq"])
            if row["prev_hash"] != previous:
                problems.append(
                    f"E-SCHEMA: state 第 {index} 行 prev_hash 不匹配上一行")
            candidate = dict(row)
            candidate.pop("row_hash", None)
            if row["row_hash"] != sha256_text(previous + canonical(candidate)):
                problems.append(f"E-SCHEMA: state 第 {index} 行 row_hash 校验失败")
            previous = row["row_hash"]
            if row["gate"] not in GATE_ORDER:
                problems.append(f"E-UNKNOWN: state 出现未知 gate {row['gate']}")
            if row["status"] != "passed":
                problems.append(f"E-UNKNOWN: state 出现未知 status {row['status']}")
            try:
                parse_time(row["created_at"])
            except WorkflowError as exc:
                problems.append(f"E-TIME: state 第 {index} 行 {exc}")
            if not row["lead_verification"].strip():
                problems.append(
                    f"E-REVIEW-LEAD-VERIFY: {row['gate']} 缺少 Lead 核验引用")
        return problems

    def _append_state(self, row: Dict[str, str]) -> Dict[str, str]:
        rows = self.state_rows()
        previous = rows[-1]["row_hash"] if rows else GENESIS_HASH
        seq = (int(rows[-1]["seq"]) if rows else 0) + 1
        full = dict(row)
        full["seq"] = str(seq)
        full["prev_hash"] = previous
        full["row_hash"] = sha256_text(previous + canonical(full))
        _append_row(self._table("state"), STATE_HEADER, full)
        return full

    # -- tool 绑定 ----------------------------------------------------
    def tool_snapshot(self) -> Dict[str, str]:
        base = Path(__file__).resolve().parent
        snapshot: Dict[str, str] = {}
        for name in TOOL_FILES:
            path = base / name
            snapshot[name] = sha256_file(path) if path.is_file() else "MISSING"
        return snapshot

    # -- artifacts ----------------------------------------------------
    def artifact(
        self,
        kind: str,
        path: str,
        gate: str,
        author: str,
        role: str = "evidence_author",
        artifact_id: str = "",
        supersedes: str = "",
        note: str = "",
        source_sha256: str = "",
    ) -> Dict[str, str]:
        with self._lock():
            self._require_init()
            self._require_known_gate(gate)
            kind = _enum(kind, ALL_KINDS, "kind")
            role = _enum(role, ARTIFACT_ROLES, "role")
            author = _text(author, "author")
            rel = self._resolve_rel(path, kind)
            target = self.path(rel)
            if not target.is_file():
                raise WorkflowError(f"E-ARTIFACT-MISSING: 文件不存在 {rel}")
            digest = sha256_file(target)
            rows = self.read("artifacts")
            current = self.current_artifacts()
            ids = {row["artifact_id"] for row in rows}
            if artifact_id:
                artifact_id = _text(artifact_id, "artifact_id")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", artifact_id):
                    raise WorkflowError(
                        f"artifact_id 必须以字母开头、仅含字母数字_.-: {artifact_id}")
                if artifact_id in ids:
                    raise BlockedError(
                        f"E-DUPLICATE: artifact_id 已存在 {artifact_id}")
            else:
                index = len(rows) + 1
                while f"A{index}" in ids:
                    index += 1
                artifact_id = f"A{index}"
            same = [r for r in current if r["path"] == rel and r["kind"] == kind]
            if same and all(r["sha256"] == digest for r in same):
                raise BlockedError(
                    f"E-DUPLICATE: {rel}({kind}) 内容未变，已登记为 "
                    + ",".join(r["artifact_id"] for r in same))
            if supersedes:
                old_ids = [item.strip() for item in supersedes.split(",") if item.strip()]
                for old in old_ids:
                    row = next((r for r in current if r["artifact_id"] == old), None)
                    if row is None:
                        raise WorkflowError(
                            f"E-SUPERSEDE: 旧 artifact 不存在或已被替代 {old}")
                    if (row["path"], row["kind"]) != (rel, kind):
                        raise WorkflowError(
                            f"E-SUPERSEDE: {old} 与 {rel}({kind}) 不匹配")
                supersede_ids = old_ids
            else:
                if same:
                    if any(r["gate"] != gate for r in same):
                        raise BlockedError(
                            f"E-DUPLICATE: {rel}({kind}) 已登记在 "
                            f"{same[0]['gate']}，替代登记必须同 gate")
                supersede_ids = [r["artifact_id"] for r in same]
            row = {
                "artifact_id": artifact_id,
                "gate": gate,
                "kind": kind,
                "path": rel,
                "sha256": digest,
                "bytes": str(target.stat().st_size),
                "author": author,
                "role": role,
                "created_at": format_time(now_local()),
                "source_sha256": source_sha256.strip(),
                "supersedes": ",".join(supersede_ids),
                "note": note.strip(),
            }
            _append_row(self._table("artifacts"), ARTIFACT_HEADER, row)
            return row

    # -- freeze -------------------------------------------------------
    def freeze(
        self,
        entries: Sequence[Tuple[str, str, str]],
        gate: str,
        author: str,
    ) -> Dict[str, Any]:
        with self._lock():
            self._require_init()
            if gate not in FREEZE_KEYS:
                raise WorkflowError(
                    f"E-FREEZE-GATE: freeze 仅适用于 {sorted(FREEZE_KEYS)}，"
                    f"收到 {gate}")
            author = _text(author, "author")
            allowed = set(FREEZE_KEYS[gate])
            seen: Dict[str, str] = {}
            prepared: List[Dict[str, str]] = []
            for mode, key, value in entries:
                key = _text(key, "freeze.key")
                if key not in allowed:
                    raise WorkflowError(
                        f"E-FREEZE-KEY: {key!r} 不属于 {gate}；"
                        f"允许 {sorted(allowed)}")
                if key in seen:
                    raise BlockedError(f"E-DUPLICATE: freeze 键重复 {key}")
                seen[key] = value
                if mode == "set":
                    if key in FILE_ONLY_FREEZE:
                        raise WorkflowError(
                            f"E-FREEZE-FILE-ONLY: {key} 必须用 --file 指向真实文件")
                    literal = _text(value, key)
                    if key in SHA_OR_FILE_FREEZE and not re.fullmatch(
                            r"[0-9a-f]{64}", literal):
                        raise WorkflowError(
                            f"E-FREEZE-SHA: {key} 必须是 64 位十六进制 sha256 "
                            "或改用 --file 引用差异文件")
                    prepared.append({"key": key, "value": literal,
                                     "sha256": "", "source": "set"})
                elif mode == "file":
                    rel = self._resolve_rel(value, "freeze")
                    target = self.path(rel)
                    if not target.is_file():
                        raise WorkflowError(f"E-FREEZE-FILE: 文件不存在 {rel}")
                    prepared.append({"key": key, "value": rel,
                                     "sha256": sha256_file(target),
                                     "source": "file"})
                else:
                    raise WorkflowError(f"E-FREEZE-MODE: 未知模式 {mode!r}")
            rows = self.read("freeze")
            freeze_id = f"F{len(rows) + 1}"
            for item in prepared:
                _append_row(self._table("freeze"), FREEZE_HEADER, {
                    "freeze_id": freeze_id,
                    "key": item["key"],
                    "value": item["value"],
                    "sha256": item["sha256"],
                    "source": item["source"],
                    "tool_version": TOOL_VERSION,
                    "contract_version": CONTRACT_VERSION,
                    "created_at": format_time(now_local()),
                    "author": author,
                    "note": "",
                })
            return {"freeze_id": freeze_id, "gate": gate, "keys": sorted(seen)}

    def latest_freeze(self, gate: str) -> Dict[str, Dict[str, str]]:
        latest: Dict[str, Dict[str, str]] = {}
        for row in self.freeze_rows(gate):
            latest[row["key"]] = row
        return latest

    # -- 上游与摘要 ---------------------------------------------------
    def upstream_binding(self, gate: str) -> str:
        upstream = GATE_UPSTREAM[gate]
        if upstream is None:
            return GENESIS_HASH
        row = self.gate_row(upstream)
        if row is None:
            return "MISSING"
        return sha256_text(canonical({
            "upstream": upstream,
            "row_hash": row["row_hash"],
            "review_id": row["review_id"],
            "attempt": row["attempt"],
        }))

    def evidence_digest(self, gate: str) -> str:
        self._require_known_gate(gate)
        artifacts = sorted(
            ((r["artifact_id"], r["gate"], r["kind"], r["path"], r["sha256"],
              r["bytes"], r["author"], r["role"], r["created_at"],
              r["source_sha256"], r["supersedes"], r["note"])
             for r in self.artifacts(gate)),
            key=lambda item: (item[0], item[2]))
        freeze = sorted(
            ((k, r["value"], r["sha256"])
             for k, r in self.latest_freeze(gate).items()),
            key=lambda item: item[0])
        std_tables = []
        for filename, _kind in STANDARD_TABLES.get(gate, ()):
            path = self._std_table(filename)
            std_tables.append(
                (filename, sha256_file(path) if path.is_file() else "MISSING"))
        return sha256_text(canonical({
            "gate": gate,
            "artifacts": artifacts,
            "freeze": freeze,
            "std": std_tables,
            "upstream": self.upstream_binding(gate),
            "tools": self.tool_snapshot(),
        }))

    def upstream_digest(self, gate: str) -> str:
        return self.upstream_binding(gate)

    def gate_authors(self, gate: str) -> set:
        """产物作者（artifact/freeze），保守纳入所有已通过上游门禁的作者。

        下游审阅同一交付链（如 G3 复用 G2 的代码），仅看当前 gate 作者会漏掉
        上游作者；推进人不是产物作者，另行校验其不得为审批人。
        """
        authors = {r["author"] for r in self.artifacts(gate)}
        authors |= {r["author"] for r in self.freeze_rows(gate)}
        for ancestor in GATES[:GATE_ORDER[gate]]:
            authors |= {r["author"] for r in self.artifacts(ancestor)}
            authors |= {r["author"] for r in self.freeze_rows(ancestor)}
        return {a for a in authors if a}

    # -- 结构检查 -----------------------------------------------------
    def _layout_problems(self) -> List[str]:
        problems: List[str] = []
        allow_extra = set()
        allow_file = self.dir / ".layout-allow"
        if allow_file.is_file():
            for line in allow_file.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if text and not text.startswith("#"):
                    allow_extra.add(text.split()[0])
        for entry in sorted(self.dir.iterdir(), key=lambda p: p.name):
            name = entry.name
            if name in allow_extra:
                continue
            if entry.is_dir() and name in LAYOUT_DIRS:
                continue
            if entry.is_file() and name in LAYOUT_FILES:
                continue
            problems.append(
                f"E-LAYOUT: 任务顶层出现白名单外条目 {name}")
        return problems

    def _artifact_problems(self, gate: str) -> List[str]:
        problems: List[str] = []
        rows = self.artifacts(gate)
        seen_ids = set()
        for row in rows:
            if row["artifact_id"] in seen_ids:
                problems.append(
                    f"E-DUPLICATE: {gate} artifact_id 重复 {row['artifact_id']}")
            seen_ids.add(row["artifact_id"])
            if row["kind"] not in ALL_KINDS:
                problems.append(f"E-UNKNOWN: {gate} 未知 kind {row['kind']}")
        kinds = {row["kind"] for row in rows}
        for kind in GATE_EVIDENCE_KINDS[gate]:
            if kind not in kinds:
                problems.append(
                    f"E-ARTIFACT-MISSING-KIND: {gate} 缺少 kind={kind} 的证据")
        return problems

    def _global_artifact_problems(self) -> List[str]:
        problems: List[str] = []
        for row in self.current_artifacts():
            target = self.path(row["path"])
            if not target.is_file():
                problems.append(
                    f"E-ARTIFACT-MISSING: {row['gate']} {row['path']} 不存在")
                continue
            if sha256_file(target) != row["sha256"]:
                problems.append(
                    f"E-STALE-ARTIFACT: {row['gate']} {row['path']} 内容已变化"
                    "（用新 artifact 替代并重新审批）")
        return problems

    def _standard_table_problems(self, gate: str) -> List[str]:
        problems: List[str] = []
        rows = self.artifacts(gate)
        for filename, kind in STANDARD_TABLES.get(gate, ()):
            path = self._std_table(filename)
            if not path.is_file():
                problems.append(f"E-EVIDENCE-TABLE-MISSING: {gate} 缺少 {filename}")
                continue
            digest = sha256_file(path)
            matched = [r for r in rows if r["kind"] == kind]
            if not matched:
                problems.append(
                    f"E-EVIDENCE-TABLE-UNREGISTERED: {gate} {filename} 未登记为 "
                    f"kind={kind} 的 artifact")
            elif not any(r["sha256"] == digest for r in matched):
                problems.append(
                    f"E-EVIDENCE-TABLE-HASH: {gate} {filename} 与已登记 artifact "
                    "sha256 不一致")
        return problems

    def _freeze_problems(self, gate: str) -> List[str]:
        problems: List[str] = []
        if gate not in FREEZE_KEYS:
            return problems
        latest = self.latest_freeze(gate)
        artifact_by_kind: Dict[str, Dict[str, str]] = {}
        for row in self.artifacts(gate):
            artifact_by_kind.setdefault(row["kind"], row)
        for key in FREEZE_KEYS[gate]:
            row = latest.get(key)
            if row is None:
                problems.append(f"E-FREEZE-MISSING-KEY: {gate} 缺少冻结键 {key}")
                continue
            if not row["value"].strip():
                problems.append(f"E-EMPTY: {gate} 冻结键 {key} 为空")
            if key in FILE_ONLY_FREEZE and row["source"] != "file":
                problems.append(f"E-FREEZE-FILE-ONLY: {gate} {key} 必须来自 --file")
            if key in SHA_OR_FILE_FREEZE and row["source"] == "set" and not re.fullmatch(
                    r"[0-9a-f]{64}", row["value"].strip()):
                problems.append(
                    f"E-FREEZE-SHA: {gate} {key} 不是有效 sha256 也不是文件引用")
            resolved_sha = ""
            if row["sha256"]:
                target = self.path(row["value"])
                if not target.is_file():
                    problems.append(
                        f"E-FREEZE-FILE: {gate} {key} 指向不存在的 {row['value']}")
                else:
                    resolved_sha = sha256_file(target)
                    if resolved_sha != row["sha256"]:
                        problems.append(
                            f"E-STALE-FREEZE: {gate} {key} 文件内容已变化")
            kind = KEY_ARTIFACT_KIND.get(key)
            if kind:
                artifact = artifact_by_kind.get(kind)
                if artifact is None:
                    problems.append(
                        f"E-FREEZE-EVIDENCE: {gate} {key} 需要 kind={kind} 的证据")
                elif artifact["sha256"] != (resolved_sha or row["value"]):
                    problems.append(
                        f"E-FREEZE-MISMATCH: {gate} {key} 与已登记 {kind} 证据 "
                        "sha256 不一致")
            if (row["tool_version"] != TOOL_VERSION or
                    row["contract_version"] != CONTRACT_VERSION):
                problems.append(
                    f"E-META-VERSION: {gate} {key} 由旧版本冻结")
        return problems

    # -- 证据引用与 checklist -----------------------------------------
    def _valid_evidence_refs(self) -> set:
        refs = {row["artifact_id"] for row in self.current_artifacts()}
        for filename, _kind in [(name, kind)
                                for gate in STANDARD_TABLES.values()
                                for name, kind in gate]:
            path = self._std_table(filename)
            if not path.is_file():
                continue
            try:
                with path.open(newline="", encoding="utf-8") as stream:
                    reader = csv.DictReader(stream)
                    for row in reader:
                        for column in VALID_REF_COLUMNS:
                            value = (row.get(column) or "").strip()
                            if value:
                                refs.add(value)
            except (OSError, csv.Error):
                continue
        requirements = self._std_table("requirements.csv")
        if requirements.is_file():
            try:
                with requirements.open(newline="", encoding="utf-8") as stream:
                    for row in csv.DictReader(stream):
                        for value in row.values():
                            if value and value.strip():
                                refs.add(value.strip())
            except (OSError, csv.Error):
                pass
        return refs

    def required_items(self, gate: str) -> Tuple[str, ...]:
        """默认 checklist 为单源；requirements.csv 只能追加，不能覆盖或删减。"""
        base: List[str] = list(REQUIRED_ITEMS[gate])
        requirements = self._std_table("requirements.csv")
        if requirements.is_file():
            try:
                with requirements.open(newline="", encoding="utf-8") as stream:
                    for row in csv.DictReader(stream):
                        if (row.get("gate") or "").strip() != gate:
                            continue
                        item = (row.get("item_id") or "").strip()
                        if item and item not in base:
                            base.append(item)
            except (OSError, csv.Error) as exc:
                raise WorkflowError(f"requirements.csv 读取失败: {exc}") from exc
        return tuple(base)

    # -- reviews ------------------------------------------------------
    @staticmethod
    def _review_seq(row: Dict[str, str]) -> int:
        digits = re.sub(r"\D", "", row["review_id"])
        return int(digits) if digits else 0

    def _review_problems(self, gate: str, digest: str) -> List[str]:
        problems: List[str] = []
        rows = sorted(self.reviews(gate), key=self._review_seq)
        if not rows:
            return [f"E-REVIEW-MISSING: {gate} 无审批记录"]
        authors = self.gate_authors(gate)
        required = REQUIRED_REVIEWERS[gate]
        required_items = set(self.required_items(gate))
        valid_refs = self._valid_evidence_refs()
        latest_by_reviewer: Dict[str, Dict[str, str]] = {}
        for row in rows:
            latest_by_reviewer[row["reviewer"]] = row
        # 任何 reviewer 的最新结论为 negative 即阻断，直到该 reviewer 重新审
        for reviewer, row in sorted(latest_by_reviewer.items()):
            if row["verdict"] != "approve":
                problems.append(
                    f"E-REVIEW-LATEST: {gate} {reviewer} 最新结论 "
                    f"{row['verdict']}（{row['review_id']}），需其本人重新审")
        approved_current = [r for r in latest_by_reviewer.values()
                            if r["verdict"] == "approve"
                            and r["target_digest"] == digest]
        independent = [r for r in approved_current if r["reviewer"] not in authors]
        for row in approved_current:
            if row["reviewer"] in authors:
                problems.append(
                    f"E-REVIEW-SELF: {gate} {row['reviewer']} 是本门禁产物作者")
        if len(independent) < required:
            problems.append(
                f"E-REVIEW-COUNT: {gate} 需 {required} 个非作者独立 approve"
                f"（当前快照），实际 {len(independent)}")
        roles = {r["reviewer_role"] for r in independent}
        if len(roles) < required:
            problems.append(
                f"E-REVIEW-ROLE: {gate} 需 {required} 个不同审查角色 "
                f"{list(REVIEWER_ROLES)}，实际 {sorted(roles)}")
        for row in independent:
            if not row["provider"].strip() or not row["model"].strip():
                problems.append(
                    f"E-EMPTY: {gate} {row['reviewer']} 缺 provider/model")
            if not row["session_id"].strip():
                problems.append(
                    f"E-REVIEW-SESSION: {gate} {row['reviewer']} 缺 session_id")
            try:
                if int(row["event_seq_from"]) > int(row["event_seq_to"]):
                    problems.append(
                        f"E-REVIEW-SESSION: {gate} {row['reviewer']} "
                        "event_seq 区间倒置")
            except ValueError:
                problems.append(
                    f"E-REVIEW-SESSION: {gate} {row['reviewer']} event_seq 非整数")
            if not row["author_scope"].strip():
                problems.append(f"E-REVIEW-SCOPE: {gate} 缺审查范围")
            items = self.review_items(row["review_id"])
            if not items:
                problems.append(
                    f"E-REVIEW-ITEMS: {gate} {row['review_id']} 无逐项判定")
                continue
            ids = [item["item_id"] for item in items]
            if len(ids) != len(set(ids)):
                problems.append(
                    f"E-DUPLICATE: {gate} {row['review_id']} item 重复")
            missing = required_items - set(ids)
            if missing:
                problems.append(
                    f"E-REVIEW-CHECKLIST: {gate} {row['reviewer']} 未覆盖 "
                    f"checklist {sorted(missing)}")
            for item in items:
                if item["judgement"] not in JUDGEMENTS:
                    problems.append(
                        f"E-UNKNOWN: {gate} item judgement={item['judgement']}")
                if item["judgement"] == "fail":
                    problems.append(
                        f"E-REVIEW-FAIL: {gate} item {item['item_id']} 判定 fail")
                if not item["reason"].strip():
                    problems.append(
                        f"E-EMPTY: {gate} item {item['item_id']} 缺理由")
                if item["judgement"] == "na" and not item["reason"].strip():
                    problems.append(
                        f"E-REVIEW-NA-REASON: {gate} item {item['item_id']} na 无理由")
                ref = item["evidence_id"].strip()
                if not ref:
                    problems.append(
                        f"E-REVIEW-EVIDENCE: {gate} item {item['item_id']} "
                        "缺 evidence_id")
                elif ref not in valid_refs:
                    problems.append(
                        f"E-REVIEW-EVIDENCE: {gate} item {item['item_id']} "
                        f"evidence_id={ref} 未指向已登记 artifact/数据行")
        return problems

    def _effective_approvals(self, gate: str, digest: str) -> List[Dict[str, str]]:
        rows = sorted(self.reviews(gate), key=self._review_seq)
        latest: Dict[str, Dict[str, str]] = {}
        for row in rows:
            latest[row["reviewer"]] = row
        return sorted((r for r in latest.values()
                       if r["verdict"] == "approve"
                       and r["target_digest"] == digest),
                      key=self._review_seq)

    def approval_binding(self, gate: str, digest: str,
                         approvals: Optional[List[Dict[str, str]]] = None) -> str:
        """绑定完整批准集合（reviewer/role/target_digest）与逐项判定内容。

        含 review_id 与审批身份/范围（session/event/provider/model/scope）：
        重审即新批准集合（需重推该 gate），任一审阅丢失、身份变化或逐项判定/
        理由/证据变化都会改变绑定；绑定只从 review 行计算，不回写 review 字段。
        """
        if approvals is None:
            approvals = self._effective_approvals(gate, digest)
        payload = []
        for review in approvals:
            items = sorted(
                ((item["item_id"], item["judgement"], item["reason"],
                  item["evidence_id"])
                 for item in self.review_items(review["review_id"])),
                key=lambda item: item[0])
            payload.append((review["review_id"], review["reviewer"],
                            review["reviewer_role"], review["target_digest"],
                            items, review["provider"], review["model"],
                            review["author_scope"], review["session_id"],
                            review["event_seq_from"], review["event_seq_to"]))
        return sha256_text(canonical({"gate": gate, "digest": digest,
                                      "approvals": sorted(payload)}))

    def _effective_review(self, gate: str, digest: str) -> Optional[Dict[str, str]]:
        rows = sorted(self.reviews(gate), key=self._review_seq)
        latest: Dict[str, Dict[str, str]] = {}
        for row in rows:
            latest[row["reviewer"]] = row
        approved = [r for r in latest.values()
                    if r["verdict"] == "approve" and r["target_digest"] == digest]
        if not approved:
            return None
        return sorted(approved, key=self._review_seq)[-1]

    def _evidence_problems(self, gate: str) -> List[str]:
        if gate not in GATES_NEEDING_EVIDENCE:
            return []
        module = self._module("evidence")
        if module is None:
            return [
                f"E-EVIDENCE-MODULE-MISSING: {self._evidence_path} 不存在；"
                f"{gate} 需要内容级校验"]
        fn = getattr(module, "check_task", None)
        if not callable(fn):
            return ["E-EVIDENCE-MODULE: evidence.check_task 不可调用"]
        try:
            result = fn(self, gate=gate)
        except TypeError:
            try:
                result = fn(self, gate)
            except Exception as exc:  # noqa: BLE001
                return [f"E-EVIDENCE-ERROR: {type(exc).__name__}: {exc}"]
        except Exception as exc:  # noqa: BLE001
            return [f"E-EVIDENCE-ERROR: {type(exc).__name__}: {exc}"]
        if result is None:
            return []
        if isinstance(result, str):
            return [f"E-EVIDENCE: {gate} {result}"]
        return [f"E-EVIDENCE: {gate} {item}" for item in result]

    def _module(self, name: str) -> Any:
        if name not in self._cache:
            path = self._evidence_path if name == "evidence" else self._reporting_path
            try:
                self._cache[name] = _load_module(path)
            except Exception as exc:  # noqa: BLE001
                raise WorkflowError(
                    f"E-MODULE-LOAD: 无法加载 {path}: {type(exc).__name__}: {exc}"
                ) from exc
        return self._cache[name]

    # -- 门禁判定 -----------------------------------------------------
    def _gate_is_stale(self, gate: str) -> bool:
        row = self.gate_row(gate)
        if row is None:
            return True
        digest = self.evidence_digest(gate)
        if row["evidence_digest"] != digest:
            return True
        if row["upstream_digest"] != self.upstream_binding(gate):
            return True
        if not any(r["review_id"] == row["review_id"] for r in self.reviews(gate)):
            return True
        if row["review_binding"] and row["review_binding"] != self.approval_binding(
                gate, digest):
            return True
        return False

    def _ancestor_problems(self, gate: str,
                           include_reviews: bool = True) -> List[str]:
        problems: List[str] = []
        index = GATE_ORDER[gate]
        for ancestor in GATES[:index]:
            row = self.gate_row(ancestor)
            if row is None:
                problems.append(
                    f"E-ORDER: 串行依赖缺 {ancestor}（{gate} 之前必须完成）")
                continue
            if row["status"] != "passed":
                problems.append(
                    f"E-ORDER: {ancestor} 状态 {row['status']} 非 passed")
            if row["evidence_digest"] != self.evidence_digest(ancestor):
                problems.append(
                    f"E-STALE-EVIDENCE: {ancestor} 证据快照变化，{gate} 失效")
            if row["upstream_digest"] != self.upstream_binding(ancestor):
                problems.append(
                    f"E-STALE-UPSTREAM: {ancestor} 上游绑定变化，{gate} 失效")
            if include_reviews:
                ancestor_digest = self.evidence_digest(ancestor)
                for item in self._review_problems(ancestor, ancestor_digest):
                    problems.append(f"E-STALE-REVIEW: {ancestor} {item}")
                if row["review_binding"]:
                    current = self.approval_binding(ancestor, ancestor_digest)
                    if row["review_binding"] != current:
                        problems.append(
                            f"E-STALE-REVIEW: {ancestor} 批准集合或逐项判定变化，"
                            f"{gate} 失效")
                else:
                    problems.append(
                        f"E-STALE-REVIEW: {ancestor} 状态行缺少批准集合绑定")
        return problems

    def _gate_problems(
        self, gate: str, for_advance: bool, evidence_only: bool = False
    ) -> Tuple[List[str], Optional[Dict[str, str]]]:
        self._require_known_gate(gate)
        problems: List[str] = []
        problems.extend(self._ancestor_problems(
            gate, include_reviews=not evidence_only))
        problems.extend(self._artifact_problems(gate))
        problems.extend(self._standard_table_problems(gate))
        problems.extend(self._freeze_problems(gate))
        problems.extend(self._evidence_problems(gate))
        digest = self.evidence_digest(gate)
        review = None
        row = self.gate_row(gate)
        if not evidence_only:
            problems.extend(self._review_problems(gate, digest))
            review = self._effective_review(gate, digest)
        if row is not None:
            if (not evidence_only and not for_advance
                    and row["review_binding"]):
                current = self.approval_binding(gate, digest)
                if row["review_binding"] != current:
                    problems.append(
                        f"E-STALE-REVIEW: {gate} 批准集合或逐项判定与推进时不同")
            if for_advance:
                if not evidence_only and not self._gate_is_stale(gate):
                    problems.append(f"E-ADVANCE-EXISTS: {gate} 已推进且未 stale")
            elif row["evidence_digest"] != digest:
                problems.append(f"E-STALE-EVIDENCE: {gate} 快照与推进时不同")
        return problems, review

    # -- check --------------------------------------------------------
    def check(self, gate: Optional[str] = None,
              evidence_only: bool = False) -> List[str]:
        problems: List[str] = []
        meta_problems = self._meta_problems()
        problems.extend(meta_problems)
        if meta_problems:
            return problems
        problems.extend(self._layout_problems())
        problems.extend(self._state_chain_problems())
        problems.extend(self._global_artifact_problems())
        if gate is not None:
            self._require_known_gate(gate)
            targets = [gate]
        else:
            targets = [g for g in GATES if self.gate_row(g) is not None]
            if not targets:
                problems.append("E-STATE-EMPTY: 尚无任何门禁记录")
        for target in targets:
            sub, _ = self._gate_problems(
                target, for_advance=self.gate_row(target) is None,
                evidence_only=evidence_only)
            problems.extend(sub)
        unique: List[str] = []
        for item in problems:
            if item not in unique:
                unique.append(item)
        return unique

    # -- record-review ------------------------------------------------
    def record_review(
        self,
        gate: str,
        reviewer: str,
        provider: str,
        model: str,
        reviewer_role: str,
        scope: str,
        verdict: str,
        session_id: str,
        event_from: int,
        event_to: int,
        items: Sequence[Tuple[str, str, str, str]],
        note: str = "",
    ) -> Dict[str, str]:
        with self._lock():
            self._require_init()
            self._require_known_gate(gate)
            reviewer = _text(reviewer, "reviewer")
            provider = _text(provider, "provider")
            model = _text(model, "model")
            reviewer_role = _enum(reviewer_role, REVIEWER_ROLES, "reviewer_role")
            scope = _text(scope, "scope")
            verdict = _enum(verdict, VERDICTS, "verdict")
            session_id = _text(session_id, "session_id")
            if int(event_from) > int(event_to):
                raise WorkflowError("event_from 必须 <= event_to")
            if not items:
                raise WorkflowError("E-REVIEW-ITEMS: 至少需要一条逐项判定")
            target_digest = self.evidence_digest(gate)
            if reviewer in self.gate_authors(gate):
                raise BlockedError(
                    f"E-REVIEW-SELF: {reviewer} 是本门禁产物作者，不能自审")
            prepared: List[Dict[str, str]] = []
            seen = set()
            for item in items:
                item_id, judgement, reason, evidence_id = (list(item) + ["", ""])[:4]
                item_id = _text(item_id, "item_id")
                if item_id in seen:
                    raise BlockedError(f"E-DUPLICATE: review item 重复 {item_id}")
                seen.add(item_id)
                judgement = _enum(judgement, JUDGEMENTS, "judgement")
                reason = (reason or "").strip()
                evidence_id = (evidence_id or "").strip()
                if not reason:
                    raise WorkflowError(f"E-EMPTY: item {item_id} 缺理由")
                if not evidence_id:
                    raise WorkflowError(
                        f"E-REVIEW-EVIDENCE: item {item_id} 缺 evidence_id")
                if judgement == "fail" and verdict == "approve":
                    raise BlockedError(
                        f"E-REVIEW-FAIL: item {item_id} 为 fail，不能 approve")
                prepared.append({"item_id": item_id, "judgement": judgement,
                                 "reason": reason, "evidence_id": evidence_id})
            rows = self.read("reviews")
            review_id = f"R{len(rows) + 1}"
            _append_row(self._table("reviews"), REVIEW_HEADER, {
                "review_id": review_id,
                "gate": gate,
                "reviewer": reviewer,
                "provider": provider,
                "model": model,
                "reviewer_role": reviewer_role,
                "author_scope": scope,
                "target_digest": target_digest,
                "verdict": verdict,
                "session_id": session_id,
                "event_seq_from": str(int(event_from)),
                "event_seq_to": str(int(event_to)),
                "lead_verified": "no",
                "lead_verifier": "",
                "created_at": format_time(now_local()),
                "revision": str(self.revision()),
                "note": note.strip(),
            })
            for item in prepared:
                _append_row(self._table("review_items"), REVIEW_ITEM_HEADER, {
                    "review_id": review_id, **item})
            return {"review_id": review_id, "gate": gate,
                    "target_digest": target_digest}

    # -- advance ------------------------------------------------------
    def advance(
        self,
        gate: str,
        author: str,
        expect_revision: int,
        lead_verification: str,
        note: str = "",
    ) -> Dict[str, str]:
        with self._lock():
            self._require_known_gate(gate)
            author = _text(author, "author")
            lead_verification = _text(lead_verification, "lead_verification")
            meta_problems = self._meta_problems()
            if meta_problems:
                raise BlockedError("; ".join(meta_problems))
            if int(expect_revision) != self.revision():
                raise WorkflowError(
                    f"E-REVISION: 期望 revision={expect_revision}，"
                    f"实际 {self.revision()}（锁内比对）")
            problems: List[str] = []
            problems.extend(self._layout_problems())
            problems.extend(self._state_chain_problems())
            problems.extend(self._global_artifact_problems())
            gate_problems, review = self._gate_problems(
                gate, for_advance=True, evidence_only=False)
            problems.extend(gate_problems)
            if problems:
                raise BlockedError("; ".join(problems))
            if review is None:
                raise BlockedError(f"E-REVIEW-MISSING: {gate} 无可用审批")
            digest = self.evidence_digest(gate)
            approvals = self._effective_approvals(gate, digest)
            reviewers = {r["reviewer"] for r in approvals}
            if author in reviewers:
                raise BlockedError(
                    f"E-REVIEW-SELF: 推进人 {author} 是有效审批人，不能自我推进")
            row = {
                "gate": gate,
                "status": "passed",
                "attempt": str(len([r for r in self.state_rows()
                                    if r["gate"] == gate]) + 1),
                "created_at": format_time(now_local()),
                "author": author,
                "upstream_digest": self.upstream_binding(gate),
                "evidence_digest": self.evidence_digest(gate),
                "review_id": review["review_id"],
                "review_binding": self.approval_binding(gate, digest, approvals),
                "freeze_id": max(
                    (r["freeze_id"] for r in self.freeze_rows(gate)), default=""),
                "lead_verification": lead_verification,
                "note": note.strip(),
            }
            return self._append_state(row)

    # -- status -------------------------------------------------------
    def _status_stale(self, gate: str, row: Dict[str, str]) -> List[str]:
        reasons: List[str] = []
        if row["evidence_digest"] != self.evidence_digest(gate):
            reasons.append("evidence-changed")
        if row["upstream_digest"] != self.upstream_binding(gate):
            reasons.append("upstream-changed")
        if self._ancestor_problems(gate):
            reasons.append("ancestor-invalid")
        latest: Dict[str, Dict[str, str]] = {}
        for item in sorted(self.reviews(gate), key=self._review_seq):
            latest[item["reviewer"]] = item
        if any(item["verdict"] != "approve" for item in latest.values()):
            reasons.append("review-negative")
        for artifact in self.artifacts(gate):
            path = self.path(artifact["path"])
            if not path.is_file() or sha256_file(path) != artifact["sha256"]:
                reasons.append("artifact-hash")
                break
        if not any(r["review_id"] == row["review_id"] for r in self.reviews(gate)):
            reasons.append("review-missing")
        if row["review_binding"] and row["review_binding"] != self.approval_binding(
                gate, row["evidence_digest"]):
            reasons.append("review-binding")
        return reasons

    def status(self) -> List[Dict[str, str]]:
        output: List[Dict[str, str]] = []
        for gate in GATES:
            row = self.gate_row(gate)
            if row is None:
                upstream = GATE_UPSTREAM[gate]
                upstream_ok = upstream is None
                if upstream is not None:
                    up_row = self.gate_row(upstream)
                    upstream_ok = (up_row is not None
                                   and up_row["status"] == "passed"
                                   and not self._status_stale(upstream, up_row))
                output.append({
                    "gate": gate, "status": "(none)", "attempt": "0",
                    "created_at": "", "author": "", "review_id": "",
                    "lead_verified": "no", "evidence_digest8": "",
                    "upstream_digest8": "", "stale": "",
                    "next_action": "check" if upstream_ok else "wait-upstream",
                })
                continue
            stale = self._status_stale(gate, row)
            next_action = "re-advance" if stale else ""
            output.append({
                "gate": gate,
                "status": row["status"],
                "attempt": row["attempt"],
                "created_at": row["created_at"],
                "author": row["author"],
                "review_id": row["review_id"],
                "lead_verified": "yes" if row["lead_verification"] else "no",
                "evidence_digest8": row["evidence_digest"][:8],
                "upstream_digest8": row["upstream_digest"][:8],
                "stale": ";".join(stale),
                "next_action": next_action,
            })
        return output

    # -- report -------------------------------------------------------
    def report(self, output_dir: Path) -> List[str]:
        if self.gate_status("G4") != "passed":
            raise BlockedError("E-ORDER: 报告生成前必须完成 G4（最终性能）")
        problems = self.check(gate="G4")
        if problems:
            raise BlockedError("; ".join(problems))
        raw = Path(output_dir)
        target = raw.resolve() if raw.is_absolute() else (self.dir / raw).resolve()
        if target != self.dir and self.dir not in target.parents:
            raise WorkflowError(f"输出目录必须位于任务目录内: {target}")
        report_root = (self.dir / "report").resolve()
        if target != report_root and report_root not in target.parents:
            raise WorkflowError(
                f"报告输出必须位于任务 report/ 下（与 reporting 模块一致）: {target}")
        if target.exists():
            raise WorkflowError(f"输出目录已存在，拒绝覆盖/复用: {target}")
        module = self._module("reporting")
        if module is None:
            raise BlockedError(
                f"E-REPORT-MODULE-MISSING: {self._reporting_path} 不存在")
        fn = getattr(module, "generate", None)
        if not callable(fn):
            raise BlockedError("E-REPORT-MODULE: reporting.generate 不可调用")
        try:
            result = fn(self, target)
        except WorkflowError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WorkflowError(
                f"E-REPORT-ERROR: {type(exc).__name__}: {exc}") from exc
        return [str(item) for item in (result or [])]

    # -- import-data --------------------------------------------------
    def import_data(self, kind: str, source: str, output: str) -> List[str]:
        """委托 evidence.import_data；kind 为 evidence 适配器名，返回生成路径。

        不自动登记为正式 coverage/measurements/claims：是否补齐由用户显式
        artifact 登记决定，避免“导入即视为已通过”。
        """
        with self._lock():
            self._require_init()
            kind = _text(kind, "kind")
            module = self._module("evidence")
            if module is None:
                raise BlockedError(
                    f"E-EVIDENCE-MODULE-MISSING: {self._evidence_path} 不存在")
            fn = getattr(module, "import_data", None)
            if not callable(fn):
                raise BlockedError(
                    "E-EVIDENCE-MODULE: evidence.import_data 不可调用")
            try:
                result = fn(self, kind, source, output)
            except WorkflowError:
                raise  # evidence 的输入/适配器错误保持输入错误语义（退出 2）
            except Exception as exc:  # noqa: BLE001
                raise WorkflowError(
                    f"E-EVIDENCE-ERROR: {type(exc).__name__}: {exc}") from exc
            if result is None:
                return []
            if isinstance(result, (str, Path)):
                return [str(result)]
            return [str(item) for item in result]

    @staticmethod
    def import_kinds() -> Tuple[str, ...]:
        """evidence 适配器名（用于 CLI 帮助/校验；缺失时返回空）。"""
        module = _load_module(Path(__file__).resolve().with_name("evidence.py"))
        if module is None:
            return ()
        for name in ("IMPORT_KINDS", "KIND_ADAPTERS", "ADAPTER_NAMES"):
            value = getattr(module, name, None)
            if value:
                return tuple(str(item) for item in value)
        return ()

    # -- 内部断言 -----------------------------------------------------
    def _require_known_gate(self, gate: str) -> None:
        if gate not in GATE_ORDER:
            raise WorkflowError(f"E-UNKNOWN-GATE: {gate!r}；允许 {list(GATES)}")

    def require_gate(self, gate: str) -> Dict[str, str]:
        self._require_known_gate(gate)
        row = self.gate_row(gate)
        if row is None or row["status"] != "passed" or self._gate_is_stale(gate):
            raise BlockedError(f"E-ORDER: {gate} 尚未通过或已 stale")
        return row
