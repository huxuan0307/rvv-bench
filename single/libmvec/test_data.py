import ctypes.util
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from generate_data import bits, rounded_input, generate, FUNCS, LIMITS

class DataTests(unittest.TestCase):
    def test_bits_exact(self):
        self.assertEqual(bits(1.0,False),0x3ff0000000000000)
        self.assertEqual(bits(-0.0,True),0x80000000)
        self.assertEqual(rounded_input(1+2**-30,True),1.0)
    def test_limits_are_release_controls(self):
        self.assertEqual(LIMITS,dict(log=1,exp=1,sin=1,cos=1,pow=1,logf=3,expf=2))
    @unittest.skipUnless(ctypes.util.find_library('mpfr'),'host MPFR unavailable')
    def test_oracle_is_deterministic_and_interval_checked(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'a',Path(d)/'b'; first=generate(a); second=generate(b)
            self.assertEqual(first,second)
            self.assertEqual(first['count_per_function'],1024)
            for f in FUNCS:
                self.assertEqual((a/(f+'.h')).read_bytes(),(b/(f+'.h')).read_bytes())
                self.assertEqual(first['functions'][f]['interval_agreement'],1024)
                text=(a/(f+'.h')).read_text()
                self.assertEqual(len(re.findall(r'0x[0-9a-f]+',text)),3072)
            with self.assertRaises(FileExistsError): generate(a)
    def test_input_roundtrip(self):
        for single in (False,True):
            for x in (0.125,1.0,8.0,-32.0,32.0):
                b=bits(x,single)
                got=struct.unpack('<f' if single else '<d',struct.pack('<I' if single else '<Q',b))[0]
                self.assertEqual(got,x)
if __name__=='__main__': unittest.main()
