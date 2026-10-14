"""Short RX decisions while preserving the nominal scan dwell budget.

Only the existing qualified-hit hold policy changes a frequency decision;
shadow predictions still never control RF. Finite receives remain separate.
"""
import math
from hit_timeout_scheduler import HitTimeoutScheduler

class ShortBoundaryScheduler(HitTimeoutScheduler):
    def __init__(self,frequencies_hz,dwell_s=.7,timeout_s=1.3,boundary_s=.175):
        if not math.isfinite(boundary_s) or not .02<=boundary_s<=dwell_s:
            raise ValueError('RX boundary must be 0.02 s through nominal dwell')
        super().__init__(frequencies_hz,dwell_s=dwell_s,timeout_s=timeout_s)
        self.boundary_s=boundary_s;self._visit=None;self._remaining_dwell=0.;self.boundary_actions=0
    def next_action(self,now):
        if self._stopped:return None
        self._time(now)
        new_hold=(self.hold_until is not None and now<self.hold_until and self.hold_frequency!=self._current)
        if self._remaining_dwell<=1e-9 or new_hold:
            self._visit=super().next_action(now);self._remaining_dwell=self.dwell_s
            retune=self._visit['retune']
        else:retune=False
        duration=min(self.boundary_s,self._remaining_dwell);self._remaining_dwell=max(0.,self._remaining_dwell-duration)
        self.boundary_actions+=1
        return dict(self._visit,dwell_s=duration,retune=retune,now=now,
                    reason=self._visit['reason'] if retune else 'short_boundary_'+self._visit['reason'],
                    nominal_dwell_s=self.dwell_s,boundary_visit_last=self._remaining_dwell<=1e-9)
    def snapshot(self):
        return dict(super().snapshot(),rx_boundary_s=self.boundary_s,boundary_actions=self.boundary_actions,remaining_visit_dwell_s=self._remaining_dwell)
