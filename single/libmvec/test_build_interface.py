"""Regression tests for direct-interface selection and archive call names."""
import unittest
import build


class DirectInterfaceTests(unittest.TestCase):
    def test_symbols_all_performance_functions(self):
        for function in build.FUNCS:
            for mode in ('direct_m1', 'direct_m2'):
                with self.subTest(function=function, mode=mode):
                    self.assertEqual(build.direct_symbol(function, mode, 'array'),
                                     function+'vec_'+mode[-2:])
                    kind = 'vv' if function == 'pow' else 'v'
                    self.assertEqual(build.direct_symbol(function, mode, 'nx'),
                                     '_ZGV'+mode[-1]+'Nx'+kind+'_'+function)


if __name__ == '__main__':
    unittest.main()
