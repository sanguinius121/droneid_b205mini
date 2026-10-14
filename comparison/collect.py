"""Passive serial logger with graceful stop and line-level receive timestamps."""
import argparse,json,select,signal,time
from pathlib import Path
import serial
from uart_stream import UARTParser

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=240)
    p.add_argument('--ports',nargs='+',default=['/dev/ttyUSB0','/dev/ttyUSB1']);p.add_argument('--baudrate',type=int,default=115200)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);ports=[];parsers={};counts={};files={}
    stopped=False
    def stop(*unused):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    errors=[];begin=time.monotonic_ns();wall=time.time_ns()
    try:
        for device in a.ports:
            name=Path(device).name
            port=serial.Serial(port=None,baudrate=a.baudrate,timeout=0,exclusive=True)
            port.dtr=False;port.rts=False;port.port=device;port.open();ports.append(port)
            parsers[name]=UARTParser();counts[name]=0;files[name]=(a.output/(name+'.bin')).open('xb')
        begin=time.monotonic_ns();wall=time.time_ns()
        (a.output/'ready.json').write_text(json.dumps(dict(start_monotonic_ns=begin,start_wall_ns=wall,ports=[p.port for p in ports])))
        with (a.output/'chunks.jsonl').open('x') as chunks,(a.output/'messages.jsonl').open('x') as messages:
            while not stopped and time.monotonic_ns()-begin<a.seconds*1e9:
                ready,_,_=select.select(ports,[],[],.1)
                for port in ready:
                    data=port.read(max(1,min(port.in_waiting,65536)))
                    if not data:continue
                    name=Path(port.port).name;now=time.monotonic_ns();utc=time.time_ns()
                    files[name].write(data)
                    chunks.write(json.dumps(dict(port=name,monotonic_ns=now,wall_ns=utc,offset=counts[name],hex=data.hex()))+'\n');chunks.flush();counts[name]+=len(data)
                    for m in parsers[name].feed(data,now,utc):messages.write(json.dumps(dict(m,port=name))+'\n');messages.flush()
    except BaseException as exc:
        errors.append(repr(exc));raise
    finally:
        end=time.monotonic_ns()
        for f in files.values():f.close()
        for p in ports:p.close()
        summary=dict(start_monotonic_ns=begin,end_monotonic_ns=end,start_wall_ns=wall,elapsed_s=(end-begin)/1e9,
                     bytes=counts,errors=errors,parsers={k:dict(v.counts,buffered_bytes=len(v.buffer)) for k,v in parsers.items()})
        (a.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
