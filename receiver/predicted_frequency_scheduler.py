"""Opt-in same-channel ZC/CP deadline control; no CRC gate or RF-hop claims."""
import collections,math
from short_boundary_scheduler import ShortBoundaryScheduler
class PredictedFrequencyScheduler(ShortBoundaryScheduler):
    def __init__(self,frequencies_hz,dwell_s=.7,timeout_s=1.3,boundary_s=.175,settle_s=.03,budgets=None):
        super().__init__(frequencies_hz,dwell_s,timeout_s,boundary_s)
        self.settle_s=settle_s;self.costs=collections.defaultdict(lambda:collections.deque(maxlen=32));self.seeds=budgets or {};self.jobs=[];self.job_counter=0;self.stats=collections.Counter();self.decisions=[];self.max_pending=128
    def tune_budget(self,origin,target):
        if origin==target:return 0.
        default=.18 if origin is None or (origin<3e9)!=(target<3e9) else .006
        observed=sorted(self.costs[(origin,target)])
        p95=observed[min(len(observed)-1,math.ceil(.95*len(observed))-1)] if observed else 0.
        return max(self.seeds.get((origin,target),default),p95)+self.settle_s+.003
    def observe_tune(self,origin,target,seconds):
        if origin is not None and math.isfinite(seconds) and seconds>=0:self.costs[(origin,target)].append(seconds)
    def on_qualified_frame(self,frame,now):
        interval=frame.get('host_start_interval_s');frequency=frame['frequency_hz']
        if interval is None or frequency not in self.frequencies_hz:self.stats['missing_time_or_unknown_channel']+=1;return
        for horizon in (1,2):
            lo=interval[0]+.64*horizon-.05;hi=interval[1]+.64*horizon+.05+.001
            if lo<=now:self.stats['seed_already_late']+=1;continue
            if any(j['frequency_hz']==frequency and abs(j['window_start_s']-lo)<.025 for j in self.jobs):self.stats['deduplicated']+=1;continue
            if len(self.jobs)>=self.max_pending:self.stats['queue_full']+=1;continue
            self.job_counter+=1;self.jobs.append(dict(prediction_id=self.job_counter,source_burst_id=frame['burst_id'],frequency_hz=frequency,window_start_s=lo,window_end_s=hi,source_confidence='zc_cp',horizon=horizon));self.stats['scheduled']+=1
        self.jobs.sort(key=lambda j:(j['window_start_s'],j['prediction_id']))
    def next_action(self,now):
        if self._stopped:return None
        self._time(now)
        while self.jobs:
            job=self.jobs[0];budget=self.tune_budget(self._current,job['frequency_hz'])
            if now>=job['window_end_s']:
                self.jobs.pop(0);self.stats['windows_finished']+=1;continue
            if self._current!=job['frequency_hz'] and now+budget>job['window_start_s']:
                self.jobs.pop(0);self.stats['discarded_cannot_arrive_in_time']+=1;continue
            # Reserve a worst observed band-switch cost before committing to a
            # baseline action. This may arrive early, but never waits blocking.
            roundtrip=max(self.tune_budget(self._current,f)+self.tune_budget(f,job['frequency_hz']) for f in self.frequencies_hz)
            if now+self.boundary_s+max(roundtrip,budget)>=job['window_start_s']:
                origin=self._current;retune=origin!=job['frequency_hz']
                available=job['window_end_s']-now-budget
                if available<.002:
                    self.jobs.pop(0);self.stats['discarded_no_receive_budget']+=1;continue
                self._current=job['frequency_hz'];self._visits[self._current]=now
                # A prediction interrupts, rather than silently resumes, an old
                # baseline visit; the scan cursor still progresses on fallback.
                self._remaining_dwell=0.;self._visit=None
                self.stats['predicted_actions']+=1
                action=dict(job,reason='predicted_zc_cp_window',retune=retune,now=now,dwell_s=min(self.boundary_s,available),nominal_dwell_s=self.dwell_s,boundary_visit_last=False,origin_frequency_hz=origin,tune_budget_s=budget)
                return action
            break
        origin=self._current;action=super().next_action(now)
        action.update(origin_frequency_hz=origin,tune_budget_s=self.tune_budget(origin,action['frequency_hz']))
        self.stats['baseline_actions']+=1;return action
    def snapshot(self):
        return dict(super().snapshot(),predictive_control=dict(self.stats),pending_predictions=len(self.jobs),period_s=.64,pair_budgets_s={f'{a}->{b}':self.tune_budget(a,b) for a,b in set(self.seeds)|set(self.costs)})
