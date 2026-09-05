import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from api import main
from api.models import QueryRequest, QueryResponse, QueryResultData
from core import analytics

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = analytics.DB_PATH
        analytics.DB_PATH = Path(self.tmp.name) / 'events.sqlite3'
    def tearDown(self):
        analytics.DB_PATH = self.old
        self.tmp.cleanup()
    def run_query(self, response=None, failure=False, dry=False):
        async def fake(request):
            await asyncio.sleep(.01)
            if failure: raise HTTPException(500, 'test')
            return response
        with patch.object(main, '_execute_query', fake):
            return asyncio.run(main.query(QueryRequest(query='private question',session_id='s1',dry_run=dry)))
    def test_persists_full_timing_without_content(self):
        result=self.run_query(QueryResponse(user_query='test', total_time_ms=0, prompt_tokens=12, completion_tokens=3,
            grounding_status='passed',query_result=QueryResultData(success=True,row_count=1)))
        rows=analytics.read()['requests']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['outcome'],'answered')
        self.assertEqual(rows[0]['input_tokens'],12)
        self.assertGreater(result.total_time_ms,8)
        self.assertNotIn('private question',str(rows))
    def test_failures_and_dry_runs(self):
        with self.assertRaises(HTTPException): self.run_query(failure=True)
        self.run_query(QueryResponse(user_query='test', ),dry=True)
        rows=analytics.read()['requests']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['outcome'],'technical_failure')
        self.assertIsNone(rows[0]['input_tokens'])
    def test_greeting_and_storage_failure(self):
        self.run_query(QueryResponse(user_query='test', direct_response_kind='greeting'))
        self.assertEqual(analytics.read()['requests'][0]['model'],'No model call')
        with self.assertLogs(main.logger, level='ERROR'), patch.object(analytics,'record',side_effect=OSError('test')):
            self.assertIsNotNone(self.run_query(QueryResponse(user_query='test', )))
    def test_period_validation(self):
        with self.assertRaises(HTTPException): asyncio.run(main.analytics_data(999))
        self.assertEqual(asyncio.run(main.analytics_data(7))['requests'],[])

if __name__=='__main__': unittest.main()
