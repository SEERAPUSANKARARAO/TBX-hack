import unittest
from core.visualization import build_visualization

def chart(sql,rows):return build_visualization(dict(success=True,rows=rows,columns=list(rows[0]) if rows else [],row_count=len(rows)),sql)
class ChartTests(unittest.TestCase):
    def test_period_comparison_uses_only_returned_values(self):
        sql='SELECT SUM(transaction_amount) AS july_total, SUM(transaction_amount) AS august_total, SUM(transaction_amount)-SUM(transaction_amount) AS absolute_change, SUM(transaction_amount)/NULLIF(SUM(transaction_amount),0)*100 AS percent_change FROM transaction'
        spec=chart(sql,[dict(july_total='3024311.85',august_total='2824835.90',absolute_change='-199475.95',percent_change='-6.595747')])
        self.assertEqual(spec['kind'],'bar');self.assertEqual([r['value'] for r in spec['rows']],['3024311.85','2824835.90'])
        self.assertEqual(spec['metrics'][-1]['unit'],'percent')
        missing=chart('SELECT SUM(transaction_amount) AS july_total, COUNT(*) AS july_count FROM transaction',[dict(july_total='3024311.85',july_count=69)])
        self.assertEqual(missing['kind'],'kpi');self.assertNotIn('august',str(missing))
    def test_signed_balances(self):
        c=chart('SELECT bank_code, SUM(available_balance) AS balance FROM account GROUP BY bank_code',[dict(bank_code='A',balance='-100.05'),dict(bank_code='B',balance='200')])
        self.assertEqual(c['kind'],'bar');self.assertEqual(c['rows'][0]['balance'],'-100.05')
    def test_dates_sort_and_null_preserved(self):
        c=chart("SELECT DATE_FORMAT(transaction_date,'%Y-%m') AS period, SUM(transaction_amount) AS total FROM transaction GROUP BY period",[dict(period='2026-08',total=None),dict(period='2026-07',total='10')])
        self.assertEqual(c['kind'],'line');self.assertEqual(c['rows'][0]['period'],'2026-07');self.assertIsNone(c['rows'][1]['total'])
    def test_missing_accounts_no_fake_zero(self):
        self.assertIsNone(chart('SELECT SUM(available_balance) AS balance, COUNT(account_id) AS account_count FROM account',[dict(balance=0,account_count=0)]))
        self.assertIsNone(chart('SELECT SUM(available_balance) AS balance FROM account',[dict(balance=None)]))
    def test_no_identifier_or_detail_chart(self):
        self.assertIsNone(chart('SELECT transaction_id, transaction_amount FROM transaction',[dict(transaction_id='x',transaction_amount=10),dict(transaction_id='y',transaction_amount=20)]))
        self.assertIsNone(chart('SELECT account_id FROM account',[dict(account_id='123')]))
    def test_limit_and_units(self):
        c=chart('SELECT bank_code, SUM(transaction_amount) AS total, COUNT(*) AS count FROM transaction GROUP BY bank_code LIMIT 2',[dict(bank_code='A',total=10,count=1),dict(bank_code='B',total=20,count=2)])
        self.assertEqual([s['unit'] for s in c['series']],['amount','count']);self.assertTrue(any('limited' in n for n in c['notes']))
    def test_unknown_duplicate_and_empty(self):
        self.assertIsNone(chart('SELECT 45 AS random',[dict(random=45)]))
        self.assertIsNone(chart('SELECT bank_code,SUM(transaction_amount) AS total FROM transaction GROUP BY bank_code',[dict(bank_code='A',total=10),dict(bank_code='A',total=20)]))
        self.assertIsNone(chart('SELECT * FROM transaction',[]))

if __name__=='__main__':unittest.main()
