#!/usr/bin/env python3
"""主机测试：分组、逐模式配对、位模式、异常值、截断及伪造控制流。"""
import array
import csv
import gzip
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import importlib.util
import sys
from unittest.mock import patch as _module_patch


def _load_script(name, relative):
    path = Path(__file__).resolve().parents[2] / 'script' / relative
    spec = importlib.util.spec_from_file_location('_libmvec_host_' + name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 检查需要临时模块登记，不修改 sys.path。
    with _module_patch.dict(sys.modules, {spec.name: module}):
        spec.loader.exec_module(module)
    return module

r = _load_script('rounding_accuracy', 'accuracy/rounding_accuracy.py')


class MetricTests(unittest.TestCase):
 def test_archive_rejects_link_script_and_thin_archive(self):
  with tempfile.TemporaryDirectory() as temp:
   archive=Path(temp)/'libm.a'
   archive.write_bytes(b'!<arch>\n')
   r.require_archive(archive,'--scalar-libm')
   for magic in (b'GROUP (libm-2.44.a libmvec.a)\n',b'!<thin>\n'):
    with self.subTest(magic=magic):
     archive.write_bytes(magic)
     with self.assertRaisesRegex(ValueError,'--scalar-libm'):
      r.require_archive(archive,'--scalar-libm')

 def test_fatal_line_classifier(self):
  for text in ('ERROR heap exhausted','panic: invalid','Assertion failed: x',
               'NEMU: timed out','timeout exceeded','FATAL failure',
               'aborted', 'RA_ERROR kind=tls', 'DIFFTEST ERROR', 'Decompress failed', 'check failed', 'failed=2',
               'failed=0x1','failed=0.5','failed=01','failed=0,','failed = 0','fail=1','failure=1'):
   with self.subTest(text=text): self.assertTrue(r.fatal_line(text))
  for text in ('RA_GROUP 0 1 2 C 3 4 4 0 2 0 1 deadbeef',
               'path /tmp/error/foo', 'error_code=0', 'no_error=0',
               'status=pass failed=0', 'RA_COMPLETE log 4 8 0'):
   with self.subTest(text=text): self.assertFalse(r.fatal_line(text))

 def test_environment_identity(self):
  ids=[dict(role='archive',sha256='a'),dict(role='gcc',sha256='g'),dict(role='nemu',sha256='n'),dict(role='elf-log',sha256='x')]
  commands=[dict(label='nemu-log',command='/tmp/nemu -b --max-instr=100 /tmp/bin',timeout_seconds='900')]
  first=r.environment_digest(['log'],ids,commands)
  ids[0]['sha256']='another';ids[3]['sha256']='another'
  self.assertEqual(first,r.environment_digest(['log'],ids,commands))
  ids[1]['sha256']='other-gcc';self.assertNotEqual(first,r.environment_digest(['log'],ids,commands))
  ids[1]['sha256']='g';commands[0]['timeout_seconds']='901'
  self.assertNotEqual(first,r.environment_digest(['log'],ids,commands))
  fixed=r.environment_digest(['log'],ids,commands,initial_budget=200)
  commands[0]['command']=commands[0]['command'].replace('=100','=155')
  self.assertEqual(fixed,r.environment_digest(['log'],ids,commands,initial_budget=200))
  self.assertNotEqual(fixed,r.environment_digest(['log'],ids,commands,initial_budget=201))

 def test_tls_symbol_table(self):
  line='  23: 0000000000000008 4 TLS GLOBAL DEFAULT 9 errno\n'
  self.assertEqual(r.errno_offset(line),8)
  for bad in ('',line+line,line.replace('TLS','OBJECT'),line.replace('errno','another')):
   with self.assertRaises(ValueError):r.errno_offset(bad)

 def test_executing_observer_identity(self):
  own=r.sha(Path(r.__file__));r.check_observer(own)
  with self.assertRaises(ValueError):r.check_observer('0'*64)
  with tempfile.TemporaryDirectory() as temp:
   fake=Path(temp)/'observer.py';fake.write_text('other code')
   with mock.patch.object(r,'__file__',str(fake)):
    with self.assertRaises(ValueError):r.check_observer(own)

 def test_distance_and_specials(self):
  self.assertEqual(r.distance(0x3f800001,0x3f800000,32),1)
  self.assertEqual(r.distance(0x80000000,0,32),1)
  self.assertIsNone(r.distance(0x7f800000,0x7f800000,32))
  self.assertEqual(r.fpclass(0x80000000,32),'neg_zero')
  self.assertEqual(r.fpclass(0x7f800001,32),'nan')
  self.assertTrue(r.gate_exceeded('pow','array',0x80000000,0,32,1))
  self.assertFalse(r.gate_exceeded('pow','array',0x7fc00001,0x7fc00002,32,1))
  self.assertTrue(r.gate_exceeded('pow','array',0x3f800000,0x7fc00002,32,1))
  self.assertFalse(r.gate_exceeded('expf','array',0x80000000,0,32,2))
  self.assertTrue(r.gate_exceeded('expf','Nx',0x80000000,0,32,2))

 def test_nx_padding_and_array_grouping(self):
  corpus=(array.array('Q',range(263)),None,array.array('Q',range(263)))
  sem=[(1,0),(2,0)]
  nx=list(r.expected_groups('exp',0,1,32,corpus,sem))
  self.assertEqual(nx[65][:5],('C',260,4,3,1))
  self.assertEqual(nx[65][5],[(260,0),(261,0),(262,0),(262,0)])
  ar=list(r.expected_groups('exp',1,1,32,corpus,sem))
  self.assertEqual([x[2] for x in ar[:4]],[1,2,3,4])
  powc=(array.array('Q',range(115500)),array.array('Q',range(115500)),array.array('Q',range(115500)))
  g=list(r.expected_groups('pow',1,1,32,powc,sem))
  around=[v for v in g if v[0]=='C' and v[1]<115422<=v[1]+v[2]]
  self.assertEqual(len(around),1);self.assertEqual(around[0][1]+around[0][2],115422)

 def test_sample_selection_keeps_two_operands_and_references(self):
  c=tuple(array.array('Q',range(base,base+11)) for base in (0,32,64))
  self.assertEqual([list(v) for v in r.subset(c,4)],
                   [[0,2,5,8],[32,34,37,40],[64,66,69,72]])
  self.assertIs(r.subset(c,11),c)
  for n in (0,r.FIXTURES['log'][1]+1):
   with self.assertRaises(ValueError):r.sample_count('log',n)


def synthetic(fn='expf',damage=None,samples=None):
 corpus=(array.array('I',[0x3f000000,0x3f800000,0x40000000]),None,array.array('I',[0x3f000000,0x3f800000,0x40000000]))
 sem=[(0,0),(0x80000000,0)];vlenb=16
 count=3 if samples is None else samples
 # Hard-code representative indices independently of the runner's subset function.
 observed=corpus if count==3 else (corpus[0][:count],None,corpus[2][:count])
 lines=[f'RA_TLS {fn} 4096 4096 4096 1 8 8',f'RA_CONFIG {fn} {vlenb} {count} 2 2'];groups=lanes=0
 for iface in range(2):
  for m in (1,2):
   for mode in range(5 if samples is None else 1):
    for kind,id_,n,emit,incoming,xs in r.expected_groups(fn,iface,m,vlenb,observed,sem):
     payload=''.join(f'{x:08x}{x:08x}' for x,z in xs)
     if damage=='input' and groups==0: payload='00000000'+payload[8:]
     after=4 if damage=='frm' and groups==0 else mode
     lines.append(f'RA_GROUP {iface} {m} {mode} {kind} {id_} {n} {emit} {incoming} {after} 0 1 {payload}')
     groups+=1;lanes+=emit
 lines.append(f'RA_COMPLETE {fn} {groups} {lanes} 0')
 if damage=='truncate': lines.pop(-3)
 if damage=='duplicate': lines.insert(3,lines[2])
 if damage=='tls': lines[0]=f'RA_TLS {fn} 4096 4096 4096 1 9 9'
 if damage=='completion': lines[-1]=f'RA_COMPLETE {fn} {groups} {lanes-1} 0'
 return '\n'.join(lines)+'\n',corpus,sem


class ParserTests(unittest.TestCase):
 def observe(self,text,corpus,sem,samples=None):
  lanes=io.StringIO();groups=io.StringIO();lw=csv.writer(lanes);gw=csv.DictWriter(groups,fieldnames=r.GROUP_FIELDS);stats=[];paired=[]
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'uart';path.write_text(text)
   with mock.patch.object(r,'oracle',return_value=corpus), mock.patch.object(r,'semantics',return_value=(Path('fake'),sem)), mock.patch.dict(r.FIXTURES,{'expf':('x',3,2,'fake')}):
    result=r.analyze_function('expf',path,Path(temp),lw,gw,stats,paired,8,samples=samples)
  return result,list(csv.reader(io.StringIO(lanes.getvalue()))),stats,paired

 def test_sampled_rne_coverage_and_mismatch(self):
  text,corpus,sem=synthetic(samples=2)
  result,rows,stats,paired=self.observe(text,corpus,sem,samples=2)
  self.assertEqual((result[0],result[-1],len(rows)),(16,208,208))
  self.assertEqual(len(stats),4);self.assertEqual(len(paired),0)
  corpus_rows=[row for row in rows if row[3].startswith('C')]
  self.assertEqual(len(corpus_rows),8)
  self.assertEqual({row[4] for row in rows},{'0'})
  self.assertEqual({(row[3],row[5]) for row in corpus_rows},{('C0','3f000000'),('C1','3f800000')})
  for asked,observed,damage in ((None,2,None),(2,3,None),(3,None,None),
                                (2,2,'frm'),(2,2,'input'),(2,2,'truncate')):
   with self.subTest(asked=asked,observed=observed,damage=damage):
    damaged,_,_=synthetic(damage=damage,samples=observed)
    with self.assertRaises(ValueError): self.observe(damaged,corpus,sem,samples=asked)

 def test_valid_and_five_contiguous(self):
  text,corpus,sem=synthetic();result,rows,stats,paired=self.observe(text,corpus,sem)
  self.assertEqual(result[0],16);self.assertEqual(len(stats),20);self.assertEqual(len(paired),16)
  keys=[]
  for i in range(0,len(rows),5):
   group=rows[i:i+5];self.assertEqual([x[4] for x in group],list(map(str,range(5))))
   self.assertEqual(len({tuple(x[j] for j in (0,1,2,3,5,6,8)) for x in group}),1)
   keys.append(tuple(group[0][:4]))
  self.assertEqual(len(set(keys)),len(keys))
  self.assertEqual(stats[0]['n'],3);self.assertEqual(stats[0]['ulp0'],3)
  self.assertEqual(stats[0]['historic_predicate_exceeded'],0)

 def test_corruption_rejected(self):
  for damage in ('input','frm','truncate','duplicate','completion','tls'):
   with self.subTest(damage=damage):
    text,corpus,sem=synthetic(damage=damage)
    with self.assertRaises(ValueError): self.observe(text,corpus,sem)

 def test_group_framing(self):
  text,corpus,sem=synthetic();lines=text.splitlines();lines[2]=lines[2][:-1]
  with self.assertRaises(ValueError):self.observe('\n'.join(lines)+'\n',corpus,sem)

 def test_crlf_and_missing_final_lf(self):
  text,corpus,sem=synthetic(); crlf=text.replace('\n','\r\n')
  self.assertEqual(self.observe(crlf,corpus,sem)[0],self.observe(text,corpus,sem)[0])
  for damaged in (text[:-1],crlf[:-1],text.replace('\n','\r\r\n',1)):
   with self.assertRaises(ValueError):self.observe(damaged,corpus,sem)

 def test_pow_binary_64bit_and_duplicate_padding(self):
  spec=('C',4,2,1,1,[(0x8000000000000000,0x3ff0000000000000)]*2)
  one='80000000000000003ff00000000000007ff8000000000001'
  parts=('RA_GROUP 0 1 3 C 4 2 1 1 3 34 17 '+one+one).split()
  row,ys=r.parse_group(parts,'pow',0,1,3,spec,None,64)
  self.assertEqual(ys,[0x7ff8000000000001]);self.assertEqual(row['flags'],17);self.assertEqual(row['errno'],34)
  parts[-1]=one+'0'+one[1:]
  with self.assertRaises(ValueError):r.parse_group(parts,'pow',0,1,3,spec,None,64)

 def test_sampled_atan2_uses_real_nx_scopes_and_second_operand(self):
  fn='atan2';one=0x3ff0000000000000; sign=1<<63
  corpus=tuple(array.array('Q',v) for v in ((one,sign,2),(sign,one,3),(4,5,6)))
  sem=[(sign,one),(one,sign)];vlenb=16
  lines=[f'RA_TLS {fn} 4096 4096 4096 1 8 8',f'RA_CONFIG {fn} {vlenb} 2 2 0']
  groups=lanes=0
  for m in (1,2):
   for kind,gid,n,emit,incoming,xs in r.expected_groups(fn,0,m,vlenb,r.subset(corpus,2),sem):
    payload=''.join(f'{x:016x}{z:016x}{x:016x}' for x,z in xs)
    lines.append(f'RA_GROUP 0 {m} 0 {kind} {gid} {n} {emit} {incoming} 0 0 1 {payload}')
    groups+=1;lanes+=emit
  lines.append(f'RA_COMPLETE {fn} {groups} {lanes} 0')
  with tempfile.TemporaryDirectory() as temp:
   uart=Path(temp)/'uart';uart.write_text('\n'.join(lines)+'\n')
   stream=io.StringIO(newline='');stat=[]
   def analyze(nx_only):
    return r.analyze_function(fn,uart,Path(temp),csv.writer(stream),
        csv.DictWriter(io.StringIO(),fieldnames=r.GROUP_FIELDS),stat,[],8,samples=2,nx_only=nx_only)
   with mock.patch.object(r,'oracle',return_value=corpus), mock.patch.object(r,'semantics',return_value=(Path('fake'),sem)), mock.patch.dict(r.FIXTURES,{fn:('x',3,0,'fake')}):
    self.assertEqual(analyze(True),(16,groups,lanes))
    got=list(csv.reader(io.StringIO(stream.getvalue())))
    self.assertEqual({(a[1],a[2],a[4]) for a in got},{('Nx','1','0'),('Nx','2','0')})
    self.assertEqual(len(got),lanes)
    self.assertEqual({(a[5],a[6]) for a in got if a[3].startswith('C')},
                     {(f'{one:016x}',f'{sign:016x}'),(f'{sign:016x}',f'{one:016x}')})
    with self.assertRaises(ValueError):analyze(False)
    lines[2]=lines[2].replace(f'{one:016x}{sign:016x}',f'{sign:016x}{one:016x}')
    uart.write_text('\n'.join(lines)+'\n')
    with self.assertRaisesRegex(ValueError,'输入位不符'):analyze(True)

 def test_snapshot_identity(self):
  with tempfile.TemporaryDirectory() as temp:
   rd=Path(temp);p=rd/'snapshot'/'source'/'probe.c';p.parent.mkdir(parents=True);p.write_text('recorded')
   row=dict(role='source',path=str(rd/'missing'),snapshot=str(p),sha256=r.sha(p))
   self.assertEqual(r.identity_path(row,rd),p)
   p.write_text('tampered')
   with self.assertRaises(ValueError):r.identity_path(row,rd)
   row['snapshot']=str(rd/'outside')
   with self.assertRaises(ValueError):r.identity_path(row,rd)

 def test_comparison_identity_and_all_analysis_artifacts(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)/'20261114-184600-rounding-comparison';root.mkdir()
   observer=r.sha(Path(r.__file__))
   def make(name,role,archive,y):
    d=root/name;d.mkdir()
    with gzip.open(d/'lanes.csv.gz','wt',encoding='ascii',newline='') as f:
     w=csv.writer(f);w.writerow(r.LANE_FIELDS)
     w.writerow(['expf','array','1','C0','0','3f800000','',y,'3f800000','0'])
    for filename in ('groups.csv.gz','summary.csv','paired.csv','coverage.csv','execution.csv'):(d/filename).write_bytes(filename.encode())
    values=dict(run_role=role,archive_sha256=archive,functions='expf',environment_sha256='e'*64,observer_sha256=observer)
    for key,filename in (('lanes','lanes.csv.gz'),('groups','groups.csv.gz'),('summary','summary.csv'),('paired','paired.csv'),('coverage','coverage.csv'),('execution','execution.csv')):
     values[key+'_sha256']=r.sha(d/filename)
    r.csv_write(d/'COMPLETE.csv',('role','value'),[dict(role=k,value=v) for k,v in values.items()])
    return d,values
   base,a=make('base','baseline','a'*64,'3f800000')
   candidate,b=make('candidate','candidate','b'*64,'3f800001')
   def args(name):return SimpleNamespace(baseline=base,candidate=candidate,output=base/name)
   r.compare(args('comparison.csv'));self.assertTrue((base/'comparison.csv').is_file())
   for name in ('lanes.csv.gz','groups.csv.gz','summary.csv','paired.csv','coverage.csv','execution.csv'):
    path=base/name;original=path.read_bytes();path.write_bytes(original+b'changed')
    with self.subTest(name=name),self.assertRaises(ValueError):r.identity_map(base)
    path.write_bytes(original)
   fake=root/'different-observer.py';fake.write_text('different script')
   with mock.patch.object(r,'__file__',str(fake)),self.assertRaises(ValueError):r.compare(args('wrong-executable.csv'))
   b['observer_sha256']='0'*64
   changed=io.StringIO();writer=csv.DictWriter(changed,fieldnames=('role','value'));writer.writeheader()
   writer.writerows(dict(role=k,value=v) for k,v in b.items());(candidate/'COMPLETE.csv').write_text(changed.getvalue())
   with self.assertRaises(ValueError):r.compare(args('wrong-run-observer.csv'))

 def test_missing_or_bad_good_trap(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp);(p/'logs').mkdir();(p/'tmp').mkdir()
   archive=p/'a';archive.write_bytes(b'a');binary=p/'b';binary.write_bytes(b'b');nemu=p/'n';nemu.write_bytes(b'n');elf=p/'e';elf.write_bytes(b'e')
   out=p/'logs/nemu-expf.stdout';out.write_text('pretend HIT GOOD TRAP')
   err=p/'logs/nemu-expf.stderr';err.write_text('')
   ids=[dict(role=k,path=str(path),sha256=r.sha(path)) for k,path in [('archive',archive),('binary-expf',binary),('staged-expf',binary),('nemu',nemu),('elf-expf',elf)]]
   commands=[dict(label='nemu-expf',status='0',command=f'{nemu} -b --max-instr=100 {binary}',cwd=str(p),stdout=str(out),stderr=str(err),tmpdir=str(p/'tmp/nemu-expf'),wall_ns='1234000')]
   def save_digests():
    for name,path in (('stdout',out),('stderr',err)):
     commands[0][name+'_bytes']=str(path.stat().st_size);commands[0][name+'_sha256']=r.sha(path)
   def status(cap=100): return r.clean_status(p,'expf',ids,commands,cap)
   good='nemu: HIT GOOD TRAP at pc = 0x80000000\nhost time spent = 1,220 us\ntotal guest instructions = 99\n'
   for text in ('pretend HIT GOOD TRAP',good.splitlines()[0]+'\n',good+good,
                'ERROR heap exhausted\n'+good,good.replace('99','100'),
                good.replace('99','101'),good.replace('99','0'),
                good.replace('99','9,9'),good+'total guest instructions = 8\n'):
    with self.subTest(text=text):
     out.write_text(text);save_digests()
     with self.assertRaises(ValueError):status()
   out.write_text(good);save_digests()
   self.assertEqual(status(),(err,dict(function='expf',max_instr=100,guest_instructions=99,
                                      nemu_host_us=1220,wall_ns=1234000,uart_bytes=0)))
   with self.assertRaises(ValueError):status(101)
   with self.assertRaises(ValueError):status(r.MAX_INSTR+1)
   commands[0]['wall_ns']='0'
   with self.assertRaises(ValueError):status()
   commands[0]['wall_ns']='1234000'
   err.write_text('Assertion failed: test\n')
   with self.assertRaises(ValueError):status()
   with self.assertRaises(ValueError):list(r.records(err))
   save_digests();commands[0]['status']='1'
   with self.assertRaises(ValueError):status()


if __name__=='__main__':unittest.main()
