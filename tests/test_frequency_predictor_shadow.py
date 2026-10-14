import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'receiver'))
from frequency_predictor_shadow import ShadowPredictor

def acquisition(id='a',first=10.,freq=2414.5e6,count=2000,errors=None,session='s'):
    return dict(acquisition_id=id,session_id=session,first_sample_device_time_s=first,sample_rate_actual_sps=1000.,sample_count=count,rx_errors=errors or [],center_requested_hz=freq)
def decode(bid='b',aid='a',sample=100,complete=110.2,crc=True,serial='X'):
    return dict(record_type='decode',burst=dict(burst_id=bid,acquisition_id=aid,burst_range=dict(start_sample=sample,end_sample=sample+1)),completed_monotonic_s=complete,result=dict(crc_valid=crc,payload_parsed=True,decode_result=dict(payload=dict(serial_number=serial,sequence_number=1))))
ANCHOR=dict(host_before_s=110.,host_after_s=110.002,device_time_s=10.)
class ShadowTests(unittest.TestCase):
    def test_device_frame_time_and_available_predictions(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);p.observe_decode(decode());self.assertAlmostEqual(p.frames[0]['device_start_s'],10.1);self.assertTrue(all(x['availability']=='available_before_window' for x in p.predictions))
    def test_missing_anchor_is_not_assumed_actionable(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition());p.observe_decode(decode());self.assertTrue(all(x['availability']=='unknown_without_anchor' for x in p.predictions))
    def test_late_result_is_labeled(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR);p.observe_decode(decode(complete=112.));self.assertTrue(all(x['availability']=='late' for x in p.predictions))
    def test_crc_failure_and_rx_errors_do_not_seed(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition());p.observe_decode(decode(crc=False));p.observe_acquisition(acquisition('bad',errors=[{'code':'overflow'}]));p.observe_decode(decode('bad','bad'));self.assertEqual(len(p.predictions),0)
    def test_wrong_channel_is_unobserved_not_missed(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(count=200));p.observe_decode(decode());p.observe_acquisition(acquisition('other',first=10.4,freq=5776.5e6));g=p.summarize()['groups']['same_channel_burst_clock:0.5:h1'];self.assertEqual(g['unobserved'],1);self.assertEqual(g['fully_observed_no_crc_match'],0)
    def test_overlap_coverage_not_double_counted_and_match(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition());p.observe_decode(decode());p.observe_acquisition(acquisition('overlap',first=10.5));p.observe_decode(decode('b2','overlap',sample=100));g=p.summarize()['groups']['same_channel_burst_clock:0.5:h1'];self.assertGreaterEqual(g['fully_observed_crc_match'],1)
    def test_device_reset_separates_coverage_domains(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(count=200));p.observe_decode(decode());p.observe_acquisition(acquisition('reset',first=0.,count=20000));g=p.summarize()['groups']['same_channel_burst_clock:0.5:h1'];self.assertEqual(g['unobserved'],1)
    def test_partial_jsonl_and_duplicate_decode_do_not_double_seed(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition());raw=json.dumps(decode())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'decoded';path.write_text(raw[:30]);p.poll_decode_file(path);self.assertFalse(p.predictions)
            with path.open('a') as f:f.write(raw[30:]+'\n'+raw+'\n')
            p.poll_decode_file(path);self.assertEqual(len(p.frames),1);self.assertEqual(len(p.predictions),10)
    def test_tune_deadlines_are_diagnostic_and_use_tune_clock(self):
        p=ShadowPredictor();p.observe_acquisition(acquisition(),ANCHOR,dict(host_start_s=109.9,host_end_s=109.902));p.observe_decode(decode());pred=[x for x in p.predictions if x['kind']=='same_channel_tune_clock_diagnostic'];self.assertEqual(len(pred),2);self.assertAlmostEqual(pred[0]['target_device_s'],10.48)
if __name__=='__main__':unittest.main()
