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


if __name__ == "__main__":
    unittest.main(verbosity=2)
