"""Offline scan/lock/revisit policy; no device access or CFO-driven retuning.

Reviewed from the SCHEDULER subagent draft. Feedback timestamps may be old;
only decision timestamps must be monotonic. Finite lock actions preserve scan cursor.
"""
import math
from collections import OrderedDict


def _finite(value,name,positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(f'{name} must be finite')
    if positive and value<=0:raise ValueError(f'{name} must be positive')
    return float(value)


class FrequencyScheduler:
    def __init__(self,frequencies_hz,dwell_s=1.,lock_rounds=4,revisit_interval_s=30.,lock_on_hit=True):
        if isinstance(frequencies_hz,(str,bytes)):raise ValueError('Frequency list required')
        self.frequencies_hz=tuple(_finite(f,'frequency',True) for f in frequencies_hz)
        if not self.frequencies_hz or len(set(self.frequencies_hz))!=len(self.frequencies_hz):raise ValueError('Require nonempty unique frequencies')
        self.dwell_s=_finite(dwell_s,'dwell',True)
        self.revisit_interval_s=_finite(revisit_interval_s,'revisit',True)
        if isinstance(lock_rounds,bool) or not isinstance(lock_rounds,int) or lock_rounds<0:raise ValueError('lock_rounds must be nonnegative integer')
        if not isinstance(lock_on_hit,bool):raise ValueError('lock_on_hit must be bool')
        self.lock_rounds=lock_rounds;self.lock_on_hit=lock_on_hit
        self._cursor=0;self._current=None;self._lock=None;self._remaining=0;self._deadline=None
        self._last_time=None;self._epoch=None;self._stopped=False
        self._visits={};self._pending=OrderedDict();self._recent=OrderedDict();self._latest_feedback={}
        self._lock_eligible=set(self.frequencies_hz)
        self.ignored_feedback=0

    def _time(self,now):
        now=self._last_time if now is None and self._last_time is not None else (0. if now is None else _finite(now,'now'))
        if self._last_time is not None and now<self._last_time:raise ValueError('Decision time moved backwards')
        self._last_time=now
        return now

    def _release(self):
        self._lock=None;self._remaining=0;self._deadline=None

    def next_action(self,now):
        if self._stopped:return None
        now=self._time(now)
        if self._epoch is None:self._epoch=now
        if self._lock is not None and (self._remaining<=0 or now>=self._deadline):self._release()
        overdue=[f for f in self.frequencies_hz if f!=self._current and now-self._visits.get(f,self._epoch)>=self.revisit_interval_s]
        # Pure scan mode preserves list order; adaptive mode may visit the oldest overdue channel.
        if self.lock_on_hit and overdue:
            frequency=min(overdue,key=lambda f:self._visits.get(f,self._epoch))
            self._release();reason='revisit'
            if frequency==self.frequencies_hz[self._cursor]:self._cursor=(self._cursor+1)%len(self.frequencies_hz)
        elif self._lock is not None:
            frequency=self._lock;reason='lock';self._remaining-=1
        else:
            frequency=self.frequencies_hz[self._cursor]
            self._cursor=(self._cursor+1)%len(self.frequencies_hz);reason='scan'
        if self._current is not None and frequency!=self._current:
            # A channel can lock again only after the radio has visited another channel.
            self._lock_eligible.add(self._current)
        retune=frequency!=self._current
        self._current=frequency;self._visits[frequency]=now
        return dict(frequency_hz=frequency,dwell_s=self.dwell_s,retune=retune,reason=reason,now=now)

    def register_acquisition(self,acquisition_id,frequency_hz,started_at):
        if self._stopped:raise RuntimeError('Scheduler stopped')
        if not isinstance(acquisition_id,str) or not acquisition_id:raise ValueError('Acquisition ID required')
        frequency=_finite(frequency_hz,'frequency',True);start=_finite(started_at,'started_at')
        if frequency not in self.frequencies_hz:raise ValueError('Unknown frequency')
        if acquisition_id in self._pending or acquisition_id in self._recent:raise ValueError('Duplicate acquisition ID')
        self._pending[acquisition_id]=dict(frequency_hz=frequency,started_at=start)
        while len(self._pending)>256:self._pending.popitem(last=False)

    def on_feedback(self,acquisition_id,frequency_hz,detected_count,rx_ok=True,observed_at=None,now=None):
        if not isinstance(acquisition_id,str) or not acquisition_id:
            raise ValueError('Acquisition ID required')
        now=self._time(now)
        frequency=_finite(frequency_hz,'frequency',True)
        if isinstance(detected_count,bool) or not isinstance(detected_count,int) or detected_count<0:raise ValueError('Invalid detected_count')
        if not isinstance(rx_ok,bool):raise ValueError('rx_ok must be bool')
        observed=now if observed_at is None else _finite(observed_at,'observed_at')
        def ignored(reason):
            self.ignored_feedback+=1
            return dict(status='ignored',reason=reason)
        if self._stopped:return ignored('stopped')
        if acquisition_id in self._recent:return ignored('duplicate_feedback')
        pending=self._pending.get(acquisition_id)
        if pending is None:return ignored('unknown_acquisition')
        if frequency!=pending['frequency_hz']:return ignored('frequency_mismatch')
        if not pending['started_at']<=observed<=now:return ignored('invalid_timestamp')
        self._pending.pop(acquisition_id)
        self._recent[acquisition_id]=True
        while len(self._recent)>256:self._recent.popitem(last=False)
        if now-observed>self.revisit_interval_s or now-pending['started_at']>self.revisit_interval_s:return ignored('expired_feedback')
        source_order=(pending['started_at'],observed)
        if source_order<self._latest_feedback.get(frequency,(-math.inf,-math.inf)):
            return ignored('out_of_order_feedback')
        self._latest_feedback[frequency]=source_order
        if not rx_ok:return dict(status='applied',reason='rx_error')
        if detected_count and self.lock_on_hit and self.lock_rounds:
            if self._lock==frequency:return dict(status='applied',reason='lock_not_extended')
            if frequency not in self._lock_eligible:return dict(status='applied',reason='lock_cap')
            self._lock=frequency;self._remaining=self.lock_rounds
            self._deadline=now+self.lock_rounds*self.dwell_s
            self._lock_eligible.discard(frequency)
            return dict(status='applied',reason='hit_on_reported_frequency')
        if not detected_count and self._lock==frequency:self._release()
        return dict(status='applied',reason='no_hit' if not detected_count else 'scan_only')

    def stop(self):
        self._stopped=True;self._release()

    def snapshot(self):
        return dict(scan_cursor_index=self._cursor,current_frequency_hz=self._current,lock_frequency_hz=self._lock,lock_rounds_remaining=self._remaining,lock_deadline=self._deadline,last_monotonic=self._last_time,pending_acquisitions=len(self._pending),last_visit=dict(self._visits),ignored_feedback=self.ignored_feedback,stopped=self._stopped)
