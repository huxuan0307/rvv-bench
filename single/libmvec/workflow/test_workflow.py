"""主机门禁负例与报告核算；合成夹具不是 RISC-V 实测。"""
from __future__ import annotations

import copy
import csv
import os
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as E
import reporting


def row(fields, **values):
    result = dict.fromkeys(fields, "")
    result.update(schema_version="1", **values)
    return result


def fixture(task):
    directory = E.root(task)
    directory.mkdir(parents=True)
    case = row(E.CASES, case_id="log-Nx", gate="G3", function="log", precision="f64", interface="Nx", lmul="1", vlen="128", mask="none", frm="RNE", input_group="normal", required="yes", expected_count="2", metric="encoding-step", limit="1", vector_required="yes")
    perf = row(E.CASES, case_id="log-Nx", gate="G4", function="log", precision="f64", interface="Nx", lmul="1", vlen="128", mask="none", frm="RNE", input_group="normal", required="yes", expected_count="3", phases="steady:3", unit="cycle/vector-element", vector_required="yes")
    coverage = row(E.COVERAGE, **{k:v for k,v in case.items() if k in E.COVERAGE and k != "schema_version"}, status="pass", actual_count="2", observed="0", evidence_id="raw-functional")
    runs = [row(E.RUNS, run_id=role, role=role, pair_id="p1", status="pass", returncode="0", completion="complete", checks="2", failures="0", identity_id="env", tool_id="tool", binary_id=role+"-bin", library_id=role+"-lib", link_id=role+"-link", source_sha256="a"*64, evidence_id="raw") for role in ("baseline", "candidate")]
    samples = [row(E.MEASUREMENTS, run_id=role, case_id="log-Nx", sample_id=str(i), phase="steady", cycles=str(cycles), cycle_begin="1000", cycle_end=str(1000+cycles), counter_bits="64", calls="4", elements="16", timing_repeat="2", vector_calls="4", vector_elements="16", unit="cycle/vector-element", checksum="123" if role=="baseline" else "124", status="pass") for role, cycles in (("baseline",160),("candidate",128)) for i in range(3)]
    claim = row(E.CLAIMS, claim_id="C1", kind="numeric", metric="change_pct", selector="p1:log-Nx", operator="eq", expected="-20", unit="%", scope="合成测试夹具", text="该合成场景周期下降", evidence_id="raw")
    data = {"cases.csv":[case,perf], "coverage.csv":[coverage], "runs.csv":runs, "measurements.csv":samples,"claims.csv":[claim]}
    for name, rows in data.items():
        E.write_csv(directory/name, E.FORMATS[name], rows)
    return data


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="workflow-tests-", dir=os.environ.get("TEST_TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name)/"task"
        self.task.mkdir()
        self.data = fixture(self.task)

    def replace(self, name, rows):
        with (E.root(self.task)/name).open("w", newline="", encoding="utf-8") as stream:
            writer=csv.DictWriter(stream,fieldnames=E.FORMATS[name]); writer.writeheader(); writer.writerows(rows)

    def test_valid_and_exact_normalization(self):
        self.assertEqual(E.check_task(self.task,"G5"), [])
        result=E.comparisons(self.data["measurements.csv"],self.data["runs.csv"])[0]
        self.assertEqual((result["baseline"],result["candidate"],result["change_pct"]),(10,8,-20))
        self.assertEqual(E.normalized(self.data["measurements.csv"][0]),10)

    def test_partial_missing_or_duplicate_rejected(self):
        for rows in (self.data["measurements.csv"][:-1],self.data["measurements.csv"]+[self.data["measurements.csv"][0]]):
            self.replace("measurements.csv",rows)
            self.assertTrue(E.check_task(self.task,"G4"))

    def test_skipped_required_case_rejected(self):
        self.data["coverage.csv"][0].update(status="not_applicable",reason="unavailable")
        self.replace("coverage.csv",self.data["coverage.csv"])
        self.assertTrue(E.check_task(self.task,"G3"))

    def test_false_claim_rejected(self):
        self.data["claims.csv"][0]["operator"]="ge"
        self.data["claims.csv"][0]["expected"]="0"
        self.replace("claims.csv",self.data["claims.csv"])
        self.assertIn("矛盾",str(E.check_task(self.task,"G5")))

    def test_environment_mismatch_rejected(self):
        self.data["runs.csv"][1]["identity_id"]="different"
        self.replace("runs.csv",self.data["runs.csv"])
        self.assertTrue(E.check_task(self.task,"G4"))

    def test_zero_vector_execution_rejected(self):
        for r in self.data["measurements.csv"]: r["vector_calls"]="0"
        self.replace("measurements.csv",self.data["measurements.csv"])
        self.assertTrue(E.check_task(self.task,"G4"))

    def test_checksum_per_run_only(self):
        self.assertEqual(E.check_task(self.task,"G4"),[])
        self.data["measurements.csv"][0]["checksum"]="999"
        self.replace("measurements.csv",self.data["measurements.csv"])
        self.assertTrue(E.check_task(self.task,"G4"))

    def test_uint64_wrap_exact(self):
        rows=copy.deepcopy(self.data["measurements.csv"])
        for r in rows:
            r["cycle_begin"]=str(2**64-10);r["cycle_end"]=str(int(r["cycles"])-10)
        E.validate_measurements(rows,self.data["runs.csv"])
        rows[0]["cycles"]="1.0"
        with self.assertRaises(ValueError): E.validate_measurements(rows,self.data["runs.csv"])

    def test_protocol_unknown_rejected(self):
        self.data["coverage.csv"][0]["schema_version"]="2"
        self.replace("coverage.csv",self.data["coverage.csv"])
        self.assertTrue(E.check_task(self.task,"G3"))

    def test_no_overwrite_and_bad_import(self):
        with self.assertRaises(ValueError): E.write_csv(E.root(self.task)/"cases.csv",E.CASES,[])
        with self.assertRaises(ValueError): E.import_data(self.task,"guess",E.root(self.task)/"cases.csv",E.root(self.task)/"import.csv")

    def test_newmath_import_is_unverified(self):
        source=self.task/"data"/"old-coverage.csv"
        E.write_csv(source,["function","interface","lmul","expected_count","actual_count","ulp_limit","status"],[dict(function="log",interface="Nx",lmul="1",expected_count="2",actual_count="2",ulp_limit="1",status="pass")])
        paths=E.import_data(self.task,"newmath-coverage",source,E.root(self.task)/"imported.csv")
        self.assertEqual(len(paths),2)
        self.assertEqual(E.read_csv(paths[0])[0]["status"],"not_run")
        self.assertEqual(E.read_csv(paths[1])[0]["source_sha256"],E.digest(source))

    def test_samples_import_does_not_mix_calibration(self):
        source=self.task/"data"/"old-samples.csv"
        fields="record_type,function,mode,n,k,round,r,phase,cycles,cycle_begin,cycle_end,checksum,run_status,case_status,measurement_valid".split(",")
        samples=[dict(zip(fields,["sample","log","direct_m1","16","4","0","0","steady","160","1000","1160","123","passed","passed","True"]))]
        samples.append(dict(samples[0],record_type="calibration"))
        E.write_csv(source,fields,samples)
        paths=E.import_data(self.task,"libmvec-samples",source,E.root(self.task)/"imported.csv")
        imported=E.read_csv(paths[0]);self.assertEqual(len(imported),1)
        self.assertEqual(imported[0]["sample_id"],"steady:0:0")
        self.assertEqual(imported[0]["calls"],"")
        with self.assertRaises(ValueError):E.validate_measurements(imported,self.data["runs.csv"])

    def test_report_svg_matches_exact_csv(self):
        output=self.task/"report"/"generated"
        paths=reporting.generate(self.task,output)
        self.assertEqual(len(paths),5)
        svg=ET.parse(output/"performance.svg")
        values=[node.attrib["data-value"] for node in svg.iter() if "data-value" in node.attrib]
        self.assertEqual(values,["-20"])
        self.assertEqual(E.read_csv(output/"performance.csv")[0]["change_pct"],"-20")
        with self.assertRaises(ValueError): reporting.generate(self.task,output)

    def test_validation_only_rejected(self):
        for r in self.data["runs.csv"]: r["role"]="validation"
        self.replace("runs.csv",self.data["runs.csv"])
        self.assertTrue(E.check_task(self.task,"G4"))

    def test_empty_numeric_budget_rejected(self):
        for r in self.data["coverage.csv"]: r["limit"]=r["observed"]=""
        self.data["cases.csv"][0]["limit"]=""
        self.replace("coverage.csv",self.data["coverage.csv"])
        self.replace("cases.csv",self.data["cases.csv"])
        self.assertTrue(E.check_task(self.task,"G3"))

    def test_negative_error_rejected(self):
        self.data["coverage.csv"][0]["observed"]="-1"
        self.replace("coverage.csv",self.data["coverage.csv"])
        self.assertTrue(E.check_task(self.task,"G3"))

    def test_vector_denominators(self):
        r=copy.deepcopy(self.data["measurements.csv"][0])
        r.update(calls="8",vector_calls="4",elements="32",vector_elements="16",unit="cycle/vector-call")
        self.assertEqual(E.normalized(r),40)
        r["unit"]="cycle/vector-element"
        self.assertEqual(E.normalized(r),10)

    def test_mutated_coverage_gate_rejected(self):
        self.data["coverage.csv"][0]["gate"]="G4"
        self.replace("coverage.csv",self.data["coverage.csv"])
        self.assertTrue(E.check_task(self.task,"G3"))

    def test_report_does_not_repeat_unverified_numeric_text(self):
        self.data["claims.csv"][0]["text"]="回退99%且全面成功"
        self.replace("claims.csv",self.data["claims.csv"])
        paths=reporting.generate(self.task,self.task/"report"/"draft")
        md=next(p for p in paths if p.suffix==".md").read_text()
        self.assertNotIn("回退99%",md)
        self.assertIn("草稿",md)

    def test_report_rejects_missing_case(self):
        self.replace("coverage.csv",[])
        with self.assertRaises(ValueError): reporting.generate(self.task,self.task/"report"/"bad")


class NewApiMatrixTests(unittest.TestCase):
    """新函数 API 矩阵（input_group='new-api'）正负例；合成夹具。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="newapi-tests-", dir=os.environ.get("TEST_TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0

    def build(self):
        cases, coverage = [], []
        for lmul, interface, mask in E.NEW_API_GRID:
            required = "yes" if lmul in E.NEW_API_REQUIRED_LMULS else "no"
            case = row(E.CASES, case_id=f"log-f64-m{lmul}-{interface}-{mask}", gate="G3", function="log", precision="f64",
                       interface=interface, lmul=lmul, vlen="128", mask=mask, frm="RNE", input_group="new-api",
                       required=required, reason="" if required == "yes" else "lmul4/8 按契约可选，见 G0-03",
                       expected_count="1", metric="encoding-step", limit="1", vector_required="yes")
            cases.append(case)
            coverage.append(row(E.COVERAGE, **{k: case[k] for k in E.COVERAGE if k in case and k != "schema_version"},
                                status="pass" if required == "yes" else "not_applicable",
                                actual_count="1" if required == "yes" else "0",
                                observed="0" if required == "yes" else "", evidence_id="raw-functional"))
        return cases, coverage

    def write(self, cases, coverage=None):
        self.count += 1
        task = self.root / f"task-{self.count}"
        E.write_csv(E.root(task) / "cases.csv", E.CASES, cases)
        if coverage is not None:
            E.write_csv(E.root(task) / "coverage.csv", E.COVERAGE, coverage)
        return task

    def test_complete_matrix_passes_g0_and_g3(self):
        cases, coverage = self.build()
        task = self.write(cases, coverage)
        self.assertEqual(E.check_task(task, "G0"), [])
        self.assertEqual(E.check_task(task, "G3"), [])

    def test_missing_cell_rejected(self):
        cases, coverage = self.build()
        del cases[0]
        del coverage[0]
        task = self.write(cases, coverage)
        self.assertIn("漏格", " ".join(E.check_task(task, "G0")))
        self.assertTrue(E.check_task(task, "G3"))

    def test_duplicate_combo_rejected(self):
        cases, coverage = self.build()
        duplicate = copy.deepcopy(cases[0])
        duplicate["case_id"] = "log-f64-duplicate"
        cases.append(duplicate)
        coverage.append(dict(coverage[0], case_id="log-f64-duplicate"))
        task = self.write(cases, coverage)
        self.assertIn("重复", " ".join(E.check_task(task, "G0")))

    def test_required_policy(self):
        cases, coverage = self.build()
        for case in cases:
            if case["lmul"] == "1":
                case["required"] = "no"
                case["reason"] = "合成负例"
        task = self.write(cases, coverage)
        self.assertIn("必须 required=yes", " ".join(E.check_task(task, "G0")))
        cases, coverage = self.build()
        for case in cases:
            if case["lmul"] == "4":
                case["required"] = "no"
                case["reason"] = ""
        task = self.write(cases, coverage)
        self.assertTrue(E.check_task(task, "G0"))

    def test_multiple_vlen_allowed_and_interface_enum(self):
        cases, coverage = self.build()
        extra = copy.deepcopy(cases[0])
        extra["case_id"] = "log-f64-m1-fixed-masked-v256"
        extra["vlen"] = "256"
        cases.append(extra)
        coverage.append(dict(coverage[0], case_id=extra["case_id"], vlen="256"))
        task = self.write(cases, coverage)
        self.assertEqual(E.check_task(task, "G0"), [])
        self.assertEqual(E.check_task(task, "G3"), [])
        cases[1]["interface"] = "Nx"
        task = self.write(cases, coverage)
        self.assertIn("interface", " ".join(E.check_task(task, "G0")))

    def test_g4_cannot_claim_omitted_api(self):
        cases, _ = self.build()
        omitted = next(r for r in cases if r['lmul'] == '8')
        perf = dict(omitted, gate='G4', required='yes')
        self.assertIn('不是已承诺', ' '.join(E.check_task(self.write(cases + [perf]), 'G0')))

    def test_precision_cannot_supply_another_precisions_missing_cell(self):
        cases, _ = self.build()
        cases[-1]['precision'] = 'f32'
        self.assertIn('漏格', ' '.join(E.check_task(self.write(cases), 'G0')))

    def test_legacy_and_g4_orphan(self):
        legacy = row(E.CASES, case_id="exp-Nx", gate="G3", function="exp", precision="f64", interface="Nx",
                     lmul="1", vlen="128", mask="none", frm="RNE", input_group="normal", required="yes",
                     expected_count="1", metric="encoding-step", limit="1", vector_required="yes")
        self.assertIsNone(E.validate_new_api_matrix([legacy]))
        cases, coverage = self.build()
        orphan = row(E.CASES, case_id="exp-f64-m1-fixed-unmasked", gate="G4", function="exp", precision="f64",
                     interface="fixed", lmul="1", vlen="128", mask="unmasked", frm="RNE", input_group="new-api",
                     required="yes", expected_count="1", phases="steady:1", unit="cycle/vector-element",
                     vector_required="yes", metric="encoding-step", limit="1")
        cases.append(orphan)
        task = self.write(cases, coverage)
        self.assertIn("缺少 G3 矩阵声明", " ".join(E.check_task(task, "G0")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
