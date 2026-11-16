#!/usr/bin/env python3
"""主机构建规则单测，不使用目标程序或仿真器。"""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('math_build', Path(__file__).with_name('build.py'))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildTests(unittest.TestCase):
    def test_selection_and_shared_trig(self):
        functions = build.select_functions('log,exp,expf,logf,pow,sin,cos')
        self.assertEqual(len(functions), 7)
        units = build.select_units(functions)
        self.assertEqual(units.count('trigvec_data'), 1)
        self.assertEqual(len(units), 12)
        self.assertEqual(build.select_units(['sin']), ['sinvec', 'trigvec_data'])
        self.assertEqual(build.select_units(['cos']), ['cosvec', 'trigvec_data'])
        for invalid in ('', 'log,log', 'log,unknown', 'log,', ' log', 'sin,cos,sin'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build.select_functions(invalid)

    def test_six_function_variant_selection(self):
        for function in ('exp', 'expf', 'logf', 'pow', 'sin', 'cos'):
            for variant in ('baseline', 'cold', 'lifetime'):
                self.assertEqual(build.select_variants([function], variant), {function: variant})
            with self.assertRaises(ValueError):
                build.select_variants([function], 'lifetime-hybrid')
        with self.assertRaises(ValueError):
            build.select_variants(['exp'], 'cold', lto=True)
        self.assertEqual(build.select_variants(['sin', 'cos'], 'cold', focus='sin'),
                         {'sin': 'cold', 'cos': 'baseline'})
        with self.assertRaises(ValueError):
            build.select_variants(['log', 'exp'], 'cold')
        with self.assertRaises(ValueError):
            build.select_variants(['sin', 'cos'], 'cold', focus='sin', lto=True)

    def test_parse_selection(self):
        functions = build.select_functions('log,exp,expf,logf,pow,sin,cos')
        spec = ('log=lifetime-hybrid,exp=cold,expf=lifetime,logf=lifetime,'
                'pow=lifetime,sin=lifetime+uniform,cos=lifetime')
        variants = build.parse_selection(spec, functions)
        self.assertEqual(variants['log'], 'lifetime-hybrid')
        self.assertEqual(variants['sin'], 'lifetime+uniform')
        for bad in ('log=cold', 'log=cold,log=lifetime',
                    'log=cold,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=cold,exp=cold',
                    'log=cold,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=uniform',
                    'log=cold,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=cold+uniform',
                    'log=cold,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=cold+fast',
                    'log=lifetime-hybrid,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=cold,unknown=cold',
                    'log=cold,exp=cold,expf=cold,logf=cold,pow=cold,sin=cold,cos=cold,'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build.parse_selection(bad, functions)
        with self.assertRaises(ValueError):
            build.parse_selection('log=baseline,exp=cold', ['log', 'exp'], lto=True)
        self.assertEqual(build.parse_selection('sin=baseline+uniform', ['sin']),
                         {'sin': 'baseline+uniform'})

    def test_cli_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, prefix = root / 'source', root / 'prefix'
            source.mkdir()
            prefix.mkdir()
            output = root / 'must-not-exist'
            common = ['--prefix', str(prefix), '--source', str(source), '--output', str(output)]
            for extra in (['--functions', 'log,exp', '--variant', 'cold'],
                          ['--functions', 'exp', '--variant', 'lifetime-hybrid'],
                          ['--functions', 'log,exp', '--variant', 'lifetime-hybrid'],
                          ['--functions', 'cos,cos', '--variant', 'baseline'],
                          ['--variant', 'baseline'],
                          ['--select', 'log=cold'],
                          ['--functions', 'sin,cos', '--select', 'sin=lifetime+uniform',
                           '--focus', 'sin'],
                          ['--functions', 'exp', '--select', 'exp=lifetime', '--lto'],
                          ['--functions', 'log,exp', '--variant', 'baseline',
                           '--select', 'log=cold,exp=cold']):
                with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        build.main(common + extra)
                    self.assertFalse(output.exists())
            # A valid selection still fails at the empty source closure, before output.
            with self.subTest(extra='valid-select'), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    build.main(common + ['--functions', 'log,exp',
                                         '--select', 'log=cold,exp=lifetime'])
                self.assertFalse(output.exists())

    def test_flags_and_macro_controls(self):
        expected_flags = {
            'baseline': ['-DLOGVEC_BASELINE=1'],
            'cold': [],
            'lifetime': ['-DLOGVEC_HOT_LIFETIME=1'],
            'lifetime-hybrid': ['-DLOGVEC_HOT_LIFETIME=1', '-DLOGVEC_ARRAY_M1_BASELINE=1'],
        }
        self.assertEqual(build.VARIANT_FLAGS, expected_flags)
        for unit in ('logvec', 'logvec_data'):
            for variant, flags in expected_flags.items():
                for lto, mode in ((False, '-fno-lto'), (True, '-flto')):
                    with self.subTest(unit=unit, variant=variant, lto=lto):
                        self.assertEqual(build.unit_flags(unit, variant, lto),
                                         build.BASE + [mode] + flags + ['-ffp-contract=fast'])
                        macros = ''.join(f'#define {f[2:-2]} 1\n' for f in flags)
                        build.check_macros(macros, unit, variant)
        other_units = set(build.select_units(list(build.FUNCTION_UNITS))) - set(build.FUNCTION_UNITS['log'])
        kernels = {'expvec': 'exp', 'expfvec': 'expf', 'logfvec': 'logf',
                   'powvec': 'pow', 'sinvec': 'sin', 'cosvec': 'cos',
                   'sinfvec': 'sinf', 'cosfvec': 'cosf'}
        for unit in other_units:
            if unit.endswith('vec_data'):
                expected = {'baseline': [], 'cold': [], 'lifetime': []}
            else:
                hot = build.SIXMATH_EXPF_HOT if kernels[unit] == 'expf' else build.SIXMATH_HOT
                expected = {'baseline': [], 'cold': [build.SIXMATH_COLD],
                            'lifetime': [build.SIXMATH_COLD, hot]}
            for variant, flags in expected.items():
                with self.subTest(unit=unit, variant=variant):
                    self.assertEqual(build.unit_flags(unit, variant, False),
                                     build.BASE + ['-fno-lto'] + flags + ['-ffp-contract=fast'])
                    good = ''.join(f'#define {f[2:-2]} 1\n' for f in flags)
                    build.check_macros(good, unit, variant)
                    for name in ((build.LOG_MACROS | build.INTERNAL_LOG_MACROS | {
                        'SIXMATH_COLD_DISPATCH', 'SIXMATH_HOT_LIFETIME',
                        'SIXMATH_EXPF_HOT_LIFETIME', 'SIXMATH_TRIG_UNIFORM'
                    }) - set(f[2:].split('=', 1)[0] for f in flags)):
                        for value in ('0', '1'):
                            with self.assertRaises(ValueError):
                                build.check_macros(f'#define {name} {value}\n', unit, variant)
        # Uniform is an independent sin-only factor, never a data-unit macro.
        uniform = build.unit_flags('sinvec', 'lifetime+uniform', False)
        self.assertEqual(uniform, build.BASE + ['-fno-lto', build.SIXMATH_COLD,
                                                build.SIXMATH_HOT,
                                                build.SIXMATH_TRIG_UNIFORM,
                                                '-ffp-contract=fast'])
        build.check_macros('#define SIXMATH_COLD_DISPATCH 1\n'
                           '#define SIXMATH_HOT_LIFETIME 1\n'
                           '#define SIXMATH_TRIG_UNIFORM 1\n',
                           'sinvec', 'lifetime+uniform')
        self.assertEqual(build.unit_flags('sinvec', 'baseline+uniform', False),
                         build.BASE + ['-fno-lto', build.SIXMATH_TRIG_UNIFORM,
                                       '-ffp-contract=fast'])
        for unit, variant in (('cosvec', 'lifetime+uniform'),
                              ('sinvec', 'baseline+uniform+uniform')):
            with self.subTest(unit=unit, variant=variant), self.assertRaises(ValueError):
                build.unit_experiment_flags(unit, variant)
        with self.assertRaises(ValueError):
            build.check_macros('#define SIXMATH_COLD_DISPATCH 1\n'
                               '#define SIXMATH_HOT_LIFETIME 1\n'
                               '#define SIXMATH_TRIG_UNIFORM 1\n',
                               'sinvec', 'lifetime')
        hybrid = '#define LOGVEC_HOT_LIFETIME 1\n#define LOGVEC_ARRAY_M1_BASELINE 1\n'
        bad_hybrid = ('', '#define LOGVEC_HOT_LIFETIME 1\n', '#define LOGVEC_ARRAY_M1_BASELINE 1\n',
                      '#define LOGVEC_HOT_LIFETIME 0\n#define LOGVEC_ARRAY_M1_BASELINE 1\n',
                      '#define LOGVEC_HOT_LIFETIME 1\n#define LOGVEC_ARRAY_M1_BASELINE 0\n',
                      hybrid + '#define LOGVEC_BASELINE 1\n',
                      hybrid + '#define LOGVEC_M1_ARRAY_FULL 1\n',
                      hybrid + '#define LOGVEC_M1_ARRAY_CORE 0\n',
                      hybrid + '#define SIXMATH_TRIG_UNIFORM 0\n')
        for unit in ('logvec', 'logvec_data'):
            for bad in bad_hybrid:
                with self.subTest(unit=unit, bad=bad), self.assertRaises(ValueError):
                    build.check_macros(bad, unit, 'lifetime-hybrid')
            for variant in ('baseline', 'cold', 'lifetime'):
                good = ''.join(f'#define {f[2:-2]} 1\n' for f in expected_flags[variant])
                for name in ('LOGVEC_ARRAY_M1_BASELINE', 'LOGVEC_M1_ARRAY_FULL',
                             'LOGVEC_M1_ARRAY_CORE', 'SIXMATH_TRIG_UNIFORM'):
                    for value in ('0', '1'):
                        with self.subTest(unit=unit, variant=variant, name=name, value=value):
                            with self.assertRaises(ValueError):
                                build.check_macros(good + f'#define {name} {value}\n', unit, variant)
            with self.assertRaises(ValueError):
                build.check_macros('#define LOGVEC_BASELINE 0\n', unit, 'baseline')

    def test_local_source_closure_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, snapshot = base / 'source', base / 'snapshot'
            source.mkdir()
            snapshot.mkdir()
            files = {'x.c': '#include "a.h"\n', 'a.h': '#include "b.h"\n', 'b.h': '/* b */\n'}
            for name, contents in files.items():
                (source / name).write_text(contents)
                (snapshot / name).write_text(contents)
            self.assertEqual(build.local_closure(source, ['x']), ['a.h', 'b.h', 'x.c'])
            hashes = {name: build.sha(source / name) for name in files}
            build.verify_snapshot(source, snapshot, hashes)
            (snapshot / 'b.h').write_text('changed\n')
            with self.assertRaises(RuntimeError):
                build.verify_snapshot(source, snapshot, hashes)
            (snapshot / 'b.h').write_text(files['b.h'])
            (source / 'b.h').unlink()
            with self.assertRaises(ValueError):
                build.local_closure(source, ['x'])

    def test_dependency_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / 'source.c', root / 'file with space.h', root / 'last.h']
            for p in paths:
                p.write_text('')
            dep = f'target.o: {paths[0]} {str(paths[1]).replace(" ", chr(92) + " ")} \\\n {paths[2]}\n'
            self.assertEqual(build.dependency_paths(dep, root), sorted(paths, key=str))
            for bad in ('invalid', 'x:', 'x: missing-file'):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    build.dependency_paths(bad, root)

    def test_symbols_and_binary_pow(self):
        functions = ['log', 'pow', 'sin', 'cos']
        symbols = build.expected_symbols(functions)
        self.assertEqual(len(symbols), 16)
        self.assertIn('_ZGV2Nxvv_pow', symbols)
        text = '\n'.join(f'00000000 T {s}' for s in symbols)
        build.verify_symbols(text, functions)
        with self.assertRaises(ValueError):
            build.verify_symbols(text.replace(' T _ZGV2Nxvv_pow', ' U _ZGV2Nxvv_pow'), functions)
        with self.assertRaises(ValueError):
            build.verify_symbols(text + '\n00000000 T _ZGV2Nxvv_pow', functions)

    def test_source_tree_contains_complete_graph(self):
        source = Path(__file__).resolve().parent
        files = build.local_closure(source, build.select_units(list(build.FUNCTION_UNITS)))
        for name in ('newlib.h', 'unary_entries.inc.h', 'binary_entries.inc.h',
                     'powvec_data.h', 'scalar_tables.h', 'trigvec_data.h',
                     'trigvec_impl.h', 'logvec_impl.inc.h'):
            self.assertIn(name, files)
        self.assertEqual(files.count('trigvec_data.c'), 1)
        log_files = build.local_closure(source, build.select_units(['log']))
        for name in ('logvec.c', 'logvec_data.c', 'logvec.h', 'logvec_data.h',
                     'logvec_impl.inc.h', 'newlib.h', 'unary_entries.inc.h'):
            self.assertIn(name, log_files)


if __name__ == '__main__':
    unittest.main(verbosity=2)
