"""Synthetic collector regression tests, not evidence of successful gsim runs."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('collector', HERE / 'collect_veclibm_gsim.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def performance_text(name='exp', n=16, repeats=3):
    lines = [f'VECLIBM_CONFIG n={n} repeats={repeats} vlen_bits=128 baseline=static-libm']
    for mode, cycles, checksum in [('libm', 160, 42), ('rvvlm', 40, 999)]:
        for i in range(repeats):
            lines.append(f'VECLIBM_SAMPLE function={name} mode={mode} sample={i} n={n} cycles={cycles+i*16} insns={20+i} checksum={checksum}')
    return '\n'.join(lines + [f'VECLIBM_FUNCTION_COMPLETE function={name}', 'VECLIBM_COMPLETE functions=1'])


def accuracy_text(name='acos.test', checks=2, failed=0):
    return (f'VECLIBM_ACCURACY_START case={name} points=16 grid_points=4 repeats=1 ldbl_bits=113\n'
            'Maximum observed absolute error is 0.0001\n'
            'Maximum observed relative error is 0.0001\n'
            f'VECLIBM_ASSERT op=LT pass={int(not failed)} lhs=0.5 rhs=1\n'
            f'VECLIBM_ACCURACY_COMPLETE case={name} checks={checks} failed={failed} body_instructions=10000\n')


class LogsAndMetrics(unittest.TestCase):
    def performance_case(self):
        return {'original_name': 'exp', 'manifest_entry': {'n': 16, 'repeats': 3}}

    def test_ansi_and_command_headers_removed(self):
        self.assertEqual(collector.clean_log('cmd: fake VECLIBM_COMPLETE ERROR\n\x1b[32mOK\x1b[0m'), 'OK')

    def test_error_diagnostics_not_failures(self):
        text = accuracy_text()
        self.assertEqual(collector.error_markers(text), [])
        self.assertEqual(collector.error_markers('[PERF] errors_recovered, 0'), [])

    def test_zero_failure_field_does_not_hide_real_error(self):
        for message in ['ERROR output bad', 'HIT BAD TRAP', 'HIT CRITICAL ERROR', 'Assertion check failed',
                        'VECLIBM_ASSERT op=LT pass=0', 'Timeout expired']:
            with self.subTest(message=message):
                self.assertTrue(collector.error_markers('failed=0\n' + message))

    def test_nonzero_failed_field_rejected(self):
        self.assertTrue(collector.error_markers('failed=2'))

    def test_performance_medians_and_different_checksums(self):
        result, issues = collector.performance_metrics(performance_text(), self.performance_case())
        self.assertEqual(issues, [])
        self.assertEqual(len(result['samples']), 6)
        self.assertEqual(result['modes']['libm']['median_cycles_per_element'], 11)
        self.assertEqual(result['modes']['rvvlm']['median_elements_per_cycle'], 16/56)
        self.assertAlmostEqual(result['libm_to_rvvlm_cycle_ratio'], 176/56)

    def test_missing_sample_rejected(self):
        text = performance_text().replace('VECLIBM_SAMPLE function=exp mode=rvvlm sample=2', 'IGNORED function=exp mode=rvvlm sample=2')
        _, issues = collector.performance_metrics(text, self.performance_case())
        self.assertTrue(any('expected 3 samples' in x for x in issues))

    def test_duplicate_sample_rejected(self):
        text = performance_text().replace('mode=rvvlm sample=2', 'mode=rvvlm sample=1')
        _, issues = collector.performance_metrics(text, self.performance_case())
        self.assertTrue(any('duplicate sample' in x for x in issues))

    def test_wrong_n_function_and_mode_rejected(self):
        for old, new in [('function=exp mode=rvvlm', 'function=cos mode=rvvlm'),
                         ('sample=1 n=16', 'sample=1 n=32'), ('mode=rvvlm', 'mode=other')]:
            with self.subTest(old=old):
                _, issues = collector.performance_metrics(performance_text().replace(old, new), self.performance_case())
                self.assertTrue(issues)

    def test_accuracy_assertion_and_diagnostic_classes(self):
        case = {'original_name': 'acos.test', 'manifest_entry': {'validation': 'upstream-assertions', 'points':16,'grid_points':4,'repeats':1}}
        metrics, issues = collector.accuracy_metrics(accuracy_text(), case)
        self.assertEqual(issues, [])
        self.assertEqual(metrics['checks'], 2)
        _, issues = collector.accuracy_metrics(accuracy_text(checks=0), case)
        self.assertIn('assertion-bearing test executed zero checks', issues)
        case['manifest_entry']['validation'] = 'diagnostic-only'
        _, issues = collector.accuracy_metrics(accuracy_text(checks=0), case)
        self.assertEqual(issues, [])
        _, issues = collector.accuracy_metrics(accuracy_text(checks=2), case)
        self.assertTrue(issues)

    def test_accuracy_failed_and_wrong_marker_rejected(self):
        case = {'original_name': 'acos.test', 'manifest_entry': {'validation': 'upstream-assertions'}}
        for text in [accuracy_text(failed=1), accuracy_text(name='cos.test'), accuracy_text()+accuracy_text()]:
            with self.subTest(text=text):
                _, issues = collector.accuracy_metrics(text, case)
                self.assertTrue(issues)

    def test_actual_gsim_counter_format_not_nemu_total(self):
        result = collector.host_counters('total guest instructions = 4,790\nCore-0 instrCnt = 5,139, cycleCnt = 33,467, IPC = 0.153554\n')
        self.assertEqual(result['gsim_cores'][0]['retired_instructions'], 5139)
        self.assertEqual(result['gsim_cores'][0]['simulation_cycles'], 33467)
        self.assertNotIn('retired_instructions', result)


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name)
        self.logs = self.run / 'logs/20261107-000000'
        self.logs.mkdir(parents=True)
        self.case = self.make_case('accuracy-acos.test', 'accuracy', 'acos.test')

    def make_case(self, name, suite, original):
        image = self.run / (name + '.bin')
        image.write_bytes(('image for ' + name).encode())
        digest = collector.sha256(image)
        entry = {'status':'built', 'sha256': digest}
        if suite == 'performance':
            entry.update(n=16, repeats=3, require_marker='VECLIBM_COMPLETE functions=1')
        else:
            entry.update(points=16,grid_points=4,repeats=1,validation='upstream-assertions',
                         require_marker=f'VECLIBM_ACCURACY_COMPLETE case={original}')
        return dict(name=name,suite=suite,original_name=original,bin=str(image),staged_bin=str(image),sha256=digest,manifest_entry=entry)

    def log_case(self, case=None, text=None, passed=True):
        case = case or self.case
        text = accuracy_text(name=case['original_name']) if text is None else text
        (self.logs / f'run-{case["name"]}.log').write_text('cmd: fake ERROR bad path\n' + text + '\nCore 0: HIT GOOD TRAP\n')
        (self.logs / f'run-{case["name"]}.err').write_text('cmd: fake ERROR bad path\n')
        if passed:
            with (self.logs / '.pass_list').open('a') as f:
                f.write(case['name'] + '\n')

    def metadata(self, cases=None):
        (self.run / 'run-metadata.json').write_text(json.dumps({'cases': cases or [self.case]}))

    def test_pass_requires_list_goodtrap_and_marker(self):
        self.log_case()
        result = collector.audit_case(self.case, [self.logs], False)
        self.assertTrue(result['functional_pass'],result['failure_reasons'])
        self.assertEqual(result['metrics']['checks'],2)
        self.assertEqual(result['error_markers'],[])

    def test_reference_good_trap_alone_is_not_rtl_completion(self):
        self.log_case()
        log=self.logs / f'run-{self.case["name"]}.log'
        log.write_text(log.read_text().replace('Core 0: HIT GOOD TRAP','nemu: HIT GOOD TRAP'))
        result=collector.audit_case(self.case,[self.logs],False)
        self.assertTrue(result['any_good_trap'])
        self.assertFalse(result['good_trap'])
        self.assertFalse(result['functional_pass'])

    def test_bad_trap_overrides_pass_list(self):
        self.log_case(text=accuracy_text()+'\nHIT BAD TRAP')
        result = collector.audit_case(self.case,[self.logs],False)
        self.assertFalse(result['functional_pass'])

    def test_missing_marker_even_good_trap_is_rejected(self):
        self.log_case(text='ordinary partial output')
        result = collector.audit_case(self.case,[self.logs],False)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(any('unique marker' in r for r in result['failure_reasons']))

    def test_half_log_is_running_not_failed(self):
        self.log_case(text='VECLIBM_ACCURACY_START case=acos.test points=16',passed=False)
        result = collector.audit_case(self.case,[self.logs],False)
        self.assertEqual(result['status'],'running')
        self.assertFalse(result['functional_pass'])
        self.assertTrue(result['validation_issues'])

    def test_no_log_is_pending_then_missing_final_failure(self):
        self.assertEqual(collector.audit_case(self.case,[self.logs],False)['status'],'pending')
        final = collector.audit_case(self.case,[self.logs],True)
        self.assertEqual(final['status'],'failed')

    def test_full_log_without_pass_list_remains_running_until_final(self):
        self.log_case(passed=False)
        self.assertEqual(collector.audit_case(self.case,[self.logs],False)['status'],'running')
        self.assertEqual(collector.audit_case(self.case,[self.logs],True)['status'],'failed')

    def test_timeout_list_overrides_runner_pass(self):
        self.log_case()
        (self.logs / '.timeout_list').write_text(self.case['name']+'\n')
        result = collector.audit_case(self.case,[self.logs],True)
        self.assertEqual(result['status'],'timeout')
        self.assertFalse(result['functional_pass'])

    def test_duplicate_log_attempt_is_ambiguous(self):
        self.log_case()
        extra = self.run / 'logs/second'
        extra.mkdir()
        (extra / f'run-{self.case["name"]}.log').write_text('partial')
        result = collector.audit_case(self.case,[self.logs,extra],False)
        self.assertFalse(result['functional_pass'])
        self.assertTrue(any('ambiguous' in x for x in result['failure_reasons']))

    def test_staged_hash_mutation_rejected(self):
        self.log_case()
        Path(self.case['staged_bin']).write_bytes(b'changed')
        self.assertFalse(collector.audit_case(self.case,[self.logs],False)['functional_pass'])

    def test_failed_manifest_cannot_pass(self):
        self.log_case()
        self.case['manifest_entry']['status']='failed'
        self.assertFalse(collector.audit_case(self.case,[self.logs],False)['functional_pass'])

    def test_metadata_unsafe_name_and_duplicate_original_rejected(self):
        bad=copy.deepcopy(self.case); bad['name']='../oops'
        with self.assertRaises(ValueError): collector.validate_metadata({'cases':[bad]})
        bad=copy.deepcopy(self.case); bad['name']='another'
        with self.assertRaises(ValueError): collector.validate_metadata({'cases':[self.case,bad]})

    def test_report_progress_updates_only_reports(self):
        self.metadata()
        self.log_case(passed=False,text='partial')
        before=(self.logs / f'run-{self.case["name"]}.log').read_bytes()
        result=collector.collect(self.run)
        self.assertEqual(result['state'],'in_progress')
        self.assertFalse(result['all_passed'])
        self.assertEqual(before,(self.logs / f'run-{self.case["name"]}.log').read_bytes())
        self.assertTrue((self.run/'reports/results.csv').is_file())
        self.assertEqual(collector.main(['--run',str(self.run)]),0)

    def test_final_missing_coverage_is_failure(self):
        self.metadata(); self.log_case()
        (self.run/'runner-exit.json').write_text('{"returncode":0}')
        result=collector.collect(self.run)
        self.assertEqual(result['state'],'failed')
        self.assertTrue(result['audit_issues'])
        self.assertEqual(collector.main(['--run',str(self.run)]),1)

    def complete_run(self, **metadata):
        cases=[]
        # Synthetic suite-count completion tests final aggregation, not real gsim.
        for i in range(25):
            c=self.make_case(f'performance-p{i}','performance',f'p{i}')
            self.log_case(c,performance_text(name=c['original_name']))
            cases.append(c)
        for i in range(141):
            c=self.make_case(f'accuracy-a{i}.test','accuracy',f'a{i}.test')
            if i >= 131:
                c['manifest_entry']['validation']='diagnostic-only'
            self.log_case(c,accuracy_text(name=c['original_name'],checks=0 if i >= 131 else 2))
            cases.append(c)
        (self.run/'run-metadata.json').write_text(json.dumps(dict(cases=cases, **metadata)))
        (self.run/'runner-exit.json').write_text('{"returncode":0}')
        return cases

    def launch_metadata(self, jobs=64, timeout='48h'):
        reference = self.run/'reference.so'
        reference.write_bytes(b'reference identity')
        return dict(jobs=jobs, timeout=timeout, diff_enabled=True,
                    reference=dict(path=str(reference), sha256=collector.sha256(reference)),
                    command=['run_emu.sh', '-j', str(jobs), '--timeout', timeout, '-r', str(reference)])

    def test_all_166_requirements_then_success(self):
        cases=self.complete_run()
        result=collector.collect(self.run)
        self.assertEqual(result['state'],'passed',result['audit_issues'])
        self.assertEqual(result['summary']['passed'],166)
        self.assertEqual(result['summary']['accuracy_checks_passed_cases'],262)
        self.assertEqual(result['summary']['diagnostic_only_passed'],10)
        (self.logs/'.timeout_list').write_text(cases[0]['name']+'\n')
        self.assertEqual(collector.collect(self.run)['state'],'failed')

    def test_recorded_launch_options_allow_defaults_and_explicit_overrides(self):
        self.complete_run()
        metadata_path=self.run/'run-metadata.json'
        cases=json.loads(metadata_path.read_text())['cases']
        for jobs, timeout in [(64, '48h'), (2, '30m')]:
            with self.subTest(jobs=jobs, timeout=timeout):
                metadata=dict(cases=cases, **self.launch_metadata(jobs, timeout))
                metadata_path.write_text(json.dumps(metadata))
                result=collector.collect(self.run)
                self.assertEqual(result['state'], 'passed', result['audit_issues'])

    def test_command_must_match_recorded_options_and_keep_differential_testing(self):
        self.complete_run(**self.launch_metadata(2, '30m'))
        metadata_path=self.run/'run-metadata.json'
        original=json.loads(metadata_path.read_text())
        for change in ('jobs', 'timeout', 'reference', 'reference-hash', 'no-diff', 'diff-enabled'):
            with self.subTest(change=change):
                metadata=copy.deepcopy(original)
                command=metadata['command']
                if change == 'jobs': command[command.index('-j') + 1]='64'
                elif change == 'timeout': command[command.index('--timeout') + 1]='48h'
                elif change == 'reference': command[command.index('-r') + 1]='/different/reference.so'
                elif change == 'reference-hash': metadata['reference'].pop('sha256')
                elif change == 'no-diff': command.append('--no-diff')
                else: metadata['diff_enabled']=False
                metadata_path.write_text(json.dumps(metadata))
                result=collector.collect(self.run)
                self.assertEqual(result['state'], 'failed')
                self.assertTrue(result['audit_issues'])

    def test_explicit_reference_identity_must_still_match(self):
        metadata=self.launch_metadata(2, '30m')
        self.complete_run(**metadata)
        Path(metadata['reference']['path']).write_bytes(b'changed reference')
        result=collector.collect(self.run)
        self.assertEqual(result['state'], 'failed')
        self.assertIn('reference identity no longer matches metadata SHA256', result['audit_issues'])

    def test_legacy_command_metadata_retains_default_contract(self):
        command=['run_emu.sh', '-j', '64', '--timeout', '48h', '-r',
                 '/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum']
        self.complete_run(command=command)
        result=collector.collect(self.run)
        self.assertEqual(result['state'], 'passed', result['audit_issues'])
        metadata_path=self.run/'run-metadata.json'
        metadata=json.loads(metadata_path.read_text())
        metadata['command'][metadata['command'].index('-j') + 1]='2'
        metadata_path.write_text(json.dumps(metadata))
        self.assertEqual(collector.collect(self.run)['state'], 'failed')

    def test_invalid_recorded_options_are_rejected(self):
        self.complete_run(**self.launch_metadata())
        metadata_path=self.run/'run-metadata.json'
        original=json.loads(metadata_path.read_text())
        for field, value in [('jobs', 0), ('jobs', 65), ('jobs', True), ('timeout', 'invalid'), ('timeout', 48)]:
            with self.subTest(field=field, value=value):
                metadata=copy.deepcopy(original)
                metadata[field]=value
                metadata_path.write_text(json.dumps(metadata))
                result=collector.collect(self.run)
                self.assertEqual(result['state'], 'failed')
                self.assertTrue(result['audit_issues'])

    def test_report_symlink_rejected(self):
        self.metadata()
        target=self.run/'external'; target.mkdir()
        (self.run/'reports').symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError): collector.collect(self.run)


if __name__ == '__main__':
    unittest.main()
