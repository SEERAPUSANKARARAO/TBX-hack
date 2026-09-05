import unittest
from unittest.mock import patch
from core.response_synthesizer import _collect_allowed_numbers, _verify_numbers_grounded, synthesize_response
from core.confidence_scorer import compute_confidence

class ConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.result=dict(success=True,row_count=1,columns=['july_total','july_count','absolute_change','percent_change'],rows=[dict(july_total='3024311.85',july_count=69,absolute_change='-199475.95',percent_change='-6.595747')])
        self.allowed=_collect_allowed_numbers(self.result)
    def test_equivalent_decrease_and_rounding(self):
        for text in ['Change: -6.60%.', 'Spending decreased by 6.60%.','Spending fell by 199,475.95.', 'Change: −6.595747%.', 'July total: 3,024,311.85 across 69 transactions.']:
            with self.subTest(text=text):self.assertTrue(_verify_numbers_grounded(text,self.allowed))
    def test_wrong_sign_precision_and_missing_value(self):
        for text in ['Spending increased by 6.60%.','Change: 6.60%.','Change: -6.51%.','Change: -7%.','Spending decreased by -6.60%.','August total: 2,824,835.90.','There were 68 transactions.','Change: 69%.','Amount: 2026.']:
            with self.subTest(text=text):self.assertFalse(_verify_numbers_grounded(text,self.allowed))
    def test_safe_fallback_does_not_lower_query_checks(self):
        baseline=compute_confidence(True,True,1,grounding_status='passed')
        fallback=compute_confidence(True,True,1,numbers_grounded=False,grounding_status='fallback')
        self.assertEqual(baseline.score,fallback.score)
        self.assertEqual(fallback.level,'HIGH')
        self.assertTrue(any('replaced' in r for r in fallback.reasons))
    def test_query_failures_still_low_and_retries_penalized(self):
        self.assertEqual(compute_confidence(False,False,0,grounding_status='fallback').level,'LOW')
        self.assertEqual(compute_confidence(True,True,1,clarification_needed='Which bank?').level,'LOW')
        self.assertLess(compute_confidence(True,True,1,retries=2).score,85)
        self.assertLessEqual(compute_confidence(True,True,0,retries=5).score,compute_confidence(True,True,1,retries=5).score)
    def test_four_column_fallback_includes_actual_values(self):
        with patch('core.response_synthesizer._call_llm',return_value=('Invented 999.99',{})):
            text,ok,usage=synthesize_response('Compare July','',self.result)
        self.assertFalse(ok)
        self.assertEqual(usage['grounding_status'],'fallback')
        for v in ['3024311.85','69','-199475.95','-6.595747']:self.assertIn(v,text)
        self.assertNotIn('999.99',text)
    def test_provider_failure_is_not_marked_verified(self):
        with patch('core.response_synthesizer._call_llm',side_effect=RuntimeError('offline')):
            text,ok,usage=synthesize_response('Compare July','',self.result)
        self.assertEqual(usage['grounding_status'],'template')
    def test_empty_result_not_evaluated(self):
        _,_,usage=synthesize_response('anything','',dict(success=True,row_count=0,rows=[]))
        self.assertEqual(usage['grounding_status'],'not_evaluated')

if __name__=='__main__':unittest.main()
