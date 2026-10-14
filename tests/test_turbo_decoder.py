import ctypes
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from turbo_decoder import TurboDecoder,dematch_llr,rate_match_indices,crc24a,K,D,E,DEFAULT_LIBRARY

# Independent scalar encoder following TS36.212 5.1.3.2, QPP(43,88).
# Its output is compared with an external C encoder, not just our decoder.
def oracle_encode(payload):
    bits=np.unpackbits(np.frombuffer(payload,dtype=np.uint8))
    def constituent(seq):
        a=b=c=0;parity=[]
        for u in seq:
            feedback=int(u)^b^c
            parity.append(feedback^a^c)
            a,b,c=feedback,a,b
        tail=[]
        for _ in range(3):
            u=b^c
            tail.extend([u,a^c])
            a,b,c=0,a,b
        return parity,tail
    parity1,tail1=constituent(bits)
    indices=(43*np.arange(K)+88*np.arange(K)**2)%K
    parity2,tail2=constituent(bits[indices])
    tails=tail1+tail2
    streams=np.zeros((3,D),dtype=np.uint8)
    streams[0,:K]=bits;streams[1,:K]=parity1;streams[2,:K]=parity2
    streams[:,K:]=np.asarray(tails).reshape(4,3).T
    return streams

def test_payload():
    data=bytes(np.random.default_rng(117).integers(0,256,173,dtype=np.uint8))
    return data+crc24a(data).to_bytes(3,'big')

class TurboTests(unittest.TestCase):
    def test_crc_standard_check(self):
        self.assertEqual(crc24a(b'123456789'),0xcde703)
        self.assertEqual(crc24a(test_payload()),0)
    def test_all_indices_and_repetition(self):
        indices=rate_match_indices()
        self.assertEqual(indices.shape,(E,))
        counts=np.bincount(indices,minlength=3*D)
        self.assertTrue((counts>0).all())
        self.assertEqual(counts.sum(),E)
        self.assertGreater(counts.max(),1)
        sums=dematch_llr(np.ones(E)).ravel()
        np.testing.assert_array_equal(sums,counts)
    def test_invalid_llr(self):
        for value in [np.ones(7199),np.full(E,np.nan),np.ones((3,2400))]:
            with self.assertRaises(ValueError):dematch_llr(value)
    @unittest.skipUnless(DEFAULT_LIBRARY.is_file(),'optional native backend not built')
    def test_independent_encoder_against_external_encoder(self):
        lib=ctypes.CDLL(str(DEFAULT_LIBRARY))
        class Code(ctypes.Structure):
            _fields_=[('n',ctypes.c_int),('k',ctypes.c_int),('length',ctypes.c_int),('rgen',ctypes.c_uint),('gen',ctypes.c_uint)]
        payload=test_payload();bits=np.unpackbits(np.frombuffer(payload,dtype=np.uint8)).copy()
        expected=oracle_encode(payload);actual=np.zeros((3,D),dtype=np.uint8)
        ptr=ctypes.POINTER(ctypes.c_uint8)
        lib.lte_turbo_encode.argtypes=[ctypes.POINTER(Code),ptr,ptr,ptr,ptr]
        status=lib.lte_turbo_encode(ctypes.byref(Code(2,4,K,0o13,0o15)),bits.ctypes.data_as(ptr),*[s.ctypes.data_as(ptr) for s in actual])
        self.assertEqual(status,3*D)
        np.testing.assert_array_equal(actual,expected)
    @unittest.skipUnless(DEFAULT_LIBRARY.is_file(),'optional native backend not built')
    def test_rate_match_against_external_forward(self):
        lib=ctypes.CDLL(str(DEFAULT_LIBRARY))
        p=ctypes.POINTER(ctypes.c_int8)
        class IO(ctypes.Structure):
            _fields_=[('D',ctypes.c_int),('E',ctypes.c_int),('d',p*3),('e',p)]
        lib.lte_rate_matcher_alloc.restype=ctypes.c_void_p
        lib.lte_rate_matcher_free.argtypes=[ctypes.c_void_p]
        lib.lte_rate_match_fw.argtypes=[ctypes.c_void_p,ctypes.POINTER(IO),ctypes.c_int]
        lib.lte_rate_match_fw.restype=ctypes.c_int
        streams=np.ascontiguousarray(oracle_encode(test_payload()),dtype=np.int8)
        actual=np.empty(E,dtype=np.int8)
        io=IO(D,E,(p*3)(*[row.ctypes.data_as(p) for row in streams]),actual.ctypes.data_as(p))
        context=lib.lte_rate_matcher_alloc()
        try:status=lib.lte_rate_match_fw(context,ctypes.byref(io),0)
        finally:lib.lte_rate_matcher_free(context)
        self.assertEqual(status,0)
        np.testing.assert_array_equal(actual,streams.ravel()[rate_match_indices()])
    @unittest.skipUnless(DEFAULT_LIBRARY.is_file(),'optional native backend not built')
    def test_clean_and_corrupted_systematic(self):
        payload=test_payload();streams=oracle_encode(payload)
        transmitted=streams.ravel()[rate_match_indices()]
        llr=12*(1-2*transmitted.astype(float))
        decoder=TurboDecoder()
        clean=decoder.decode(llr)
        self.assertEqual(clean['payload'],payload)
        self.assertTrue(clean['crc24_valid'])
        # Erase the systematic observations; parity remains independently
        # generated. This cannot pass by merely slicing systematic bits.
        llr[rate_match_indices()<D]=0
        decoded=decoder.decode(llr)
        self.assertEqual(decoded['payload'],payload)
        self.assertTrue(decoded['crc24_valid'])
    @unittest.skipUnless(DEFAULT_LIBRARY.is_file(),'optional native backend not built')
    def test_noise_integrity_and_zero_input(self):
        decoder=TurboDecoder()
        self.assertIsNone(decoder.decode(np.zeros(E))['payload'])
        result=decoder.decode(np.random.default_rng(72).normal(size=E))
        self.assertFalse(result['crc24_valid'])
    def test_missing_backend(self):
        with self.assertRaises(RuntimeError):TurboDecoder('/does/not/exist')

if __name__=='__main__':unittest.main()
