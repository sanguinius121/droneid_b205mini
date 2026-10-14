#!/usr/bin/env python3
"""Local map; opening the page never starts a hardware capture."""
import argparse,json,pathlib,signal,sys
from urllib.parse import urlsplit
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from contextlib import asynccontextmanager
from engine import Engine
HERE=pathlib.Path(__file__).resolve().parent
engine=Engine()
@asynccontextmanager
async def lifespan(app):
    yield
    engine.stop()
    if engine.thread:engine.thread.join(45)
app=FastAPI(title='DroneID — three receiver map',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=HERE/'static'),name='static')
@app.get('/')
def index():return FileResponse(HERE/'static/index.html')
@app.get('/api/state')
def state():return JSONResponse(engine.snapshot(),headers={'Cache-Control':'no-store'})
def check_origin(request):
    origin=request.headers.get('origin')
    if origin and urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'Origin không hợp lệ')
@app.post('/api/start')
async def start(request:Request):
    check_origin(request)
    if engine.replay:raise HTTPException(409,'Đang xem replay; khởi động server không có --replay để thu live')
    data=await request.json()
    try:
        seconds=int(data.get('seconds',1200));order=data.get('uart_order','lonlat')
        if not 30<=seconds<=3600 or order not in ('latlon','lonlat'):raise ValueError('Thời lượng phải từ 30 tới 3600 giây')
        engine.start(seconds,order)
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
    return {'status':'preparing'}
@app.post('/api/stop')
def stop(request:Request):
    check_origin(request);engine.stop();return {'status':'stopping'}
@app.get('/api/export')
def export():return JSONResponse(engine.snapshot(),headers={'Content-Disposition':'attachment; filename="droneid-session.json"'})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8088);p.add_argument('--replay',type=pathlib.Path);p.add_argument('--config',type=pathlib.Path,help='Connection settings JSON');args=p.parse_args()
    if args.config:engine.config=json.loads(args.config.read_text())
    if args.replay:engine.load_replay(args.replay.resolve())
    uvicorn.run(app,host=args.host,port=args.port)
