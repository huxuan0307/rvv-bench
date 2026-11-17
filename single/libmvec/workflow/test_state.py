"""状态和审阅的主机回归；会话ID为显式合成夹具，不是实际批准。"""
from __future__ import annotations

import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import core as C
import evidence as E
from test_workflow import fixture


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="state-tests-",dir=os.environ.get("TEST_TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"task"; self.path.mkdir()
        self.task=C.Task.load(self.path); self.task.init("lead")
        staging=self.path/"data"/"fixture"; fixture(staging)
        source=E.root(staging)/"cases.csv"
        (self.task.state/"cases.csv").write_bytes(source.read_bytes())
        (self.path/"data"/"contract.md").write_text("合成契约，绝非实际libmvec验收。\n")
        (self.path/"data"/"scope.md").write_text("full-development；fixture only\n")
        self.task.artifact("contract","data/contract.md","G0","author",artifact_id="contract")
        self.task.artifact("target_matrix","data/workflow/cases.csv","G0","author",artifact_id="matrix")
        self.task.freeze([("file","contract_sha256","data/contract.md"),("file","scope_sha256","data/scope.md"),("file","target_matrix_sha256","data/workflow/cases.csv")],"G0","author")

    def review(self,gate="G0",reviewer="reviewer",verdict="approve",role="process",items=None):
        return self.task.record_review(gate,reviewer,"fixture","fixture-model",role,"全部必做项（合成测试）",verdict,"synthetic-session",1,2,
            items or [(item,"pass","合成证据已检查","contract") for item in C.REQUIRED_ITEMS[gate]])

    def advance(self,gate="G0"):
        return self.task.advance(gate,"lead",self.task.revision(),"synthetic-session:1-2;fixture-only")

    def test_valid_g0_then_no_duplicate_advance(self):
        self.review(); self.assertEqual(self.task.check("G0"),[])
        self.advance(); self.assertEqual(self.task.check("G0"),[])
        with self.assertRaises(C.WorkflowError): self.advance()

    def test_no_self_review(self):
        with self.assertRaises(C.WorkflowError): self.review(reviewer="author")

    def test_missing_checklist_rejected(self):
        self.review(items=[("made-up","pass","synthetic","contract")])
        with self.assertRaises(C.WorkflowError): self.advance()

    def test_review_requires_real_evidence(self):
        items=[(item,"pass","synthetic","does-not-exist") for item in C.REQUIRED_ITEMS["G0"]]
        self.review(items=items)
        with self.assertRaises(C.WorkflowError): self.advance()

    def test_bad_revision_rejected(self):
        self.review()
        with self.assertRaises(C.WorkflowError): self.task.advance("G0","lead",999,"fixture")

    def test_changed_evidence_stales_then_can_reapprove(self):
        self.review(); self.advance()
        (self.path/"data"/"contract.md").write_text("第二版合成契约\n")
        self.assertTrue(self.task.check("G0"))
        self.task.artifact("contract","data/contract.md","G0","author",artifact_id="contract2",supersedes="contract")
        self.task.freeze([("file","contract_sha256","data/contract.md")],"G0","author")
        self.review(items=[(item,"pass","第二版","contract2") for item in C.REQUIRED_ITEMS["G0"]])
        self.advance()
        self.assertEqual(self.task.gate_row("G0")["attempt"],"2")
        self.assertEqual(len(self.task.read("artifacts")),3)
        self.assertEqual(len(self.task.artifacts()),2)

    def test_negative_review_cannot_be_bypassed(self):
        self.review(reviewer="blocker",verdict="blocked")
        self.review(reviewer="other")
        with self.assertRaises(C.WorkflowError): self.advance()
        self.review(reviewer="blocker")
        self.advance()

    def test_review_identity_change_requires_reapproval(self):
        self.review();self.advance()
        self.task.record_review("G0","reviewer","fixture","new-model","process","全部必做项（合成测试）","approve","new-synthetic-session",3,4,[(item,"pass","合成证据","contract") for item in C.REQUIRED_ITEMS["G0"]])
        self.assertTrue(self.task.check("G0"))
        self.assertIn("review-binding",self.task.status()[0]["stale"])
        self.advance();self.assertEqual(self.task.check("G0"),[])

    def test_concurrent_advance_single_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.review()
        def attempt(_):
            try:
                C.Task.load(self.path).advance("G0","lead",0,"synthetic-verification")
                return "passed"
            except C.WorkflowError:return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
        self.assertEqual(sorted(results),["blocked","passed"])
        self.assertEqual(len(self.task.state_rows()),1)

    def test_cli_unknown_schema_and_init(self):
        import subprocess
        cli=str(Path(C.__file__).with_name("cli.py"))
        result=subprocess.run([sys.executable,"-B",cli,"--help"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        result=subprocess.run([sys.executable,"-B",cli,"init","--task",str(self.path),"--author","lead"],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        result=subprocess.run([sys.executable,"-B",cli,"check","--task",str(self.path),"--gate","G0","--evidence-only"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        result=subprocess.run([sys.executable,"-B",cli,"import-data","--task",str(self.path),"--kind","unknown","--source",str(self.path/"data/contract.md"),"--output",str(self.task.state/"new.csv")],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)

    def test_gate_order_rejected(self):
        with self.assertRaises(C.WorkflowError): self.advance("G1a")

    def test_layout_checked_at_advance(self):
        self.review(); (self.path/"surprise.txt").write_text("x")
        with self.assertRaises(C.WorkflowError): self.advance()

    def test_symlink_escape_rejected(self):
        outside=Path(self.tmp.name)/"outside.md";outside.write_text("x")
        (self.path/"data"/"escape.md").symlink_to(outside)
        with self.assertRaises(C.WorkflowError): self.task.artifact("raw","data/escape.md","G0","author")

    def test_state_table_symlink_rejected(self):
        other=Path(self.tmp.name)/"other";other.mkdir()
        state=other/"data"/"workflow";state.mkdir(parents=True)
        (state/"meta.csv").symlink_to(self.task.state/"meta.csv")
        with self.assertRaises(C.WorkflowError):C.Task.load(other)

    def test_missing_state_under_escaped_parent_rejected(self):
        other=Path(self.tmp.name)/"other";other.mkdir()
        external=Path(self.tmp.name)/"external";external.mkdir()
        (other/"data").symlink_to(external)
        with self.assertRaises(C.WorkflowError):C.Task.load(other)

    def test_meta_duplicate_and_path_copy_rejected(self):
        with (self.task.state/"meta.csv").open("a") as stream: stream.write("tool_version,1\n")
        with self.assertRaises(C.WorkflowError): self.task.check("G0")

    def test_all_gates_and_upstream_retraction(self):
        staging=self.path/"data"/"fixture"
        source=E.root(staging)
        def artifact(kind,gate,identifier,text="合成证据；非实际RISC-V运行。"):
            path=self.path/"data"/(identifier+".md")
            path.write_text(text)
            return self.task.artifact(kind,str(path),gate,"author",artifact_id=identifier)
        def table(name,gate,kind):
            destination=self.task.state/name
            destination.write_bytes((source/name).read_bytes())
            return self.task.artifact(kind,str(destination),gate,"author",artifact_id=kind)
        for gate in C.GATES:
            if gate=="G1a":
                artifact("design",gate,"design");artifact("measurement_protocol",gate,"protocol")
                self.task.freeze([("file","design_sha256","data/design.md"),("file","protocol_sha256","data/protocol.md")],gate,"author")
            elif gate=="G1b":
                artifact("baseline",gate,"baseline")
                artifact("tool",gate,"tool"); raw=artifact("raw",gate,"raw")
                artifact("raw",gate,"raw-functional")
                env=self.path/"data"/"environment.csv"
                E.write_csv(env,["key","value"],[{"key":k,"value":("a"*64 if k.endswith("sha256") else "gsim" if k=="simulator_family" else "fixture")} for k in sorted(E.ENV_KEYS)])
                self.task.artifact("environment",str(env),gate,"author",artifact_id="env")
                for role in ("baseline","candidate"):
                    for suffix in ("bin","lib","link"): artifact("source",gate,role+"-"+suffix)
                entries=[("set",key,("a"*64 if key=="diff_sha256" else "fixture")) for key in C.FREEZE_KEYS[gate] if key not in {"inputs_sha256","oracle_sha256"}]
                entries += [("file",key,"data/raw.md") for key in ("inputs_sha256","oracle_sha256")]
                self.task.freeze(entries,gate,"author")
            elif gate=="G2": artifact("screening",gate,"screening")
            elif gate=="G3": table("coverage.csv",gate,"coverage")
            elif gate=="G4":
                runs=E.read_csv(source/"runs.csv",E.RUNS)
                for r in runs:r["source_sha256"]=E.digest(self.path/"data"/"raw.md")
                with (source/"runs.csv").open("w",newline="") as stream:
                    writer=csv.DictWriter(stream,fieldnames=E.RUNS);writer.writeheader();writer.writerows(runs)
                table("runs.csv",gate,"runs");table("measurements.csv",gate,"measurements")
            elif gate=="G5":
                table("claims.csv",gate,"claims")
                paths=self.task.report(self.path/"report"/"fixture-draft")
                report=next(Path(p) for p in paths if str(p).endswith(".md"))
                self.task.artifact("report",str(report),gate,"author",artifact_id="report")
            elif gate=="G6": artifact("delivery",gate,"delivery")
            self.review(gate,reviewer="reviewer",role="process")
            if C.REQUIRED_REVIEWERS[gate]==2:self.review(gate,reviewer="reviewer-two",role="data")
            self.assertEqual(self.task.check(gate),[],gate)
            self.advance(gate)
        self.assertEqual(self.task.check(),[])
        self.review("G0",reviewer="reviewer",verdict="blocked")
        self.assertTrue(self.task.check("G6"))
        last=self.task.status()[-1]
        self.assertTrue(last["stale"])

    def test_state_hash_corruption_rejected(self):
        self.review();self.advance()
        path=self.task.state/"state.csv"
        text=path.read_text();path.write_text(text.replace("passed","blocked"))
        self.assertTrue(self.task.check("G0"))


if __name__=="__main__":
    unittest.main(verbosity=2)
