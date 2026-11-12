"""使用替身runner/emu验证外部后端，不运行RTL。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import run as runner
import run_emu_backend as backend
import runner_common as common


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def executable(self, name, body):
        p = self.root / name
        p.write_text('#!' + sys.executable + '\n' + body)
        p.chmod(0o755)
        return p

    def config(self, behavior='pass', emu_body=None, count=2):
        emu = self.executable('emu', emu_body or 'import os,sys\nprint("GUEST:"+os.getcwd())\nprint("ERR",file=sys.stderr)\n')
        ref = self.root / 'ref.so';ref.write_text('ref')
        dram = self.root / 'dram.ini';dram.write_text('dram')
        external = self.executable('runner', '''import pathlib,subprocess,sys
args=sys.argv[1:]
def opt(x):return args[args.index(x)+1]
logs=pathlib.Path(opt('-o'))/'stamp';logs.mkdir()
for image in sorted(pathlib.Path(opt('-t')).glob('*.bin')):
 name=image.stem
 mode=BEHAVIOR
 if mode=='missing':continue
 if mode!='timeout-missing':
  p=subprocess.run([opt('-e'),'--diff',opt('-r'),'-i',str(image)])
 status='timeout' if mode.startswith('timeout') else ('fail' if p.returncode else 'pass')
 with (logs/('.'+status+'_list')).open('a') as f:f.write(name+'\\n')
 if mode=='conflict':
  with (logs/'.fail_list').open('a') as f:f.write(name+'\\n')
sys.exit(0)
'''.replace('BEHAVIOR', repr(behavior)))
        run = self.root / 'run';run.mkdir();(run/'staging').mkdir()
        cases=[]
        tools={'gsim':common.identity(emu),'reference':common.identity(ref),'dram_config':common.identity(dram),'runner':common.identity(external)}
        for n in range(count):
            name=f'perf-{n}';image=run/'staging'/(name+'.bin');image.write_bytes(bytes([n]))
            work=run/'cases'/name/'work';work.mkdir(parents=True)
            out=work.parent/'dram';out.mkdir()
            case={'name':name,'suite':'performance','staged_bin':str(image),'sha256':common.sha256(image),
                  'workdir':str(work),'dram_outdir':str(out),'stdout':str(work.parent/'stdout.log'),
                  'stderr':str(work.parent/'stderr.log'),'result':str(work.parent/'result.json')}
            case['command']=common.gsim_command(emu,ref,image,dram,out,123456789)
            cases.append(case)
        meta={'created_at':common.utc_now(),'mode':'gsim','tools':tools,'cases':cases,
              'policy':{'jobs':32,'timeout':'2s','backend':'run-emu','seed':123456789}}
        backend.prepare(run,meta,external)
        common.write_json(run/'run-metadata.json',meta)
        return {'run':run,'metadata':meta,'cases':cases,'timeout_seconds':2,'mode':'gsim'}

    def result(self, c):
        return json.loads(Path(c['result']).read_text())

    def test_actual_commands_cwd_logs_and_external_receipt(self):
        config=self.config()
        self.assertEqual(backend.execute(config),0)
        info=config['metadata']['external_runner']
        self.assertEqual(info['command'][info['command'].index('-j')+1],'32')
        for case in config['cases']:
            record=self.result(case)
            self.assertEqual(record['command'],case['command'])
            self.assertEqual(record['returncode'],0)
            self.assertEqual(record['backend'],'run-emu')
            self.assertEqual(record['external_status'],'pass')
            self.assertEqual(Path(case['stdout']).read_text(),'GUEST:'+case['workdir']+'\n')
            self.assertEqual(Path(case['stderr']).read_text(),'ERR\n')
        self.assertEqual(json.loads((config['run']/'external-result.json').read_text())['issues'],[])

    def test_external_zero_cannot_hide_missing_timeout_or_conflict(self):
        for behavior in ('missing','timeout','timeout-missing','conflict'):
            with self.subTest(behavior=behavior):
                # 每种失败情况使用独立新目录。
                with tempfile.TemporaryDirectory() as d:
                    original=self.root;self.root=Path(d)
                    try:
                        config=self.config(behavior)
                        self.assertEqual(backend.execute(config),1)
                        receipt=json.loads((config['run']/'external-result.json').read_text())
                        self.assertEqual(receipt['returncode'],0)
                        self.assertTrue(receipt['issues'])
                        if behavior.startswith('timeout'):
                            self.assertTrue(self.result(config['cases'][0])['timed_out'])
                    finally:self.root=original

    def test_nonzero_real_emu_fails_even_external_zero(self):
        config=self.config(emu_body='raise SystemExit(7)\n')
        self.assertEqual(backend.execute(config),1)
        self.assertEqual(self.result(config['cases'][0])['returncode'],7)

    def test_proxy_rejects_unknown_image_ref_and_mutated_binary(self):
        config=self.config(count=1);case=config['cases'][0];ref=config['metadata']['tools']['reference']['realpath']
        self.assertEqual(backend.proxy_main(config['run'],['--no-diff','-i',case['staged_bin']]),125)
        self.assertEqual(backend.proxy_main(config['run'],['--diff',ref,'-i','unknown']),125)
        self.assertEqual(backend.proxy_main(config['run'],['--diff','wrong','-i',case['staged_bin']]),125)
        self.assertIn('reference',self.result(case)['launch_error'])
        Path(case['staged_bin']).write_bytes(b'changed')
        self.assertEqual(backend.proxy_main(config['run'],['--diff',ref,'-i',case['staged_bin']]),125)
        self.assertIn('BIN',self.result(case)['launch_error'])

    def test_signal_records_and_propagates(self):
        config=self.config(count=1,emu_body='import time\ntime.sleep(30)\n')
        case=config['cases'][0];info=config['metadata']['external_runner']
        p=subprocess.Popen([info['proxy']['realpath'],'--diff',config['metadata']['tools']['reference']['realpath'],'-i',case['staged_bin']])
        self.addCleanup(lambda:p.kill() if p.poll() is None else None)
        deadline=time.monotonic()+5
        while not Path(case['stdout']).exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(Path(case['stdout']).exists())
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5),143)
        record=self.result(case)
        self.assertTrue(record['aborted'])
        self.assertEqual(record['state'],'aborted')
        self.assertEqual(record['signals'],[signal.SIGTERM])
        self.assertNotEqual(record['returncode'],0)

    def test_installed_external_runner_with_fake_emu(self):
        installed=Path('/nfs/home/huxuan/opt/bin/run_emu.sh')
        if not installed.is_file():self.skipTest('installed run_emu.sh unavailable')
        config=self.config(count=1)
        info=config['metadata']['external_runner']
        actual=installed.resolve()
        config['metadata']['tools']['runner']=common.identity(actual)
        info['runner']=config['metadata']['tools']['runner']
        info['command'][0]=str(actual)
        common.write_json(config['run']/'run-metadata.json',config['metadata'])
        self.assertEqual(backend.execute(config),0)
        record=self.result(config['cases'][0])
        self.assertEqual(record['external_status'],'pass')
        self.assertEqual(record['returncode'],0)
        self.assertNotIn('cmd:',Path(config['cases'][0]['stdout']).read_text())
        directories=list(Path(info['log_root']).iterdir())
        self.assertEqual(len(directories),1)
        self.assertIn('cmd:',(directories[0]/'run-perf-0.log').read_text())

    def test_argument_limits_preserve_direct(self):
        parser=runner.build_parser()
        for backend_name,jobs,valid in [('direct',4,True),('direct',5,False),('run-emu',32,True),('run-emu',33,False)]:
            args=parser.parse_args(['gsim','--manifest','manifest.json','--backend',backend_name,'--jobs',str(jobs)])
            if valid:runner.validate_arguments(parser,args)
            else:
                with self.assertRaises(SystemExit):runner.validate_arguments(parser,args)


if __name__=='__main__':unittest.main()
