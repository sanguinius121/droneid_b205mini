"""Bounded TCP collector using the project's actual corrected CSV handler."""
import argparse,importlib.util,json,pathlib,signal,socket,threading,time
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bridge',ROOT/'vendor/dji_receiver.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path,required=True);parser.add_argument('--seconds',type=float,default=240)
    parser.add_argument('--listen-ip',default='172.31.100.1');parser.add_argument('--listen-port',type=int,default=52002);parser.add_argument('--peer-ip',default='172.31.100.2')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    stop=threading.Event();signal.signal(signal.SIGTERM,lambda *a:stop.set());signal.signal(signal.SIGINT,lambda *a:stop.set())
    begin=time.monotonic_ns();counts={'connections':0,'bytes':0,'messages':0};errors=[];threads=[];connections=[];lock=threading.Lock()
    with (args.output/'tcp.bin').open('xb') as raw,(args.output/'chunks.jsonl').open('x') as chunks,(args.output/'messages.jsonl').open('x') as messages:
        class Sink:
            def put(self,decoded):
                with lock:
                    messages.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(),wall_ns=time.time_ns(),decoded=decoded))+'\n');messages.flush();counts['messages']+=1
        class RecordingSocket:
            def __init__(self,conn,peer):self.conn=conn;self.peer=peer
            def __getattr__(self,name):return getattr(self.conn,name)
            def recv(self,n):
                data=self.conn.recv(n)
                if data:
                    now=time.monotonic_ns()
                    with lock:
                        raw.write(data);raw.flush();chunks.write(json.dumps(dict(monotonic_ns=now,wall_ns=time.time_ns(),peer=self.peer,offset=counts['bytes'],hex=data.hex()))+'\n');chunks.flush();counts['bytes']+=len(data)
                return data
        server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:
            server.bind((args.listen_ip,args.listen_port));server.listen();server.settimeout(.2)
            begin=time.monotonic_ns();(args.output/'ready.json').write_text(json.dumps(dict(start_monotonic_ns=begin,listen=f'{args.listen_ip}:{args.listen_port}')))
            while not stop.is_set() and (time.monotonic_ns()-begin)/1e9<args.seconds:
                try:conn,peer=server.accept()
                except socket.timeout:continue
                if args.peer_ip and peer[0]!=args.peer_ip:conn.close();continue
                counts['connections']+=1;connections.append(conn)
                worker=threading.Thread(target=bridge.new_fw_connection_handler,args=(RecordingSocket(conn,peer),peer,Sink()),daemon=True);threads.append(worker);worker.start()
        except BaseException as exc:errors.append(repr(exc));raise
        finally:
            server.close()
            for conn in connections:
                try:conn.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            for worker in threads:worker.join(3)
            end=time.monotonic_ns();summary=dict(start_monotonic_ns=begin,end_monotonic_ns=end,elapsed_s=(end-begin)/1e9,counts=counts,errors=errors)
            (args.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
