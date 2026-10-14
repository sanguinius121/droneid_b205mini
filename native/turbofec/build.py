#!/usr/bin/env python3
"""Build optional GPL-3.0-or-later turbofec locally, without installation."""
import hashlib,json,platform,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
if platform.machine() not in ('x86_64','amd64'):
    raise SystemExit('This native backend requires x86_64 SSSE3; another backend is needed')
flags=Path('/proc/cpuinfo').read_text()
if 'ssse3' not in flags:raise SystemExit('CPU lacks required SSSE3')
command=['gcc','-shared','-fPIC','-O3','-mssse3','-DHAVE_SSE3','-I'+str(root/'include'),str(root/'src/turbo_dec.c'),str(root/'src/turbo_enc.c'),str(root/'src/turbo_rate_match.c'),'-o',str(root/'libturbofec.so')]
subprocess.run(command,check=True)
provenance=json.loads((root/'provenance.json').read_text())
provenance['build_command']=command
provenance['binary_sha256']=hashlib.sha256((root/'libturbofec.so').read_bytes()).hexdigest()
(root/'build_result.json').write_text(json.dumps(provenance,indent=2))
print('Built',root/'libturbofec.so')
