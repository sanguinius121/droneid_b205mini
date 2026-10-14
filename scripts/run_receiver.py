#!/usr/bin/env python3
"""Portable entry point; additional arguments are forwarded to the receiver."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
args=sys.argv[1:]; baseline='--baseline' in args
if baseline:args.remove('--baseline')
flags=['--center-first']
if not baseline:flags+=['--predictor-shadow','--rx-boundary','0.175','--predictor-control']
os.execv(sys.executable,[sys.executable,str(root/'receiver/droneid_hybrid.py'),*flags,*args])
