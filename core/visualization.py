"""Conservative chart specifications from executed results, never model prose."""
import re
from decimal import Decimal, InvalidOperation
from datetime import date
import sqlglot
from sqlglot import exp

AMOUNTS = {'transaction_amount', 'available_balance', 'total_spend', 'avg_transaction', 'min_transaction', 'max_transaction', 'total_amount'}
CATEGORIES = {'bank_name','bank_code','counterparty_name','rail_type','transaction_type','reconciliation_proxy_status','masked_account_number','month','period','date','transaction_date','txn_month','txn_year'}

def number(value):
    if value is None or isinstance(value, bool): return None
    try:
        d = Decimal(str(value))
        return str(d) if d.is_finite() and abs(d) < Decimal('1e30') else None
    except (ValueError, TypeError, InvalidOperation): return None

def unit_for(column, expression):
    if re.search(r'(^|_)(id|reference|utr|number)(_|$)', column): return None
    if expression is None: return None
    sources = {c.name.lower() for c in expression.find_all(exp.Column)}
    # Date/year extraction and categorical codes must never become measures.
    if column in CATEGORIES or expression.find(exp.Year) or expression.find(exp.Month): return None
    if re.search(r'percent|pct', column) and expression.find(exp.Div): return 'percent'
    if expression.find(exp.Count): return 'count'
    if sources & AMOUNTS: return 'amount'
    if column == 'transaction_count': return 'count'
    return None

def build_visualization(result, sql):
    rows, columns = result.get('rows', []), result.get('columns', [])
    if not result.get('success') or not rows or len(rows)>120: return None
    if any(not isinstance(row,dict) for row in rows): return None
    try: tree = sqlglot.parse_one(sql, read='mysql')
    except Exception: return None
    if not isinstance(tree, exp.Select): return None
    projections = {p.alias_or_name.lower():p for p in tree.expressions}
    units = {c:unit_for(c.lower(),projections.get(c.lower())) for c in columns}
    measures = [c for c in columns if units[c] and all(row.get(c) is None or number(row[c]) is not None for row in rows)]
    if not measures: return None
    # Do not visualize detailed transaction records or numeric identifiers.
    if any(c in columns for c in ['transaction_id','transaction_reference_id','utr_number','description']): return None
    limited = tree.args.get('limit') is not None or result.get('row_count',len(rows)) > len(rows)
    notes = ['Uses the returned result only; currency is not specified in the schema.']
    if limited: notes.append('Query-limited result; not a complete portfolio view.')
    if 'reconciliation_proxy_status' in columns: notes.append('Reference availability is a reconciliation proxy, not confirmed accounting status.')
    if any(row.get(c) is None for row in rows for c in measures): notes.append('Missing values are unavailable, not zero.')
    series = [{'key':c,'label':c.replace('_',' '),'unit':units[c]} for c in measures]
    if len(rows)==1:
        # Hide a coalesced zero balance when its supporting account count is zero.
        account_counts = [c for c in measures if units[c]=='count' and 'account' in c.lower()]
        if account_counts and all(Decimal(number(rows[0][c]) or '0')==0 for c in account_counts): return None
        available = [s for s in series if number(rows[0].get(s['key'])) is not None]
        if not available: return None
        # Only explicit period-total aliases qualify for a period comparison.
        periods = [s for s in available if s['unit']=='amount' and re.fullmatch(r'(?:january|february|march|april|may|june|july|august|september|october|november|december|previous|current|baseline|comparison)_(?:total|amount|spend)',s['key'],re.I)]
        if len(periods)==2:
            return dict(kind='bar',title='Period comparison',category='period',series=[dict(key='value',label='Total',unit='amount')],rows=[{'period':s['label'],'value':number(rows[0][s['key']])} for s in periods],metrics=[dict(**s,value=number(rows[0].get(s['key']))) for s in available if s not in periods],notes=notes)
        return dict(kind='kpi',title='Result metrics',series=available,rows=[{s['key']:number(rows[0][s['key']]) for s in available}],metrics=[],notes=notes)
    categories = [c for c in columns if c.lower() in CATEGORIES and c not in measures]
    if len(categories)!=1: return None  # multiple dimensions need an explicit pivot
    category=categories[0]
    if any(row.get(category) is None for row in rows): return None
    labels=[str(row[category]) for row in rows]
    if len(set(labels))!=len(labels): return None
    temporal=all(re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',label) for label in labels)
    if temporal:
        try:
            for label in labels: date.fromisoformat(label+'-01' if len(label)==7 else label)
        except ValueError: return None
        rows=sorted(rows,key=lambda row:str(row[category]))
        notes.append('Returned dates only; gaps and partial periods are not filled or inferred.')
    elif len(rows)>20:
        notes.append(f'Showing first 20 of {len(rows)} returned categories in query order; see the table for all rows.')
        rows=rows[:20]
    return dict(kind='line' if temporal else 'bar',title='Trend' if temporal else 'Category comparison',category=category,series=series,rows=[{category:str(row[category]),**{c:number(row.get(c)) for c in measures}} for row in rows],metrics=[],notes=notes)
