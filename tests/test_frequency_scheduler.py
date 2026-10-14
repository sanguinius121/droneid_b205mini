import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from frequency_scheduler import FrequencyScheduler

FREQUENCIES=[2414.5,2429.502441,2434.5,2444.5,2459.5,2474.5,5721.5,5731.5,5741.5,5756.5,5761.5,5771.5,5786.5,5801.5,5816.5,5831.5]

class SchedulerTests(unittest.TestCase):
    def test_scan_all_sixteen_in_order(self):
        scheduler=FrequencyScheduler([f*1e6 for f in FREQUENCIES],lock_on_hit=False)
        actions=[scheduler.next_action(i) for i in range(17)]
        self.assertEqual([a['frequency_hz'] for a in actions[:16]],[f*1e6 for f in FREQUENCIES])
        self.assertEqual(actions[16]['frequency_hz'],FREQUENCIES[0]*1e6)
    def test_lock_preserves_scan_cursor_and_expires(self):
        s=FrequencyScheduler([1.,2.,3.],lock_rounds=2,revisit_interval_s=30.)
        self.assertEqual(s.next_action(0.)['frequency_hz'],1.)
        s.register_acquisition('A',1.,0.);s.on_feedback('A',1.,1,observed_at=0.,now=0.)
        a=s.next_action(0.1);b=s.next_action(0.2);c=s.next_action(0.3)
        self.assertEqual([a['frequency_hz'],b['frequency_hz'],c['frequency_hz']],[1.,1.,2.])
        self.assertFalse(a['retune']);self.assertFalse(b['retune']);self.assertTrue(c['retune'])
    def test_persistent_hits_cannot_starve_other_channels(self):
        s=FrequencyScheduler([1.,2.,3.],lock_rounds=2,revisit_interval_s=3.)
        visited=[]
        for i in range(30):
            action=s.next_action(float(i));frequency=action['frequency_hz'];visited.append(frequency)
            s.register_acquisition(str(i),frequency,float(i))
            s.on_feedback(str(i),frequency,1,observed_at=float(i),now=float(i))
        for start in range(0,24,6):self.assertEqual(set(visited[start:start+6]),{1.,2.,3.})
    def test_late_feedback_locks_reported_channel_not_current_channel(self):
        s=FrequencyScheduler([1.,2.,3.]);s.next_action(0.);s.register_acquisition('A',1.,0.)
        self.assertEqual(s.next_action(1.)['frequency_hz'],2.)
        status=s.on_feedback('A',1.,1,observed_at=0.5,now=1.)
        self.assertEqual(status['status'],'applied')
        self.assertEqual(s.next_action(1.1)['frequency_hz'],1.)
    def test_stale_mismatch_duplicate_and_unknown_feedback(self):
        s=FrequencyScheduler([1.,2.],revisit_interval_s=3.)
        s.next_action(0.);s.register_acquisition('A',1.,0.)
        self.assertEqual(s.on_feedback('A',2.,1,now=0.)['reason'],'frequency_mismatch')
        self.assertEqual(s.on_feedback('A',1.,1,observed_at=0.,now=4.)['reason'],'expired_feedback')
        self.assertEqual(s.on_feedback('A',1.,1,now=4.)['reason'],'duplicate_feedback')
        self.assertEqual(s.on_feedback('unknown',1.,1,now=4.)['reason'],'unknown_acquisition')
        self.assertIsNone(s.snapshot()['lock_frequency_hz'])
    def test_out_of_order_feedback_does_not_release_newer_lock(self):
        s=FrequencyScheduler([1.,2.]);s.next_action(0.)
        s.register_acquisition('old',1.,0.);s.register_acquisition('new',1.,1.)
        s.on_feedback('new',1.,1,observed_at=1.,now=2.)
        result=s.on_feedback('old',1.,0,observed_at=0.,now=2.)
        self.assertEqual(result['reason'],'out_of_order_feedback')
        self.assertEqual(s.snapshot()['lock_frequency_hz'],1.)
    def test_rx_error_does_not_clear_lock_as_no_hit(self):
        s=FrequencyScheduler([1.,2.]);s.next_action(0.);s.register_acquisition('A',1.,0.)
        s.on_feedback('A',1.,1,now=0.);s.register_acquisition('B',1.,0.1)
        self.assertEqual(s.on_feedback('B',1.,0,rx_ok=False,now=0.1)['reason'],'rx_error')
        self.assertEqual(s.snapshot()['lock_frequency_hz'],1.)
    def test_late_processing_timestamp_does_not_make_old_acquisition_newer(self):
        s=FrequencyScheduler([1.,2.]);s.next_action(0.)
        s.register_acquisition('old',1.,0.);s.register_acquisition('new',1.,1.)
        s.on_feedback('new',1.,1,observed_at=1.,now=2.)
        result=s.on_feedback('old',1.,0,observed_at=2.,now=2.)
        self.assertEqual(result['reason'],'out_of_order_feedback')
        self.assertEqual(s.snapshot()['lock_frequency_hz'],1.)
    def test_stop_is_permanent(self):
        s=FrequencyScheduler([1.,2.]);s.next_action(0.);s.stop()
        self.assertIsNone(s.next_action(1.))
        with self.assertRaises(RuntimeError):s.register_acquisition('A',1.,1.)
    def test_validation_and_monotonic_decision_time(self):
        for frequencies in [[],[0.],[float('nan')],[1.,1.]]:
            with self.assertRaises(ValueError):FrequencyScheduler(frequencies)
        s=FrequencyScheduler([1.,2.]);s.next_action(2.)
        with self.assertRaises(ValueError):s.next_action(1.)
        with self.assertRaises(ValueError):FrequencyScheduler([1.],dwell_s=float('inf'))

if __name__=='__main__':unittest.main()
