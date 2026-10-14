"""Scan/hold policy renewed only by recent, qualified detector feedback."""
import math
from frequency_scheduler import FrequencyScheduler


class HitTimeoutScheduler(FrequencyScheduler):
    def __init__(self,frequencies_hz,dwell_s=.7,timeout_s=1.3,**unused):
        if not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('Positive timeout required')
        super().__init__(frequencies_hz,dwell_s=dwell_s,lock_on_hit=False,revisit_interval_s=60.)
        self.timeout_s=timeout_s;self.hold_until=None;self.hold_frequency=None;self.hold_observed=-math.inf

    def next_action(self,now):
        if self._stopped:return None
        if self.hold_until is not None and now<self.hold_until:
            self._time(now);retune=self._current!=self.hold_frequency
            self._current=self.hold_frequency;self._visits[self._current]=now
            return dict(frequency_hz=self._current,dwell_s=self.dwell_s,retune=retune,reason='qualified_hold',now=now)
        self.hold_until=None
        action=super().next_action(now)
        return action

    def on_feedback(self,acquisition_id,frequency_hz,detected_count,rx_ok=True,observed_at=None,now=None):
        result=super().on_feedback(acquisition_id,frequency_hz,detected_count,rx_ok,observed_at,now)
        if result['status']!='applied' or not rx_ok or not detected_count:return result
        now=self._last_time;observed=now if observed_at is None else observed_at
        if observed<self.hold_observed or now>=observed+self.timeout_s:
            return dict(status='ignored',reason='stale_for_hold')
        self.hold_until=observed+self.timeout_s;self.hold_frequency=frequency_hz;self.hold_observed=observed
        return dict(status='applied',reason='qualified_hold_renewed')

    def snapshot(self):
        return dict(super().snapshot(),qualified_hold_until=self.hold_until,qualified_hold_frequency=self.hold_frequency,timeout_s=self.timeout_s)

    def stop(self):
        self.hold_until=None;super().stop()
