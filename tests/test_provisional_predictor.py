import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from frequency_predictor_shadow import ShadowPredictor
from test_frequency_predictor_shadow import acquisition,decode,ANCHOR
class ProvisionalTests(unittest.TestCase):
    def test_predicts_before_any_crc_and_without_inventing_serial(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);d=decode();p.observe_qualified_burst(d['burst'],110.25)
        self.assertEqual(len(p.predictions),10);self.assertEqual(len(p.frames),0);self.assertTrue(all(x['serial'] is None for x in p.predictions));self.assertTrue(all(x['availability']=='available_before_window' for x in p.predictions))
    def test_confirmation_does_not_retime_or_duplicate_provisional(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);d=decode(complete=112.);p.observe_qualified_burst(d['burst'],110.25);created=[x['created_host_s'] for x in p.predictions];p.observe_decode(d);p.observe_qualified_burst(d['burst'],113.)
        provisional=[x for x in p.predictions if x['kind'].startswith('provisional')];self.assertEqual(len(provisional),10);self.assertEqual([x['created_host_s'] for x in provisional],created);self.assertEqual(p.confirmations['b']['crc_status'],'passed');self.assertEqual(p.confirmations['b']['serial'],'X')
    def test_crc_failed_keeps_detector_evidence_and_marks_confirmation(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);d=decode(crc=False);p.observe_qualified_burst(d['burst'],110.25);p.observe_decode(d)
        self.assertEqual(len(p.predictions),10);self.assertEqual(len(p.frames),0);self.assertEqual(p.summarize()['provisional_confirmations'],{'failed':1});self.assertEqual(p.summarize()['provisional_pending'],0)
    def test_rx_error_never_seeds(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(errors=[{'code':'overflow'}]),ANCHOR);p.observe_qualified_burst(decode()['burst'],110.25);self.assertEqual(len(p.predictions),0)
    def test_historical_unknown_availability_is_preserved(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);p.observe_qualified_burst(decode()['burst'],None);self.assertTrue(all(x['availability']=='unknown_without_availability_timestamp' for x in p.predictions))
if __name__=='__main__':unittest.main()
