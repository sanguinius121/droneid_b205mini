"""Bounded single-worker processing queue. No UHD imports or hardware access.

submit copies IQ and metadata; budget includes queued and in-flight bytes.
It drops entire new acquisitions rather than silently overwriting queued data.
"""
import copy
import threading
from collections import deque
import numpy as np


class BoundedAcquisitionPipeline:
    def __init__(self, max_queue_bytes, process_block, on_drop=None):
        if isinstance(max_queue_bytes,bool) or not isinstance(max_queue_bytes,int) or max_queue_bytes <= 0:
            raise ValueError('max_queue_bytes must be a positive integer')
        if not callable(process_block) or (on_drop is not None and not callable(on_drop)):
            raise ValueError('Callbacks must be callable')
        self.max_queue_bytes=max_queue_bytes
        self.process_block=process_block
        self.on_drop=on_drop
        self._condition=threading.Condition()
        self._queue=deque()
        self._thread=None
        self._closing=False
        self._error=None
        self._stats=dict(submitted=0,processed=0,dropped=0,dropped_samples=0,
                         outstanding_bytes=0,peak_outstanding_bytes=0,failures=0,
                         failed_samples=0,failed_acquisition_id=None)
        # Persist all loss records via on_drop in a runner; retain only bounded recent history.
        self._drops=deque(maxlen=256)

    @property
    def stats(self):
        with self._condition:
            return dict(self._stats,drop_records=copy.deepcopy(list(self._drops)),
                        drop_history_truncated=self._stats['dropped'] > len(self._drops))

    def start(self):
        with self._condition:
            if self._closing or self._thread is not None:
                raise RuntimeError('Pipeline can only be started once')
            self._thread=threading.Thread(target=self._run,name='droneid-process',daemon=True)
            self._thread.start()
        return self

    def _drop_locked(self, record, samples, reason):
        event=dict(acquisition_id=record.get('acquisition_id'),sample_count=len(samples),
                   dropped_samples=len(samples),reason=reason)
        self._stats['dropped']+=1
        self._stats['dropped_samples']+=len(samples)
        self._drops.append(event)
        return event

    def submit(self, samples, record):
        samples=np.asarray(samples)
        if samples.ndim!=1 or not np.iscomplexobj(samples):
            raise ValueError('IQ must be a one-dimensional complex array')
        if len(samples)==0:
            raise ValueError('Empty/error acquisitions must be journaled separately from the IQ queue')
        if not isinstance(record,dict) or not isinstance(record.get('acquisition_id'),str) or not record['acquisition_id']:
            raise ValueError('Acquisition identity is required for queue accounting')
        if record.get('sample_count')!=len(samples):
            raise ValueError('Acquisition sample_count mismatch')
        size=len(samples)*np.dtype('<c8').itemsize
        event=None
        with self._condition:
            if self._thread is None or self._closing:
                raise RuntimeError('Pipeline is not accepting acquisitions')
            if self._error is not None:
                raise RuntimeError('Processing worker failed') from self._error
            if self._stats['outstanding_bytes']+size > self.max_queue_bytes:
                event=self._drop_locked(record,samples,'queue_drop')
            else:
                # Copy under the budget lock; caller may reuse its receive buffer afterwards.
                owned=np.array(samples,dtype='<c8',copy=True)
                self._queue.append((owned,copy.deepcopy(record)))
                self._stats['submitted']+=1
                self._stats['outstanding_bytes']+=size
                self._stats['peak_outstanding_bytes']=max(self._stats['peak_outstanding_bytes'],self._stats['outstanding_bytes'])
                self._condition.notify_all()
        if event is not None:
            if self.on_drop is not None:
                self.on_drop(dict(event))
            return False
        return True

    def _run(self):
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait()
                if not self._queue:
                    return
                samples,record=self._queue.popleft()
            try:
                self.process_block(samples,record)
            except BaseException as error:
                pending=[]
                with self._condition:
                    self._error=error
                    self._stats['failures']+=1
                    self._stats['failed_samples']+=len(samples)
                    self._stats['failed_acquisition_id']=record.get('acquisition_id')
                    self._stats['outstanding_bytes']-=samples.nbytes
                    self._closing=True
                    while self._queue:
                        iq,meta=self._queue.popleft()
                        pending.append(self._drop_locked(meta,iq,'worker_failure'))
                        self._stats['outstanding_bytes']-=iq.nbytes
                    self._condition.notify_all()
                for event in pending:
                    if self.on_drop is not None:
                        try:self.on_drop(dict(event))
                        except BaseException:pass  # Original worker exception remains the primary failure.
                return
            with self._condition:
                self._stats['processed']+=1
                self._stats['outstanding_bytes']-=samples.nbytes
                self._condition.notify_all()

    def close(self, drain=True):
        events=[]
        with self._condition:
            self._closing=True
            if not drain:
                while self._queue:
                    samples,record=self._queue.popleft()
                    events.append(self._drop_locked(record,samples,'shutdown_drop'))
                    self._stats['outstanding_bytes']-=samples.nbytes
            self._condition.notify_all()
            thread=self._thread
        callback_error=None
        for event in events:
            if self.on_drop is not None:
                try:self.on_drop(dict(event))
                except BaseException as error:callback_error=error
        if thread is threading.current_thread():
            raise RuntimeError('Worker cannot close and join itself')
        if thread is not None:
            thread.join()
        if self._error is not None:
            raise RuntimeError('Processing worker failed') from self._error
        if callback_error is not None:
            raise RuntimeError('Drop callback failed') from callback_error
