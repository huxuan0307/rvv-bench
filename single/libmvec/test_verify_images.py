"""Check actual call relocations and reject mismatched interfaces."""
import unittest
import verify_images


def dis(*symbols):
    return '\n'.join('  0: R_RISCV_CALL_PLT '+symbol for symbol in symbols)


class CallAuditTests(unittest.TestCase):
    def audit(self, function, mode, interface, symbols, recorded=None):
        return verify_images.audit_calls(dis(*symbols), function, mode, interface, recorded)[0]

    def test_array_direct_and_wrong_interfaces(self):
        for f in ('log', 'pow', 'logf'):
            for n in ('1', '2'):
                mode='direct_m'+n; array=f+'vec_m'+n
                nx='_ZGV'+n+'Nx'+('vv' if f=='pow' else 'v')+'_'+f
                self.assertTrue(self.audit(f, mode, 'array', [array], [array]))
                self.assertFalse(self.audit(f, mode, 'array', [nx], [nx]))
                self.assertFalse(self.audit(f, mode, 'nx', [array], [array]))
                self.assertFalse(self.audit(f, mode, 'array', [array, nx], [array, nx]))
                self.assertFalse(self.audit(f, mode, 'array', [array], [nx]))

    def test_auto_and_scalar(self):
        self.assertTrue(self.audit('log', 'auto_m2', 'array', ['_ZGV2Nxv_log', '_ZGV1Nxv_log']))
        self.assertFalse(self.audit('log', 'auto_m1', 'array', ['logvec_m1']))
        self.assertTrue(self.audit('log', 'scalar_fast', 'array', ['log']))
        self.assertFalse(self.audit('log', 'scalar_fast', 'array', ['log', 'logvec_m1']))
        self.assertFalse(self.audit('log', 'scalar_fast', 'array', ['_ZGV1Nxv_log']))


if __name__=='__main__': unittest.main()
