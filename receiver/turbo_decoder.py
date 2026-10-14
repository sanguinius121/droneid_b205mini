"""Experimental DroneID LTE Turbo path; LLR > 0 means bit zero.

K=1408, D=1412, E=7200, RV=0.  Integrity is CRC24A of all 176
bytes, independent from the DroneID inner CRC16. Native optional backend:
ttsou/turbofec (GPL-3.0-or-later); no dependency is installed globally.
"""
import ctypes
from functools import lru_cache
from pathlib import Path
import numpy as np

K, D, E = 1408, 1412, 7200
PERM = np.array([0,16,8,24,4,20,12,28,2,18,10,26,6,22,14,30,1,17,9,25,5,21,13,29,3,19,11,27,7,23,15,31])
DEFAULT_LIBRARY = Path(__file__).resolve().parents[1]/'native/turbofec/libturbofec.so'

@lru_cache(maxsize=1)
def rate_match_indices():
    """TS 36.212 5.1.4.1: indices into stream-major d0,d1,d2.

    NULL entries never consume an input bit. Repeated observations combine
    by addition; the circular-buffer shift is RV0 k0=2*45, not offset4148.
    """
    rows=45; width=rows*32; dummy=width-D
    padded=np.arange(width)-dummy
    padded[padded<0]=-1
    first=padded.reshape(rows,32)[:,PERM].T.ravel()
    pos=np.arange(width)
    third=padded[(PERM[pos//rows]+32*(pos%rows)+1)%width]
    second=np.where(first>=0,first+D,-1)
    third=np.where(third>=0,third+2*D,-1)
    circular=np.concatenate([first,np.column_stack([second,third]).ravel()])
    shifted=np.roll(circular,-2*rows)
    valid=shifted[shifted>=0]
    result=np.resize(valid,E)
    result.flags.writeable=False
    return result

def dematch_llr(llr):
    values=np.asarray(llr,dtype=float)
    if values.shape!=(E,) or not np.isfinite(values).all():
        raise ValueError('Expected 7200 finite descrambled LLRs')
    out=np.zeros(3*D,dtype=float)
    np.add.at(out,rate_match_indices(),values)
    return out.reshape(3,D)

def crc24a(data):
    """Unreflected CRC24A, init=0, polynomial 0x864cfb, xorout=0."""
    crc=0
    for byte in bytes(data):
        crc ^= byte<<16
        for _ in range(8):
            crc=((crc<<1)^ (0x864cfb if crc&0x800000 else 0)) & 0xffffff
    return crc

class TurboDecoder:
    """Optional native decoder. An unavailable library raises explicitly.

    Each call owns its native context; no shared mutable decoder. Dematching
    sums in float before a common scale is applied, avoiding int8 overflow.
    Soft amplitude is relative, not a calibrated noise likelihood.
    """
    def __init__(self,library=None,iterations=6):
        if not isinstance(iterations,int) or not 1<=iterations<=16:
            raise ValueError('iterations must be 1..16')
        self.iterations=iterations
        self.library_path=Path(library) if library else DEFAULT_LIBRARY
        if not self.library_path.is_file():
            raise RuntimeError('Turbo backend unavailable; build native/turbofec first')
        self.lib=ctypes.CDLL(str(self.library_path))
        self.lib.alloc_tdec.restype=ctypes.c_void_p
        self.lib.free_tdec.argtypes=[ctypes.c_void_p]
        p=ctypes.POINTER(ctypes.c_int8)
        self.lib.lte_turbo_decode.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.POINTER(ctypes.c_uint8),p,p,p]
        self.lib.lte_turbo_decode.restype=ctypes.c_int
    def decode(self,llr):
        streams=dematch_llr(llr)
        maximum=float(np.max(np.abs(streams)))
        if maximum==0:
            return dict(payload=None,crc24_valid=False,crc24_residue=None,error='all LLRs are zero',backend='turbofec')
        # Native backend positive means bit ONE. Invert API convention.
        quantized=np.ascontiguousarray(np.clip(np.rint(-streams*63/maximum),-63,63),dtype=np.int8)
        output=np.empty(K//8,dtype=np.uint8)
        context=self.lib.alloc_tdec()
        if not context:raise MemoryError('alloc_tdec failed')
        ptr=ctypes.POINTER(ctypes.c_int8)
        try:
            status=self.lib.lte_turbo_decode(context,K,self.iterations,output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),*[s.ctypes.data_as(ptr) for s in quantized])
        finally:self.lib.free_tdec(context)
        if status:raise RuntimeError(f'Turbo backend status {status}')
        payload=output.tobytes();residue=crc24a(payload)
        return dict(payload=payload,crc24_valid=residue==0,crc24_residue=residue,error=None,backend='turbofec',iterations=self.iterations)
