import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from short_boundary_scheduler import ShortBoundaryScheduler
class BoundaryTests(unittest.TestCase):
    def test_four_blocks_keep_channel_and_nominal_budget(self):
        s=ShortBoundaryScheduler([1.,2.]);a=[s.next_action(i*.175) for i in range(5)]
        self.assertEqual([x['frequency_hz'] for x in a],[1.,1.,1.,1.,2.]);self.assertEqual([x['retune'] for x in a],[True,False,False,False,True]);self.assertAlmostEqual(sum(x['dwell_s'] for x in a[:4]),.7);self.assertTrue(a[3]['boundary_visit_last'])
    def test_residual_block_preserves_budget(self):
        s=ShortBoundaryScheduler([1.,2.],boundary_s=.2);a=[s.next_action(i*.2) for i in range(4)];self.assertAlmostEqual(a[-1]['dwell_s'],.1);self.assertAlmostEqual(sum(x['dwell_s'] for x in a),.7)
    def test_delayed_qualified_feedback_can_interrupt_scan_at_boundary(self):
        s=ShortBoundaryScheduler([1.,2.]);s.next_action(0.);s.register_acquisition('a',1.,0.)
        for i in range(1,5):s.next_action(i*.175)
        self.assertEqual(s._current,2.)
        s.on_feedback('a',1.,1,observed_at=.175,now=.71)
        a=s.next_action(.72);self.assertEqual(a['frequency_hz'],1.);self.assertTrue(a['retune'])
    def test_expired_hold_does_not_interrupt(self):
        s=ShortBoundaryScheduler([1.,2.]);s.next_action(0.);s.register_acquisition('a',1.,0.)
        for i in range(1,5):s.next_action(i*.175)
        s.on_feedback('a',1.,1,observed_at=.175,now=2.);self.assertEqual(s.next_action(2.01)['frequency_hz'],2.)
    def test_stop_and_invalid_boundary(self):
        s=ShortBoundaryScheduler([1.]);s.stop();self.assertIsNone(s.next_action(1.))
        for v in [0.,.001,1.,float('nan')]:
            with self.assertRaises(ValueError):ShortBoundaryScheduler([1.],boundary_s=v)
if __name__=='__main__':unittest.main()
