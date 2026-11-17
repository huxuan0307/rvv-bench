"""CSV 证据校验与显式适配；不启动目标程序，不把导入当作通过。"""
from __future__ import annotations

import csv
import hashlib
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from statistics import median

SCHEMA = "1"
COVERAGE = "schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,status,expected_count,actual_count,metric,limit,observed,reason,evidence_id".split(",")
RUNS = "schema_version,run_id,role,pair_id,status,returncode,completion,checks,failures,identity_id,tool_id,binary_id,library_id,link_id,source_sha256,evidence_id".split(",")
MEASUREMENTS = "schema_version,run_id,case_id,sample_id,phase,cycles,cycle_begin,cycle_end,counter_bits,calls,elements,timing_repeat,vector_calls,vector_elements,unit,checksum,status".split(",")
CLAIMS = "schema_version,claim_id,kind,metric,selector,operator,expected,unit,scope,text,evidence_id".split(",")
CASES = "schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,required,expected_count,phases,metric,limit,unit,vector_required,reason".split(",")
FORMATS = {"cases.csv": CASES, "coverage.csv": COVERAGE, "runs.csv": RUNS, "measurements.csv": MEASUREMENTS, "claims.csv": CLAIMS}
IMPORT_KINDS = ("libmvec-samples", "newmath-coverage")
ENV_KEYS = {"simulator_family", "simulator_sha256", "reference_sha256", "dram_sha256", "isa", "vlen", "toolchain_sha256", "harness_sha256", "input_sha256", "flags", "window"}
STATUSES = {"pass", "fail", "skip", "not_run", "not_applicable"}
TEMP_REF = re.compile(r"(?:^|[/\\\s(<`])(tmp|log|runs)(?:[/\\])", re.I)


def root(task):
    return Path(getattr(task, "dir", task)).resolve() / "data" / "workflow"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def read_csv(path, required=None):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if not fields or len(fields) != len(set(fields)):
            raise ValueError(f"{Path(path).name}: 缺少或重复表头")
        if required and set(fields) != set(required):
            raise ValueError(f"{Path(path).name}: schema 列不匹配，要求 {','.join(required)}")
        rows = list(reader)
        if not rows:
            raise ValueError(f"{Path(path).name}: 空数据")
        for number, row in enumerate(rows, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{Path(path).name}:{number}: 字段数量错误")
            if required and row.get("schema_version") != SCHEMA:
                raise ValueError(f"{Path(path).name}:{number}: 未知 schema_version")
        return rows


def write_csv(path, fields, rows):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError(f"拒绝覆盖 {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def integer(value, label, minimum=0):
    if not re.fullmatch(r"[0-9]+", value or ""):
        raise ValueError(f"{label}: 必须为非负十进制整数")
    number = int(value)
    if number < minimum:
        raise ValueError(f"{label}: 必须 >= {minimum}")
    return number


def number(value):
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("非有限数值")
    return Fraction(result)


def unique(rows, keys, label):
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in keys)
        if any(not value for value in key) or key in seen:
            raise ValueError(f"{label}: 空或重复键 {key}")
        seen.add(key)


def validate_coverage(rows):
    unique(rows, ("case_id",), "coverage")
    for r in rows:
        status = r["status"]
        if status not in STATUSES:
            raise ValueError(f"{r['case_id']}: 未知覆盖状态")
        if not r["evidence_id"] or not r["metric"]:
            raise ValueError(f"{r['case_id']}: 缺证据或误差定义")
        if status != "pass":
            if status != "not_applicable" or not r["reason"]:
                raise ValueError(f"{r['case_id']}: 必测覆盖未通过 ({status})")
            # N/A 的适用性还需要 gate 的独立审阅。
            continue
        expected = integer(r["expected_count"], "expected_count", 1)
        if integer(r["actual_count"], "actual_count", 1) != expected:
            raise ValueError(f"{r['case_id']}: 样本数不完整")
        if not r["limit"] or not r["observed"]:
            raise ValueError(f"{r['case_id']}: 缺少预算或观测；布尔语义检查用失败数预算0")
        limit, observed = number(r["limit"]), number(r["observed"])
        if min(limit, observed) < 0 or observed > limit:
            raise ValueError(f"{r['case_id']}: 负误差或超过登记预算")


def validate_runs(rows):
    unique(rows, ("run_id",), "runs")
    for r in rows:
        if r["role"] not in {"baseline", "candidate", "validation"}:
            raise ValueError(f"{r['run_id']}: 未知 role")
        if r["status"] != "pass" or r["returncode"] != "0" or r["completion"] != "complete":
            raise ValueError(f"{r['run_id']}: 运行不完整或未通过")
        integer(r["checks"], "checks", 1)
        if integer(r["failures"], "failures") != 0:
            raise ValueError(f"{r['run_id']}: 存在功能失败")
        if not all(r[k] for k in ("identity_id", "tool_id", "binary_id", "library_id", "link_id", "evidence_id")):
            raise ValueError(f"{r['run_id']}: 身份/工具/证据缺失")
        if not re.fullmatch(r"[a-f0-9]{64}", r["source_sha256"]):
            raise ValueError(f"{r['run_id']}: 原始来源 SHA 无效")
        if r["role"] != "validation" and not r["pair_id"]:
            raise ValueError(f"{r['run_id']}: 缺少 A/B 配对")


def validate_measurements(rows, runs):
    unique(rows, ("run_id", "case_id", "sample_id"), "measurements")
    by_run = {r["run_id"]: r for r in runs}
    groups = {}
    for r in rows:
        if r["run_id"] not in by_run:
            raise ValueError(f"{r['run_id']}: 缺少运行记录")
        if r["status"] != "pass" or r["phase"] not in {"first", "warmup", "steady"}:
            raise ValueError(f"{r['case_id']}: 无效样本状态/相位")
        bits = integer(r["counter_bits"], "counter_bits", 1)
        if bits not in {32, 64}:
            raise ValueError("counter_bits 仅支持32/64")
        begin = integer(r["cycle_begin"], "cycle_begin")
        end = integer(r["cycle_end"], "cycle_end")
        cycles = integer(r["cycles"], "cycles", 1)
        if max(begin, end, cycles) >= 2**bits or (end - begin) % (2**bits) != cycles:
            raise ValueError(f"{r['case_id']}: 周期恒等式错误")
        integer(r["calls"], "calls", 1)
        integer(r["elements"], "elements", 1)
        integer(r["timing_repeat"], "timing_repeat", 1)
        vector_calls = integer(r["vector_calls"], "vector_calls")
        vector_elements = integer(r["vector_elements"], "vector_elements")
        if vector_elements > int(r["elements"]):
            raise ValueError(f"{r['case_id']}: 向量元素数超过总工作量")
        if "vector" in r["unit"] and (vector_calls == 0 or vector_elements == 0):
            raise ValueError(f"{r['case_id']}: 向量性能样本未执行向量调用")
        if r["unit"] not in {"cycle/window", "cycle/call", "cycle/element", "cycle/vector-call", "cycle/vector-element"}:
            raise ValueError(f"{r['case_id']}: 未知单位")
        if not r["checksum"]:
            raise ValueError(f"{r['case_id']}: checksum 缺失")
        groups.setdefault((r["run_id"], r["case_id"]), []).append(r)
    for key, group in groups.items():
        if not any(r["phase"] == "steady" for r in group):
            raise ValueError(f"{key}: 没有 steady 样本")
        if len({r["checksum"] for r in group}) != 1:
            raise ValueError(f"{key}: 同一固定输入重复 checksum 漂移")
        for column in ("unit", "calls", "elements", "timing_repeat", "vector_calls", "vector_elements"):
            if len({r[column] for r in group}) != 1:
                raise ValueError(f"{key}: {column} 不一致")
    # 相同 pair 的身份标识表示合同冻结的共同环境，不含允许变化的候选库/ELF。
    pairs = {}
    for r in runs:
        if r["role"] != "validation":
            pairs.setdefault(r["pair_id"], []).append(r)
    for pair, members in pairs.items():
        if len(members) != 2 or {r["role"] for r in members} != {"baseline", "candidate"}:
            raise ValueError(f"{pair}: A/B 必须恰好各一个")
        if len({(r["identity_id"], r["tool_id"]) for r in members}) != 1:
            raise ValueError(f"{pair}: A/B 环境或工具身份不匹配")
        a, b = members
        ga = {case: group for (run, case), group in groups.items() if run == a["run_id"]}
        gb = {case: group for (run, case), group in groups.items() if run == b["run_id"]}
        if not ga or ga.keys() != gb.keys():
            raise ValueError(f"{pair}: A/B 场景不完整")
        for case in ga:
            columns = ("sample_id", "phase", "calls", "elements", "timing_repeat", "unit")
            signature = lambda group: sorted(tuple(r[k] for k in columns) for r in group)
            if signature(ga[case]) != signature(gb[case]):
                raise ValueError(f"{pair}/{case}: A/B 窗口或工作量不匹配")
    return groups


def normalized(row):
    """calls/elements 均为窗口实际总数，已含 timing_repeat，禁止重复相除。"""
    cycles = Fraction(int(row["cycles"]))
    if row["unit"] == "cycle/window":
        return cycles
    denominator = {"cycle/call": "calls", "cycle/element": "elements", "cycle/vector-call": "vector_calls", "cycle/vector-element": "vector_elements"}[row["unit"]]
    return cycles / int(row[denominator])


def comparisons(measurements, runs):
    groups = validate_measurements(measurements, runs)
    by_role = {(r["pair_id"], r["role"]): r["run_id"] for r in runs if r["role"] != "validation"}
    if not by_role:
        raise ValueError("正式性能必须至少包含一对 baseline/candidate，validation 不能代替")
    result = []
    for pair in sorted({p for p, role in by_role}):
        a, b = by_role[(pair, "baseline")], by_role[(pair, "candidate")]
        for (run, case), values in sorted(groups.items()):
            if run != a:
                continue
            old = median(normalized(r) for r in values if r["phase"] == "steady")
            new = median(normalized(r) for r in groups[(b, case)] if r["phase"] == "steady")
            result.append(dict(pair_id=pair, case_id=case, baseline=old, candidate=new,
                               change_pct=(new / old - 1) * 100, unit=values[0]["unit"]))
    return result


def evaluate_claims(claims, coverage, comparison):
    unique(claims, ("claim_id",), "claims")
    outputs = []
    for r in claims:
        if r["kind"] not in {"numeric", "qualitative", "hypothesis"} or not all(r[k] for k in ("scope", "text", "evidence_id")):
            raise ValueError(f"{r['claim_id']}: 缺少类型/范围/正文/证据")
        value = None
        if r["kind"] == "numeric":
            if r["metric"] == "coverage_pass_count":
                chosen = coverage if r["selector"] == "all" else [v for v in coverage if v["gate"] == r["selector"]]
                if not chosen or r["unit"] != "cases":
                    raise ValueError(f"{r['claim_id']}: 覆盖选择为空或单位错误")
                value = Fraction(sum(v["status"] == "pass" for v in chosen))
            elif r["metric"] in {"baseline", "candidate", "change_pct"}:
                chosen = [v for v in comparison if f"{v['pair_id']}:{v['case_id']}" == r["selector"]]
                if len(chosen) != 1:
                    raise ValueError(f"{r['claim_id']}: 数值来源缺失或歧义")
                expected_unit = "%" if r["metric"] == "change_pct" else chosen[0]["unit"]
                if r["unit"] != expected_unit:
                    raise ValueError(f"{r['claim_id']}: 单位不匹配")
                value = chosen[0][r["metric"]]
            else:
                raise ValueError(f"{r['claim_id']}: 未支持的数值 metric")
            expected = number(r["expected"])
            operations = {"eq": value == expected, "le": value <= expected, "ge": value >= expected,
                          "lt": value < expected, "gt": value > expected}
            if r["operator"] not in operations or not operations[r["operator"]]:
                raise ValueError(f"{r['claim_id']}: 数值结论与权威数据矛盾")
        elif any(r[k] for k in ("operator", "expected")):
            raise ValueError(f"{r['claim_id']}: 定性/假说不得冒充数值检查")
        outputs.append((r, value))
    return outputs


def validate_cases(cases, loaded, gate):
    unique(cases, ("gate", "case_id"), "cases")
    for row in cases:
        if row["gate"] not in {"G3", "G4"} or row["required"] not in {"yes", "no"}:
            raise ValueError("cases: gate仅G3/G4，required仅yes/no")
        if row["required"] == "no" and not row["reason"]:
            raise ValueError(f"{row['case_id']}: 非必测格缺适用性理由")
        for column in ("function", "precision", "interface", "lmul", "vlen", "mask", "frm", "input_group"):
            if not row[column]:
                raise ValueError(f"{row['case_id']}: 矩阵维度 {column} 不能为空")
        integer(row["expected_count"], "expected_count", 1)
        if row["vector_required"] not in {"yes", "no"}:
            raise ValueError("cases: vector_required仅yes/no")
    if "coverage.csv" in loaded:
        expected = {r["case_id"]: r for r in cases if r["gate"] == "G3"}
        actual = {r["case_id"]: r for r in loaded["coverage.csv"]}
        if not expected or actual.keys() != expected.keys():
            raise ValueError("coverage: 与预登记矩阵的用例集合不一致")
        for key, row in actual.items():
            plan = expected[key]
            for column in ("gate", "function", "precision", "interface", "lmul", "vlen", "mask", "frm", "input_group", "expected_count", "metric", "limit"):
                if row[column] != plan[column]:
                    raise ValueError(f"{key}: {column} 偏离预登记矩阵")
            if plan["required"] == "yes" and row["status"] != "pass":
                raise ValueError(f"{key}: 必测格未通过")
    if "measurements.csv" in loaded:
        expected = {r["case_id"]: r for r in cases if r["gate"] == "G4" and r["required"] == "yes"}
        groups = {}
        for row in loaded["measurements.csv"]:
            groups.setdefault(row["run_id"], {}).setdefault(row["case_id"], []).append(row)
        for run, by_case in groups.items():
            if not expected or by_case.keys() != expected.keys():
                raise ValueError(f"{run}: 性能场景集合偏离预登记矩阵")
            for key, rows in by_case.items():
                plan = expected[key]
                if len(rows) != int(plan["expected_count"]):
                    raise ValueError(f"{key}: 性能窗口数量不完整")
                phases = {}
                for item in plan["phases"].split(";"):
                    phase, count = item.split(":")
                    if phase in phases or phase not in {"first", "warmup", "steady"}:
                        raise ValueError("cases: 重复或未知phase")
                    phases[phase] = integer(count, "phase count")
                actual = {p: sum(r["phase"] == p for r in rows) for p in phases}
                if actual != phases or sum(phases.values()) != len(rows):
                    raise ValueError(f"{key}: phase数量偏离预登记矩阵")
                if any(r["unit"] != plan["unit"] for r in rows):
                    raise ValueError(f"{key}: 统计单位偏离预登记矩阵")
                if plan["vector_required"] == "yes" and any(int(r["vector_calls"]) == 0 for r in rows):
                    raise ValueError(f"{key}: 没有实际向量调用证据")


def validate_artifact_references(task, loaded):
    if not hasattr(task, "artifacts"):
        return  # 主机纯数据测试；正式 CLI 总是传入 Task。
    artifacts = task.artifacts()
    by_id = {r["artifact_id"]: r for r in artifacts}
    for artifact in artifacts:
        path = Path(task.path(artifact["path"]))
        if not path.is_file() or digest(path) != artifact["sha256"]:
            raise ValueError(f"{artifact['artifact_id']}: 证据缺失或hash变化")
    for filename in loaded:
        file = root(task) / filename
        matches = [r for r in artifacts if Path(task.path(r["path"])).resolve() == file.resolve()]
        if len(matches) != 1 or matches[0]["sha256"] != digest(file):
            raise ValueError(f"{filename}: 必须唯一登记且hash匹配")
    for filename, columns in (("coverage.csv", ("evidence_id",)), ("runs.csv", ("identity_id", "tool_id", "binary_id", "library_id", "link_id", "evidence_id")), ("claims.csv", ("evidence_id",))):
        for row in loaded.get(filename, []):
            for key in columns:
                if row[key] not in by_id:
                    raise ValueError(f"{filename}: {key} 未登记 {row[key]}")
    for run in loaded.get("runs.csv", []):
        for key in ("binary_id", "library_id", "link_id"):
            if not by_id[run[key]]["sha256"]:
                raise ValueError(f"{run['run_id']}: 缺少逐对象链接身份")
        original = by_id[run["evidence_id"]]
        if original["sha256"] != run["source_sha256"]:
            raise ValueError(f"{run['run_id']}: 原始证据hash不匹配")
        env = read_csv(task.path(by_id[run["identity_id"]]["path"]))
        unique(env, ("key",), "environment")
        values = {r["key"]: r["value"] for r in env}
        if not ENV_KEYS <= values.keys() or any(not values[k] for k in ENV_KEYS):
            raise ValueError(f"{run['run_id']}: 环境身份字段不完整")
        if run["role"] != "validation" and values["simulator_family"] not in {"gsim", "verilator", "hardware"}:
            raise ValueError(f"{run['run_id']}: 功能模拟器不能提供正式性能")
        for key in ENV_KEYS:
            if key.endswith("sha256") and not re.fullmatch(r"[a-f0-9]{64}", values[key]):
                raise ValueError(f"{run['run_id']}: {key}格式错误")


def check_task(task, gate=None):
    errors, loaded = [], {}
    directory = root(task)
    required = {"G0": ("cases.csv",), "G3": ("cases.csv", "coverage.csv"), "G4": ("cases.csv", "runs.csv", "measurements.csv"),
                "G5": ("cases.csv", "coverage.csv", "runs.csv", "measurements.csv", "claims.csv")}.get(gate, ())
    for filename, fields in FORMATS.items():
        path = directory / filename
        if path.exists():
            try:
                loaded[filename] = read_csv(path, fields)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        elif filename in required:
            errors.append(f"{gate}: 缺少 {filename}")
    try:
        validate_artifact_references(task, loaded)
        if "cases.csv" in loaded:
            validate_cases(loaded["cases.csv"], loaded, gate)
        elif any(k in loaded for k in ("coverage.csv", "measurements.csv")):
            raise ValueError("缺少预登记 cases.csv")
        if "coverage.csv" in loaded:
            validate_coverage(loaded["coverage.csv"])
        if "runs.csv" in loaded:
            validate_runs(loaded["runs.csv"])
        comp = []
        if "measurements.csv" in loaded:
            comp = comparisons(loaded["measurements.csv"], loaded.get("runs.csv", []))
        if "claims.csv" in loaded:
            evaluate_claims(loaded["claims.csv"], loaded.get("coverage.csv", []), comp)
    except (ValueError, KeyError, InvalidOperation, ZeroDivisionError) as exc:
        errors.append(str(exc))
    return errors


def import_data(task, kind, source, output):
    """旧工具输出只形成待补充记录，绝不猜测实际调用/身份/测试通过。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    directory = root(task)
    if not output.is_relative_to(directory) or output.is_symlink():
        raise ValueError("导入输出必须位于显式任务 data/workflow 内")
    rows = read_csv(source)
    converted = []
    if kind == "libmvec-samples":
        needed = {"record_type", "function", "mode", "n", "k", "round", "r", "phase", "cycles", "cycle_begin", "cycle_end", "checksum", "run_status", "case_status", "measurement_valid"}
        if not needed <= rows[0].keys():
            raise ValueError("不是支持的 libmvec samples CSV")
        for old in rows:
            if old["record_type"] == "calibration":
                continue  # 校准记录保留在原件，不混入目标测量。
            if old["record_type"] != "sample":
                raise ValueError("未知 record_type；拒绝猜测样本类型")
            row = dict.fromkeys(MEASUREMENTS, "")
            row.update(schema_version=SCHEMA, run_id="NEEDS_RUN_ID", case_id=f"{old['function']}:{old['mode']}:n{old['n']}:k{old['k']}",
                       sample_id=f"{old['phase']}:{old['round']}:{old['r']}", phase=old["phase"], cycles=old["cycles"], cycle_begin=old["cycle_begin"],
                       cycle_end=old["cycle_end"], counter_bits="64", timing_repeat=old.get("timing_repeat") or "1",
                       checksum=old["checksum"], status="not_run", unit="cycle/window")
            # 只保留原始通过状态；整体门禁仍因缺实际工作量及运行身份而阻断。
            if old["run_status"] == old["case_status"] == "passed" and old["measurement_valid"].lower() == "true":
                row["status"] = "pass"
            converted.append(row)
        fields = MEASUREMENTS
    elif kind == "newmath-coverage":
        needed = {"function", "interface", "lmul", "expected_count", "actual_count", "ulp_limit", "status"}
        if not needed <= rows[0].keys():
            raise ValueError("不是支持的 newmath coverage CSV")
        for index, old in enumerate(rows):
            row = dict.fromkeys(COVERAGE, "")
            row.update(schema_version=SCHEMA, case_id=f"import-{index+1}-{old['function']}-{old['interface']}-m{old['lmul']}",
                       gate="G3", function=old["function"], interface=old["interface"], lmul=old["lmul"],
                       status="not_run", expected_count=old["expected_count"], actual_count=old["actual_count"],
                       metric="encoding-step", limit=old["ulp_limit"], reason="导入待核对运行身份、观测误差、VLEN、语义范围及独立批准")
            converted.append(row)
        fields = COVERAGE
    else:
        raise ValueError("未知适配器；不得猜测 schema")
    if not converted:
        raise ValueError("没有可导入目标样本（校准不计入）")
    receipt = output.with_suffix(output.suffix + ".source.csv")
    if output.exists() or receipt.exists():
        raise ValueError("导入结果或来源记录已存在")
    write_csv(output, fields, converted)
    write_csv(receipt, ["schema_version", "adapter", "source_name", "source_sha256", "adapter_sha256", "rows", "acceptance"],
              [dict(schema_version=SCHEMA, adapter=kind, source_name=source.name, source_sha256=digest(source),
                    adapter_sha256=digest(__file__), rows=len(rows), acceptance="imported-unverified")])
    return [output, receipt]
