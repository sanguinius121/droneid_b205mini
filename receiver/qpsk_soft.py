"""Soft QPSK observations for experimental LTE Turbo. Positive means bit zero.

Axis amplitudes are relative likelihoods, not noise-calibrated LLRs. The
serialization agrees with legacy qpsk: LSB then MSB, DC removed, pilot removed.
"""
import numpy as np
from goldgen import gold


def descrambled_llr(symbols,phase):
    if isinstance(phase,bool) or not isinstance(phase,(int,np.integer)) or phase not in range(4):raise ValueError('Phase must be 0..3')
    data=np.asarray(symbols)
    if data.shape not in ((7,601),(6,601)) or not np.iscomplexobj(data) or not np.isfinite(data).all():
        raise ValueError('Expected six or seven finite OFDM rows of 601 complex carriers')
    data=np.delete(data,300,axis=1)
    if len(data)==7:data=data[1:]
    axes=((data.imag,-data.real),(data.real,data.imag),(-data.imag,data.real),(-data.real,-data.imag))[phase]
    values=np.stack(axes,axis=-1).reshape(-1).astype(float)
    scrambler=np.asarray(gold(1600,len(values),0x12345678),dtype=bool)
    return values*np.where(scrambler,-1.,1.)


class SoftTurboQPSKDecoder:
    """Adapter for decode_symbols; rejects CRC24-invalid Turbo results."""
    def __init__(self,symbols,turbo=None):
        from turbo_decoder import TurboDecoder
        self.symbols=symbols;self.turbo=turbo or TurboDecoder();self.phase=None
        self.turbo_attempts=[]
    def raw_data_to_symbol_bits(self,phase):
        self.llr=descrambled_llr(self.symbols,phase);self.phase=phase
    def magic(self):
        result=self.turbo.decode(self.llr)
        self.turbo_attempts.append(dict(phase=self.phase,crc24_valid=result['crc24_valid'],crc24_residue=result['crc24_residue']))
        if not result['crc24_valid']:raise ValueError('Turbo CRC24A failed')
        return result['payload']
