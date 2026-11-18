"""CSV 证据校验与显式适配；不启动目标程序，不把导入当作通过。"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath
from stat import S_IFMT, S_IFREG
from statistics import median
from zipfile import BadZipFile, ZipFile

SCHEMA = "1"
COVERAGE = "schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,status,expected_count,actual_count,metric,limit,observed,reason,evidence_id".split(",")
RUNS = "schema_version,run_id,role,pair_id,status,returncode,completion,checks,failures,identity_id,tool_id,binary_id,library_id,link_id,source_sha256,evidence_id".split(",")
MEASUREMENTS = "schema_version,run_id,case_id,sample_id,phase,cycles,cycle_begin,cycle_end,counter_bits,calls,elements,timing_repeat,vector_calls,vector_elements,unit,checksum,status".split(",")
CLAIMS = "schema_version,claim_id,kind,metric,selector,operator,expected,unit,scope,text,evidence_id".split(",")
CASES = "schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,required,expected_count,phases,metric,limit,unit,vector_required,reason".split(",")
LTO_CASE_DIMS = ("function", "precision", "interface", "lmul", "vlen", "mask", "frm")
LTO_KEY = (*LTO_CASE_DIMS, "language", "route", "link", "policy")
LTO_RESULTS = ["schema_version", *LTO_KEY, "lto", "source_sha256", "sdk_sha256",
               "elf_sha256", "batches", "vector_call_sites", "actual_vector_calls",
               "tested_values", "max_ulp", "limit_ulp", "exit_code", "status",
               "source_manifest", "elf", "link_map", "disassembly", "runtime"]
LTO_SOURCES = ("schema_version", "role", "path", "sha256")
LTO_TABLE = "consumer-lto.csv"
LTO_BUNDLE = "consumer-lto.zip"
LTO_GROUP = "consumer-lto"
LTO_ARTIFACT = "consumer_lto"
LTO_SDK_ARTIFACT = "consumer_lto_sdk"
LTO_BUNDLE_ARTIFACT = "consumer_lto_bundle"
LTO_PASS = re.compile(r"^AUTOVEC_PASS family=(\w+) tested=(\d+) max_ulp=(\d+) limit=(\d+)$", re.M)
FORMATS = {"cases.csv": CASES, "coverage.csv": COVERAGE, "runs.csv": RUNS, "measurements.csv": MEASUREMENTS, "claims.csv": CLAIMS}
IMPORT_KINDS = ("libmvec-samples", "newmath-coverage")
ENV_KEYS = {"simulator_family", "simulator_sha256", "reference_sha256", "dram_sha256", "isa", "vlen", "toolchain_sha256", "harness_sha256", "input_sha256", "flags", "window"}
STATUSES = {"pass", "fail", "skip", "not_run", "not_applicable"}
TEMP_REF = re.compile(r"(?:^|[/\\\s(<`])(tmp|log|runs)(?:[/\\])", re.I)

# 新函数 API 矩阵：只在 cases.csv 中 input_group='new-api' 的 G3 行出现时启用。
# 老 profile（历史优化任务、未声明新增公开接口）不带此标记，行为完全不变；
# 工具无法区分“本任务不是新增 API”与“忘了标记”，因此标记本身是 G0 契约
# 义务，须由审查者核对（G0-01/G0-03），机器检查不能替代该判断。
NEW_API_GROUP = "new-api"
NEW_API_LMULS = ("1", "2", "4", "8")
NEW_API_INTERFACES = ("fixed", "variable")
NEW_API_MASKS = ("masked", "unmasked")
NEW_API_REQUIRED_LMULS = {"1", "2"}
NEW_API_GRID = tuple((lmul, interface, mask)
                     for lmul in NEW_API_LMULS
                     for interface in NEW_API_INTERFACES
                     for mask in NEW_API_MASKS)


def root(task):
    return Path(getattr(task, "dir", task)).resolve() / "data" / "workflow"


def stream_digest(stream):
    h = hashlib.sha256()
    for part in iter(lambda: stream.read(1024 * 1024), b""):
        h.update(part)
    return h.hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return stream_digest(stream)


def parse_csv(stream, label, required):
    reader = csv.DictReader(stream)
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)):
        raise ValueError(f"{label}: 缺少或重复表头")
    if required and set(fields) != set(required):
        raise ValueError(f"{label}: schema 列不匹配，要求 {','.join(required)}")
    rows = list(reader)
    if not rows:
        raise ValueError(f"{label}: 空数据")
    for number, row in enumerate(rows, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"{label}:{number}: 字段数量错误")
        if required and row.get("schema_version") != SCHEMA:
            raise ValueError(f"{label}:{number}: 未知 schema_version")
    return rows


def read_csv(path, required=None):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return parse_csv(stream, Path(path).name, required)


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


def validate_new_api_matrix(cases, gate=None):
    """新函数 API 矩阵检查：只由 cases.csv 的 input_group='new-api' 触发。

    触发与兼容性：
    - 存在任意 input_group='new-api' 的 G3 cases 行时启用；无该标记时立即返回，
      历史优化任务（旧 profile）不受影响。不引入任何旁表或隐式默认。
    - 工具无法辨别“未声明新增 API”与“漏标”；标记是否真实完整属 G0 契约义务，
      须由独立审查核对（G0-01 需求/矩阵、G0-03 验收预算），机器检查不替代。

    每个 (function, precision) 群组必须正好覆盖 16 格：
      lmul{1,2,4,8} × interface{fixed,variable} × mask{masked,unmasked}
    - 漏格、同格同 vlen/frm 重复均拒；同一格允许按需登记多个 VLEN/frm
      （每个新增行都必须有对应 G3 coverage，既有 coverage 集合检查保证）。
    - lmul=1/2 必须 required=yes；lmul=4/8 可 required=no，但 reason 必填，
      其适用性由独立评审判断。
    - interface/mask 只接受上述词表；G4 的 new-api 行必须已有 G3 群组声明，
      防止性能场景脱离预登记矩阵。
    """
    tagged = [row for row in cases if row["input_group"] == NEW_API_GROUP]
    if not tagged:
        return
    g3 = [row for row in tagged if row["gate"] == "G3"]
    g4 = [row for row in tagged if row["gate"] == "G4"]
    if not g3:
        raise ValueError("cases: new-api 只有 G4 行而缺少 G3 预登记矩阵")
    groups = {}
    for row in g3:
        groups.setdefault((row["function"], row["precision"]), []).append(row)
    for (function, precision), rows in groups.items():
        if not function or not precision:
            raise ValueError("cases: new-api 的 function/precision 不能为空")
        cells = {}
        for row in rows:
            lmul, interface, mask = row["lmul"], row["interface"], row["mask"]
            if lmul not in NEW_API_LMULS:
                raise ValueError(
                    f"{row['case_id']}: new-api lmul 仅支持 {NEW_API_LMULS}")
            if interface not in NEW_API_INTERFACES:
                raise ValueError(
                    f"{row['case_id']}: new-api interface 仅 "
                    f"{NEW_API_INTERFACES}")
            if mask not in NEW_API_MASKS:
                raise ValueError(
                    f"{row['case_id']}: new-api mask 仅 {NEW_API_MASKS}")
            if not row["vlen"]:
                raise ValueError(f"{row['case_id']}: new-api 缺 vlen")
            signature = (lmul, interface, mask, row["vlen"], row["frm"])
            if signature in cells:
                raise ValueError(
                    f"{row['case_id']}: new-api 组合重复 {signature}")
            cells[signature] = row
            if row["required"] == "yes":
                continue
            if lmul in NEW_API_REQUIRED_LMULS:
                raise ValueError(
                    f"{row['case_id']}: new-api lmul={lmul} 必须 required=yes")
            if not row["reason"]:
                raise ValueError(
                    f"{row['case_id']}: new-api lmul={lmul} 非必测缺理由")
        present = {(cell[0], cell[1], cell[2]) for cell in cells}
        missing = [cell for cell in NEW_API_GRID if cell not in present]
        if missing:
            raise ValueError(
                f"{function}/{precision}: new-api 矩阵漏格 {sorted(missing)}")
    declared = set(groups)
    selected = {(r['function'], r['precision'], r['lmul'], r['interface'], r['mask'])
                for r in g3 if r['required'] == 'yes'}
    for row in g4:
        key = (row["function"], row["precision"])
        if key not in declared:
            raise ValueError(
                f"{row['case_id']}: G4 new-api 用例缺少 G3 矩阵声明 {key}")
        cell = (*key, row['lmul'], row['interface'], row['mask'])
        if cell[2:] not in NEW_API_GRID or (row['required'] == 'yes' and cell not in selected):
            raise ValueError(f"{row['case_id']}: G4 new-api 性能场景不是已承诺的有效接口 {cell}")


def consumer_lto_plan(cases):
    """Decode preregistered G3 pairs without changing the existing cases schema."""
    plans = {}
    for row in cases:
        group = row["input_group"]
        if group != LTO_GROUP and not group.startswith(LTO_GROUP + "/"):
            continue
        fields = group.split("/")
        label = row["case_id"]
        if len(fields) != 5:
            raise ValueError(f"{label}: consumer-lto 须指定 language/route/link/policy")
        _, language, route, link, policy = fields
        if (row["gate"] != "G3" or row["required"] != "yes"
                or policy not in {"positive", "strict"}
                or link not in {"static", "shared"}
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", language)
                or route not in {"native", "installed-contract"}
                or (route == "installed-contract" and
                    (language != "gcc-fortran" or policy != "positive"))
                or row["vector_required"] != ("yes" if policy == "positive" else "no")):
            raise ValueError(f"{label}: consumer-lto 预登记条件无效")
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", row["function"])
                or row["precision"] not in {"f32", "f64"}
                or row["interface"] not in NEW_API_INTERFACES
                or row["mask"] not in NEW_API_MASKS
                or row["lmul"] not in (*NEW_API_LMULS, "natural")
                or row["metric"] != "encoding-step"):
            raise ValueError(f"{label}: consumer-lto 函数、形状或指标无效")
        integer(row["vlen"], f"{label} VLEN", 128)
        integer(row["limit"], f"{label} limit")
        if int(row["expected_count"]) < 4 or int(row["expected_count"]) % 2:
            raise ValueError(f"{label}: consumer-lto expected_count 须为两档总数（偶数且至少 4）")
        key = (*[row[k] for k in LTO_CASE_DIMS], language, route, link, policy)
        if key in plans:
            raise ValueError(f"{label}: consumer-lto 预登记组合重复")
        plans[key] = row
    if plans and not any(key[-1] == "positive" for key in plans):
        raise ValueError("consumer-lto: 不能只登记 strict，缺少 positive")
    for key, row in plans.items():
        if key[-1] == "positive" and (*key[:-3], "native", key[-2], "strict") not in plans:
            raise ValueError(f"{row['case_id']}: consumer-lto 缺少配套 native strict 用例")
    return plans


def validate_lto_calls(result, plan):
    label = plan["case_id"]
    count = integer(result["vector_call_sites"], f"{label} 静态调用点数")
    calls = result["actual_vector_calls"].split(";") if result["actual_vector_calls"] else []
    if result["policy"] == "strict":
        if count or calls:
            raise ValueError(f"{label}: strict 最终 ELF 含应用 _ZGV 调用")
        return
    if not calls or len(calls) != len(set(calls)) or count < len(calls):
        raise ValueError(f"{label}: positive 缺少有效的最终 ELF 调用证据")
    requested = plan["lmul"]
    optional_tail = requested == "2" and plan["interface"] == "variable" and plan["mask"] == "unmasked"
    sew = int(plan["precision"][1:])
    branch = "M" if plan["mask"] == "masked" else "N"
    matched = False
    for symbol in calls:
        match = re.fullmatch(r"_ZGV([1248])([NM])(x|[0-9]+)v{1,2}_" + re.escape(plan["function"]), symbol)
        if not match:
            raise ValueError(f"{label}: 非目标向量调用 {symbol}")
        lmul, form, length = match.groups()
        expected_length = "x" if plan["interface"] == "variable" else str((128 // sew) * int(lmul))
        if form != branch or length != expected_length:
            raise ValueError(f"{label}: 调用形状不符合预登记 {symbol}")
        if requested in {"natural", lmul}:
            matched = True
        elif not (optional_tail and lmul == "1"):
            raise ValueError(f"{label}: 非目标 LMUL {symbol}")
    if not matched:
        raise ValueError(f"{label}: 未调用要求的 LMUL")


def validate_lto_bundle(path, results):
    # Bind results to preserved bytes without extracting or running the archive.
    with ZipFile(path) as archive:
        infos = archive.infolist()
        members = {info.filename: info for info in infos}
        if len(members) != len(infos):
            raise ValueError("consumer-lto: 归档成员重名")

        def member(name):
            posix = PurePosixPath(name)
            info = members.get(name)
            if (not name or posix.is_absolute() or ".." in posix.parts or "\\" in name
                    or posix.as_posix() != name or any(ord(c) < 32 for c in name)
                    or info is None or info.orig_filename != name or info.is_dir() or not info.file_size
                    or info.flag_bits & 1
                    or (info.create_system == 3 and S_IFMT(info.external_attr >> 16) not in {0, S_IFREG})):
                raise ValueError(f"consumer-lto: 归档成员无效或缺失 {name!r}")
            return info

        def read_small(name):
            if member(name).file_size > 1024 * 1024:
                raise ValueError(f"consumer-lto: 源码清单或运行输出过大 {name}")
            return archive.read(name)

        member("commands.txt")
        hashes, source_sets, binaries, runtime_paths = {}, {}, {}, set()

        def member_sha(name, *, elf=False):
            member(name)
            cache_key = name, elf
            if cache_key not in hashes:
                with archive.open(name) as stream:
                    if elf:
                        header = stream.read(20)
                        if (len(header) < 20 or header[:6] != b"\x7fELF\x02\x01"
                                or header[18:20] != b"\xf3\x00"):
                            raise ValueError(f"consumer-lto: 不是 RV64 LE ELF {name}")
                        stream.seek(0)
                    hashes[cache_key] = stream_digest(stream)
            return hashes[cache_key]

        for row in results:
            source_manifest = row["source_manifest"]
            if source_manifest not in source_sets:
                content = read_small(source_manifest)
                sources = parse_csv(io.StringIO(content.decode("utf-8")), source_manifest, LTO_SOURCES)
                unique(sources, ("role",), source_manifest)
                unique(sources, ("path",), source_manifest)
                for source in sources:
                    if member_sha(source["path"]) != source["sha256"]:
                        raise ValueError(f"consumer-lto: 源文件 hash 不匹配 {source['path']}")
                source_sets[source_manifest] = hashlib.sha256(content).hexdigest(), {s["role"] for s in sources}
            expected_roles = {"consumer", "kernel", "oracle", "checker"}
            if row["route"] == "installed-contract":
                expected_roles.add("installed-contract")
            if source_sets[source_manifest] != (row["source_sha256"], expected_roles):
                raise ValueError(f"consumer-lto: 源码清单 hash 或角色不匹配 {source_manifest}")
            if member_sha(row["elf"], elf=True) != row["elf_sha256"]:
                raise ValueError(f"consumer-lto: 最终 ELF hash 不匹配 {row['elf']}")
            binary_key = tuple(row[k] for k in (*LTO_KEY, "lto") if k != "vlen")
            for field in ("elf", "link_map", "disassembly"):
                name = row[field]
                member(name)
                if name in runtime_paths or binaries.setdefault(name, (field, binary_key)) != (field, binary_key):
                    raise ValueError(f"consumer-lto: 跨构建或证据类型复用归档成员 {name}")
            runtime = row["runtime"]
            if runtime in runtime_paths or runtime in binaries:
                raise ValueError(f"consumer-lto: 重复使用运行输出 {runtime}")
            runtime_paths.add(runtime)
            markers = LTO_PASS.findall(read_small(runtime).decode("utf-8", errors="replace"))
            expected = (row["function"], row["tested_values"], row["max_ulp"], row["limit_ulp"])
            if markers != [expected]:
                raise ValueError(f"consumer-lto: 运行完成标记与结果不匹配 {runtime}")


def validate_consumer_lto(task, plans, loaded, gate):
    if gate not in {None, "G3", "G4", "G5", "G6"}:
        return
    table = root(task) / LTO_TABLE
    bundle = root(task) / LTO_BUNDLE
    if not plans:
        if any(path.exists() or path.is_symlink() for path in (table, bundle)):
            raise ValueError("consumer-lto: 已有结果但没有 G0 预登记")
        return
    if table.is_symlink() or not table.is_file() or "coverage.csv" not in loaded:
        raise ValueError(f"consumer-lto: 缺少 {LTO_TABLE} 或 coverage.csv，不能通过 G3")
    results = read_csv(table, LTO_RESULTS)
    unique(results, (*LTO_KEY, "lto"), LTO_TABLE)
    grouped = {}
    sdk_ids = set()
    for result in results:
        key = tuple(result[k] for k in LTO_KEY)
        lto = result["lto"]
        if key not in plans or lto not in {"off", "on"}:
            raise ValueError(f"consumer-lto: 未预登记的结果 {key}/{lto}")
        label = plans[key]["case_id"]
        for field in ("source_sha256", "sdk_sha256", "elf_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", result[field]):
                raise ValueError(f"{label}: {field} 格式无效")
        sdk_ids.add(result["sdk_sha256"])
        if result["status"] != "pass" or result["exit_code"] != "0":
            raise ValueError(f"{label}: consumer-lto {lto} 未通过执行")
        batches = integer(result["batches"], f"{label} batches", 2)
        tested = integer(result["tested_values"], f"{label} tested_values", batches)
        if 2 * tested != int(plans[key]["expected_count"]):
            raise ValueError(f"{label}: tested_values 须精确匹配预登记总数的一半")
        error = integer(result["max_ulp"], f"{label} max_ulp")
        if result["limit_ulp"] != plans[key]["limit"] or error > int(result["limit_ulp"]):
            raise ValueError(f"{label}: consumer-lto 超预算或预算与预登记不同")
        validate_lto_calls(result, plans[key])
        grouped.setdefault(key, {})[lto] = result

    if len(sdk_ids) != 1:
        raise ValueError("consumer-lto: G3 结果混用不同 SDK 身份")
    coverage = {r["case_id"]: r for r in loaded["coverage.csv"]}
    evidence_id = None
    if hasattr(task, "artifacts"):
        artifacts = task.artifacts("G3")

        def require_artifact(kind, path):
            registered = [r for r in artifacts if r["kind"] == kind]
            # validate_artifact_references has already checked the on-disk digest.
            if len(registered) != 1 or Path(task.path(registered[0]["path"])) != path.resolve():
                raise ValueError(f"consumer-lto: 必须把 {path.name} 唯一登记为 G3 {kind}")
            return registered[0]["artifact_id"]

        evidence_id = require_artifact(LTO_ARTIFACT, table)
        sdk = [r for r in artifacts if r["kind"] == LTO_SDK_ARTIFACT]
        if len(sdk) != 1 or {sdk[0]["sha256"]} != sdk_ids:
            raise ValueError(f"consumer-lto: SDK hash 与 G3 登记的 {LTO_SDK_ARTIFACT} 不一致")
        require_artifact(LTO_BUNDLE_ARTIFACT, bundle)

    for key, plan in plans.items():
        label = plan["case_id"]
        pair = grouped.get(key, {})
        if pair.keys() != {"off", "on"}:
            raise ValueError(f"{label}: 缺少 LTO off/on 成对结果")
        off, on = pair["off"], pair["on"]
        if any(off[k] != on[k] for k in ("source_sha256", "batches", "tested_values")):
            raise ValueError(f"{label}: LTO off/on 源码或检查范围不同")
        record = coverage[label]
        if (record["status"] != "pass"
                or int(record["actual_count"]) != 2 * int(off["tested_values"])
                or number(record["observed"]) != max(int(r["max_ulp"]) for r in pair.values())
                or (evidence_id is not None and record["evidence_id"] != evidence_id)):
            raise ValueError(f"{label}: coverage 未与 LTO off/on 实测及证据对账")
    if bundle.is_symlink() or not bundle.is_file():
        raise ValueError(f"consumer-lto: 缺少 {LTO_BUNDLE}")
    validate_lto_bundle(bundle, results)


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
    validate_new_api_matrix(cases, gate)
    return consumer_lto_plan(cases)


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
        plans = {}
        if "cases.csv" in loaded:
            plans = validate_cases(loaded["cases.csv"], loaded, gate)
        elif any(k in loaded for k in ("coverage.csv", "measurements.csv")):
            raise ValueError("缺少预登记 cases.csv")
        if "coverage.csv" in loaded:
            validate_coverage(loaded["coverage.csv"])
        validate_consumer_lto(task, plans, loaded, gate)
        if "runs.csv" in loaded:
            validate_runs(loaded["runs.csv"])
        comp = []
        if "measurements.csv" in loaded:
            comp = comparisons(loaded["measurements.csv"], loaded.get("runs.csv", []))
        if "claims.csv" in loaded:
            evaluate_claims(loaded["claims.csv"], loaded.get("coverage.csv", []), comp)
    except (ValueError, KeyError, OSError, BadZipFile, csv.Error, InvalidOperation, ZeroDivisionError) as exc:
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
