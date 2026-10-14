"""Single-owner IQ + JSONL writer, with validation and rollback on write failure."""
import hashlib
import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path
import numpy as np
from capture_records import STAGES, validate_acquisition, validate_burst


class BurstWriter:
    def __init__(self,output_root,iq_filename='bursts.fc32',metadata_filename='records.jsonl'):
        self.root=Path(output_root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        def safe(filename):
            p=Path(filename)
            if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('Output filename must stay inside output root')
            resolved=(self.root/p).resolve()
            if not resolved.is_relative_to(self.root):raise ValueError('Output path escapes root')
            return resolved
        self.iq_path=safe(iq_filename);self.metadata_path=safe(metadata_filename)
        if self.iq_path==self.metadata_path:raise ValueError('IQ and metadata paths must differ')
        for p in [self.iq_path,self.metadata_path]:
            if p.exists():raise FileExistsError('Refusing to overwrite capture evidence')
            p.parent.mkdir(parents=True,exist_ok=True)
        self._iq=self.iq_path.open('xb')
        try:self._metadata=self.metadata_path.open('xb')
        except BaseException:
            self._iq.close();self.iq_path.unlink();raise
        self._owner=None;self._closed=False;self._acquisitions={};self.candidate_saved=0

    def _check(self):
        if self._closed:raise RuntimeError('Writer is closed')
        current=threading.get_ident()
        if self._owner is None:self._owner=current
        elif current!=self._owner:raise RuntimeError('Writer requires a single processing owner')

    def _append_record(self,record):
        encoded=(json.dumps(record,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
        count=self._metadata.write(encoded)
        if count!=len(encoded):raise OSError('Short metadata write')
        self._metadata.flush()

    def write_acquisition(self,record):
        self._check();validate_acquisition(record)
        identity=record['acquisition_id']
        if identity in self._acquisitions:raise ValueError('Duplicate acquisition identity')
        offset=self._metadata.tell()
        try:self._append_record(record)
        except BaseException:
            self._metadata.seek(offset);self._metadata.truncate();raise
        self._acquisitions[identity]=deepcopy(record)

    def write_burst(self,acquisition,samples,burst_range,slice_range,cfo_hz=None):
        self._check();validate_acquisition(acquisition)
        stored=self._acquisitions.get(acquisition['acquisition_id'])
        if stored is None or stored!=acquisition:raise ValueError('Acquisition must be registered unchanged before burst')
        samples=np.asarray(samples)
        if samples.ndim!=1 or not np.iscomplexobj(samples) or len(samples)!=acquisition['sample_count']:raise ValueError('Source IQ does not match acquisition')
        # Validate bounds before indexing; Python slicing must not silently clamp invalid requests.
        body=deepcopy(burst_range);padded=deepcopy(slice_range)
        record=dict(schema_version=1,record_type='burst',session_id=acquisition['session_id'],acquisition_id=acquisition['acquisition_id'],burst_id=str(uuid.uuid4()),burst_range=body,slice_range=padded,cfo_hz=cfo_hz,output_file=str(self.iq_path.relative_to(self.root)),output_offset_bytes=self._iq.tell(),output_length_samples=padded.get('end_sample',0)-padded.get('start_sample',0),iq_sha256='0'*64,duplicate_of=None,stage_status={stage:('passed' if stage=='detected' else 'not_run') for stage in STAGES},failure_reason=None)
        validate_burst(record,acquisition)
        data=np.asarray(samples[padded['start_sample']:padded['end_sample']],dtype='<c8').tobytes()
        record['iq_sha256']=hashlib.sha256(data).hexdigest()
        iq_offset,metadata_offset=self._iq.tell(),self._metadata.tell()
        try:
            if self._iq.write(data)!=len(data):raise OSError('Short IQ write')
            self._iq.flush()
            self._append_record(record)
        except BaseException:
            self._iq.seek(iq_offset);self._iq.truncate()
            self._metadata.seek(metadata_offset);self._metadata.truncate()
            raise
        self.candidate_saved+=1
        return deepcopy(record)

    def close(self):
        if not self._closed:
            try:self._iq.close()
            finally:self._metadata.close();self._closed=True

    def __enter__(self):return self
    def __exit__(self,*exc):self.close()
