import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from predicted_frequency_scheduler import PredictedFrequencyScheduler
F1=2414.5e6;F2=2429.5e6;F3=5756.5e6

def frame(time=10.,freq=F1,id='a'):
 return dict(host_start_interval_s=[time,time+.0002],frequency_hz=freq,burst_id=id)
class PredictedSchedulerTests(unittest.TestCase):
 def make(self):return PredictedFrequencyScheduler([F1,F2,F3])
 def test_seed_and_dedup_do_not_need_crc(self):
  s=self.make();s.on_qualified_frame(frame(),10.1);s.on_qualified_frame(frame(id='b'),10.11);self.assertEqual(len(s.jobs),2);self.assertEqual(s.stats['deduplicated'],2)
 def test_budget_depends_on_pair_and_updates_conservatively(self):
  s=self.make();self.assertEqual(s.tune_budget(F1,F1),0);self.assertLess(s.tune_budget(F1,F2),s.tune_budget(F1,F3));old=s.tune_budget(F1,F2);s.observe_tune(F1,F2,.05);self.assertGreater(s.tune_budget(F1,F2),old)
 def test_earliest_prediction_selected_and_rx_capped_at_window_end(self):
  s=self.make();s.next_action(10.);s.on_qualified_frame(frame(),10.1);a=s.next_action(10.3);self.assertEqual(a['reason'],'predicted_zc_cp_window');self.assertEqual(a['frequency_hz'],F1);self.assertFalse(a['retune']);a=s.next_action(10.65);self.assertLess(a['dwell_s'],.05)
 def test_late_cross_band_job_is_discarded(self):
  s=self.make();s.next_action(10.);s.on_qualified_frame(frame(freq=F3),10.1);s.next_action(10.5);self.assertGreater(s.stats['discarded_cannot_arrive_in_time'],0)
 def test_fallback_returns_after_finite_predictions_expire(self):
  s=self.make();s.next_action(10.);s.on_qualified_frame(frame(),10.1);s.next_action(12.);self.assertFalse(s.jobs);self.assertNotEqual(s.next_action(12.2)['reason'],'predicted_zc_cp_window')
 def test_does_not_take_band_detour_which_prevents_return(self):
  s=self.make();s.next_action(10.);s.on_qualified_frame(frame(),10.1);s.next_action(10.65);a=s.next_action(10.70);self.assertEqual(a['reason'],'predicted_zc_cp_window');self.assertEqual(a['frequency_hz'],F1)
 def test_missing_clock_and_stale_seeds(self):
  s=self.make();f=frame();f['host_start_interval_s']=None;s.on_qualified_frame(f,10.1);self.assertFalse(s.jobs);s.on_qualified_frame(frame(),12.);self.assertFalse(s.jobs)
 def test_stop(self):
  s=self.make();s.stop();self.assertIsNone(s.next_action(10.))
if __name__=='__main__':unittest.main()
