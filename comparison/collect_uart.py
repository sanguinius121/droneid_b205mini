import argparse,sys,pathlib
ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import collect,uart_stream
from adapters import add_uart_coordinates
ORDER='lonlat'
class MapParser(uart_stream.UARTParser):
    def feed(self,data,monotonic_ns,wall_ns):
        # Process one complete line at a time to preserve coordinate/message pairing.
        self.buffer+=data;out=[]
        while b'\n' in self.buffer:
            line,remaining=self.buffer.split(b'\n',1);self.buffer=b''
            records=super().feed(line+b'\n',monotonic_ns,wall_ns)
            self.buffer=remaining
            for record in records:out.append(add_uart_coordinates(record,line.decode(errors='replace'),ORDER))
        if len(self.buffer)>1048576:raise ValueError('UART line exceeds 1 MiB')
        return out
if __name__=='__main__':
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--gps-order',choices=['latlon','lonlat'],default='lonlat');args,rest=p.parse_known_args();ORDER=args.gps_order
    collect.UARTParser=MapParser;sys.argv=[sys.argv[0]]+rest;collect.main()
