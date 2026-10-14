"""QPSK phase attempts with raw-byte CRC independent of strict payload parsing."""
from droneid_packet import DRONEID_MAX_LEN, DroneIDPacket, check_raw_crc
from qpsk import Decoder


def decode_symbols(symbols,decoder_factory=None):
    decoder=(decoder_factory or Decoder)(symbols)
    attempts=[]
    result=dict(attempts=attempts,selected_phase=None,raw_crc_status='not_run',
                payload_parse_status='not_run',payload=None,raw_payload_hex=None,
                crc_valid=False,payload_parsed=False,diagnostic_payload=None)
    for phase in range(4):
        attempt=dict(phase=phase,qpsk_status='not_run',payload_bytes_status='not_run',
                     raw_crc_status='not_run',payload_parse_status='not_run',error=None)
        attempts.append(attempt)
        try:
            decoder.raw_data_to_symbol_bits(phase)
            attempt['qpsk_status']='passed'
        except Exception as error:
            attempt['qpsk_status']='failed';attempt['error']=f'{type(error).__name__}: {error}'
            continue
        try:
            raw=decoder.magic()
            if raw is None or raw is False:
                raise ValueError('Decoder produced no payload bytes')
            raw=bytes(raw)
            if not raw:raise ValueError('Decoder produced empty payload bytes')
            attempt['payload_bytes_status']='passed'
        except Exception as error:
            attempt['payload_bytes_status']='failed';attempt['error']=f'{type(error).__name__}: {error}'
            continue
        if len(raw)<DRONEID_MAX_LEN:
            attempt['error']=f'Payload too short for CRC: {len(raw)} < {DRONEID_MAX_LEN}'
            continue
        crc_valid=check_raw_crc(raw)
        attempt['raw_crc_status']='passed' if crc_valid else 'failed'
        try:
            payload=dict(DroneIDPacket(raw).droneid)
            attempt['payload_parse_status']='passed'
            # Parser success on wrong CRC is diagnostic only.
            if not crc_valid:result['diagnostic_payload']=payload
        except Exception as error:
            payload=None;attempt['payload_parse_status']='failed'
            attempt['error']=f'{type(error).__name__}: {error}'
        if crc_valid:
            result.update(selected_phase=phase,raw_crc_status='passed',
                payload_parse_status=attempt['payload_parse_status'],payload=payload,
                raw_payload_hex=raw.hex(),crc_valid=True,payload_parsed=payload is not None)
            return result
    if any(a['raw_crc_status']=='failed' for a in attempts):result['raw_crc_status']='failed'
    if any(a['payload_parse_status']=='passed' for a in attempts):
        result['payload_parse_status']='passed'
        result['payload_parsed']=True
    elif any(a['payload_parse_status']=='failed' for a in attempts):result['payload_parse_status']='failed'
    return result
