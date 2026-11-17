"""从规范 CSV 生成待审阅报告与 SVG，不生成未经测量的性能数据。"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, localcontext
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET

import evidence


def decimal(value):
    with localcontext() as context:
        context.prec = 40
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def display(value):
    return f"{float(value):.3f}"


def markdown(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def svg_document(title, rows, mode):
    height = max(180, 120 + 42 * len(rows))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             '<style>text{font-family:system-ui,sans-serif;fill:#182432;font-size:14px}</style>',
             f'<text x="24" y="32" style="font-size:22px">{escape(title)}</text>']
    if not rows:
        parts.append('<text x="24" y="90">无接受数据；不提供性能推测。</text>')
    if mode == "performance":
        parts.append('<text x="24" y="62">变化率=(候选/基线−1)×100%；负值表示周期降低；各行独立场景，未加权。</text>')
        maximum = max([abs(float(r["change_pct"])) for r in rows] + [1])
        parts.append(f'<line x1="670" y1="80" x2="670" y2="{height-20}" stroke="#8797a8"/>')
        for index, row in enumerate(rows):
            y = 100 + 42 * index
            change = float(row["change_pct"])
            width = abs(change) / maximum * 190
            x = 670 - width if change < 0 else 670
            key = f"{row['pair_id']}:{row['case_id']}"
            color = "#20846c" if change <= 0 else "#ba4d3e"
            parts.extend([f'<g data-key="{escape(key, quote=True)}" data-value="{decimal(row["change_pct"])}" data-unit="%">',
                          f'<text x="24" y="{y+16}">{escape(key[:70])}</text>',
                          f'<rect x="{x:.3f}" y="{y}" width="{max(width,0.5):.3f}" height="24" fill="{color}"/>',
                          f'<text x="885" y="{y+16}">{change:+.3f}%</text></g>'])
    else:
        parts.append('<text x="24" y="62">每行一个预登记验证用例；颜色不代表超出该用例的覆盖。</text>')
        for index, row in enumerate(rows):
            y = 100 + 42 * index
            color = "#20846c" if row["status"] == "pass" else "#66788a"
            parts.extend([f'<g data-key="{escape(row["case_id"], quote=True)}" data-value="{escape(row["status"], quote=True)}">',
                          f'<text x="24" y="{y+16}">{escape(row["case_id"][:80])}</text>',
                          f'<rect x="740" y="{y}" width="220" height="24" fill="{color}"/>',
                          f'<text x="750" y="{y+16}" style="fill:white">{escape(row["status"])}</text></g>'])
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def generate(task, output_dir):
    task_dir = Path(getattr(task, "dir", task)).resolve()
    output = Path(output_dir).resolve()
    if not output.is_relative_to(task_dir / "report"):
        raise ValueError("报告输出必须位于显式任务 report 内")
    if output.exists() or Path(output_dir).is_symlink():
        raise ValueError("报告输出目录必须不存在")
    issues = evidence.check_task(task, gate="G5")
    if issues:
        raise ValueError("报告拒收：" + "; ".join(issues))
    directory = evidence.root(task)
    coverage = evidence.read_csv(directory / "coverage.csv", evidence.COVERAGE)
    runs = evidence.read_csv(directory / "runs.csv", evidence.RUNS)
    samples = evidence.read_csv(directory / "measurements.csv", evidence.MEASUREMENTS)
    claims = evidence.read_csv(directory / "claims.csv", evidence.CLAIMS)
    compared = evidence.comparisons(samples, runs)
    checked_claims = evidence.evaluate_claims(claims, coverage, compared)
    output.mkdir(parents=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    table = output / "performance.csv"
    fields = ["pair_id", "case_id", "baseline", "candidate", "change_pct", "unit"]
    serial = [{k: decimal(v) if k in {"baseline", "candidate", "change_pct"} else v for k, v in r.items()} for r in compared]
    evidence.write_csv(table, fields, serial)
    paths = [table]
    for name, title, rows, mode in (("performance.svg", "libmvec 分场景周期变化", compared, "performance"),
                                     ("coverage.svg", "libmvec 功能验证覆盖", coverage, "coverage")):
        path = output / name
        path.write_text(svg_document(title, rows, mode), encoding="utf-8")
        ET.parse(path)
        paths.append(path)
    lines = ["# libmvec 功能与性能报告", "", "- 状态：草稿（非当前交付依据；独立审阅后须发布到 report 根目录）", "- 阶段：review", "",
             "本报告仅描述规范证据表登记的范围。定性、因果和推广范围仍需独立审阅。", "", "## 结论索引", "",
             "| ID | 类型 | 结论 | 核算值 | 单位 | 适用范围 | 证据 ID |", "|---|---|---|---|---|---|---|"]
    for row, value in checked_claims:
        lines.append("| " + " | ".join(markdown(v) for v in (row["claim_id"], row["kind"], (f"{row['metric']} {row['operator']} {row['expected']}（数据键 {row['selector']}）" if value is not None else row["text"]),
                     display(value) if value is not None else "需独立推理", row["unit"], row["scope"], row["evidence_id"])) + " |")
    lines += ["", "## 功能覆盖", "", "![功能覆盖](coverage.svg)", "", "| 用例 | 函数 | 接口 | LMUL | VLEN | 状态 | 期望/实际 | 误差定义 | 预算/观测 |", "|---|---|---|---|---|---|---|---|---|"]
    for row in coverage:
        lines.append("| " + " | ".join(markdown(v) for v in (row["case_id"], row["function"], row["interface"], row["lmul"], row["vlen"],
                     row["status"], row["expected_count"] + "/" + row["actual_count"], row["metric"], row["limit"] + "/" + row["observed"])) + " |")
    lines += ["", "## 性能比较", "", "![性能变化](performance.svg)", "", "[权威派生表](performance.csv)", "",
              "| 配对 | 场景 | 基线 | 候选 | 周期变化 % | 单位 |", "|---|---|---|---|---|---|"]
    for row in compared:
        lines.append("| " + " | ".join(markdown(v) for v in (row["pair_id"], row["case_id"], display(row["baseline"]), display(row["candidate"]), display(row["change_pct"]), row["unit"])) + " |")
    lines += ["", "## 方法、字段与限制", "",
              "每个场景仅对 steady 窗口归一化值取中位数；first 和 warmup 留在原始数据但不参与该中位数。calls/elements 表示窗口实际接口调用/元素总数，vector_calls/vector_elements 表示实际向量调用/元素数；各单位分别使用对应分母，均已包含 timing_repeat，不重复相除。数值结论正文由结构化字段生成，claims 中自由 text 只供审阅，不直接作为已核算摘要。周期变化为 (candidate/baseline−1)×100%，负值表示降低。阈值检查用原始有理数，显示值四舍五入到三位。重复确定性窗口不构成独立统计样本。", "",
              "pair_id 是匹配环境的 A/B 标识；case_id 是预登记场景；baseline/candidate 是基线/候选的同单位中位数；unit 是 cycle/window、cycle/call、cycle/element 或带 vector 的对应单位。LMUL 是向量寄存器分组，VLEN 是向量长度位数。pass 仅表示该行规定范围通过；not_applicable 表示契约声明不适用，需要独立审阅理由。encoding-step 与数学真值 ULP 不等同。numeric 是已核算数值，qualitative 为定性结论，hypothesis 为待验证假说。", "",
              "图表和数值一致性不证明算法全域误差、真实动态调用链或微架构因果；代码、环境和原始证据须由相应门禁审阅。脚本不生成加权总体收益。", "", "## 永久证据", ""]
    import os
    for name in evidence.FORMATS:
        path = directory / name
        lines.append(f"- [{name}](<{os.path.relpath(path, output)}>), SHA256 `{evidence.digest(path)}`")
    report = output / f"libmvec-summary-review-{stamp}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(report)
    manifest = output / "generation.csv"
    evidence.write_csv(manifest, ["path", "sha256", "generator_sha256", "approval"],
                       [dict(path=p.name, sha256=evidence.digest(p), generator_sha256=evidence.digest(__file__), approval="pending") for p in paths])
    return paths + [manifest]
