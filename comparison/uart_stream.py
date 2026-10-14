"""Frame text UART output; a log line is not necessarily a DroneID message."""
import re
from collections import Counter

CRC=re.compile(r'CRC flag\s+([01])\b')
MESSAGE=re.compile(r'\bnum=(\d+),\s*device=(\d+),\s*serial=([^,\r\n]+),\s*model=([^,\r\n]+),[^\r\n]*?\bfreq=([\d.]+),[^\r\n]*?\bdistance=[^,\r\n]*km,')

class UARTParser:
    def __init__(self):
        self.buffer=b'';self.pending_crc=None;self.counts=Counter()

    def feed(self,data,monotonic_ns,wall_ns):
        self.buffer+=data;out=[]
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1);line=line.decode('utf-8',errors='replace')
            self.counts['lines']+=1
            crc=CRC.search(line)
            if crc:
                self.pending_crc=int(crc.group(1));self.counts['crc_headers']+=1
            matches=list(MESSAGE.finditer(line))
            for m in matches:
                flag=self.pending_crc;self.pending_crc=None
                record=dict(monotonic_ns=monotonic_ns,wall_ns=wall_ns,num=int(m[1]),device=int(m[2]),
                            serial=m[3].strip(),model=m[4].strip(),frequency_mhz=float(m[5]),crc_flag=flag)
                self.counts['messages']+=1;self.counts['crc_valid' if flag==1 else 'crc_unknown' if flag is None else 'crc_failed']+=1
                out.append(record)
            if not matches and 'wifi mac=' in line:self.counts['wifi_lines']+=1
        if len(self.buffer)>1048576:raise ValueError('UART line exceeds 1 MiB')
        return out
