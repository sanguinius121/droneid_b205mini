import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
import numpy as np
from qpsk import get_symbol_bits
from goldgen import gold
from qpsk_soft import descrambled_llr,SoftTurboQPSKDecoder

class SoftQPSKTests(unittest.TestCase):
 def test_all_rotations_match_hard_serialization_and_gold(self):
  rng=np.random.default_rng(912)
  symbols=rng.normal(size=(7,601))+1j*rng.normal(size=(7,601))
  for phase in range(4):
   carriers=np.delete(symbols[1:],300,axis=1).ravel()
   pairs=[get_symbol_bits(s,phase) for s in carriers]
   hard=np.array([[p&1,(p>>1)&1] for p in pairs],dtype=np.uint8).ravel()
   expected=hard^np.asarray(gold(1600,7200,0x12345678),dtype=np.uint8)
   np.testing.assert_array_equal(descrambled_llr(symbols,phase)<0,expected)
 def test_legacy_rows_equal_skipped_first_nonlegacy_row(self):
  x=np.ones((7,601),dtype=complex)*(1+2j)
  np.testing.assert_array_equal(descrambled_llr(x,0),descrambled_llr(x[1:],0))
 def test_dc_does_not_affect_soft_observations(self):
  x=np.ones((7,601),dtype=complex)*(1+2j);baseline=descrambled_llr(x,2)
  x[:,300]=1e20-2e20j
  np.testing.assert_array_equal(baseline,descrambled_llr(x,2))
 def test_invalid_shapes_nonfinite_and_phase(self):
  for x in [np.ones((7,600),complex),np.ones((7,601)),np.full((7,601),np.nan+1j)]:
   with self.assertRaises(ValueError):descrambled_llr(x,0)
  for phase in [-1,4,True]:
   with self.assertRaises(ValueError):descrambled_llr(np.ones((7,601),complex),phase)
 def test_crc24_failure_rejected_before_inner_parser(self):
  class Backend:
   def decode(self,llr):return dict(payload=b'bad',crc24_valid=False,crc24_residue=2)
  d=SoftTurboQPSKDecoder(np.ones((7,601),complex),turbo=Backend())
  d.raw_data_to_symbol_bits(1)
  with self.assertRaisesRegex(ValueError,'CRC24A'):d.magic()
  self.assertEqual(d.turbo_attempts[0]['phase'],1)

if __name__=='__main__':unittest.main()
