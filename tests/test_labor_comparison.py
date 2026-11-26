"""Before-after planning estimates are descriptive, read-only, and internally consistent."""
import ast
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labor_balancing import analyze_labor_balance, prepare_labor_comparison


@pytest.fixture
def scenario():
    return pd.read_csv(ROOT / 'data' / 'labor_balance_scenarios.csv')


def test_before_after_preserves_headcount_and_improves_coverage(scenario):
    outcome = analyze_labor_balance(scenario)
    comparison = prepare_labor_comparison(outcome)
    m = comparison['metrics']
    assert m['headcount_before'] == m['headcount_after']
    assert m['stage_work_at_risk_after'] <= m['stage_work_at_risk_before']
    assert m['stage_work_covered_after'] >= m['stage_work_covered_before']
    assert m['stage_work_gap_reduced'] == pytest.approx(
        m['stage_work_at_risk_before'] - m['stage_work_at_risk_after'])
    assert m['coverage_pct_after'] >= m['coverage_pct_before']
    assert (comparison['departments']['staff_change'].sum() == 0)
    assert m['shortage_after'] <= m['shortage_before']


def test_coverage_within_bounds_and_not_double_counted_as_orders(scenario):
    outcome = analyze_labor_balance(scenario)
    c = prepare_labor_comparison(outcome)
    assert c['metrics']['total_stage_work_units'] == scenario['remaining_work_units'].sum()
    for period in ('before', 'after'):
        assert c['departments'][f'coverage_pct_{period}'].between(0, 100).all()
    assert c['metrics']['stage_work_covered_after'] <= c['metrics']['total_stage_work_units']


def test_custom_shift_rates_change_estimated_coverage(scenario):
    subset = scenario.query("warehouse_id == 'WH_NC_01'").head(3).copy()
    baseline = prepare_labor_comparison(analyze_labor_balance(subset))['metrics']
    alternative = prepare_labor_comparison(analyze_labor_balance(
        subset, rates={'Picking': 4.0, 'Packing': 5.0, 'Dispatch': 6.0}
    ))['metrics']
    assert baseline['coverage_pct_before'] != alternative['coverage_pct_before']


def test_no_demand_coverage_is_not_misleading(scenario):
    subset = scenario.head(3).copy()
    subset['remaining_work_units'] = 0
    m = prepare_labor_comparison(analyze_labor_balance(subset))['metrics']
    assert m['coverage_pct_before'] is None
    assert m['coverage_pct_after'] is None
    assert m['stage_work_gap_reduced'] == 0


def test_invalid_or_modified_results_raise(scenario):
    a = analyze_labor_balance(scenario)
    broken = {'departments': a['departments'].drop(columns=['staff_after'])}
    with pytest.raises(ValueError, match='Missing comparison'):
        prepare_labor_comparison(broken)
    a['departments'].loc[a['departments'].index[0], 'staff_after'] += 1
    with pytest.raises(ValueError, match='preserve total headcount'):
        prepare_labor_comparison(a)


def test_public_streamlit_uses_safe_comparison_and_no_data_editor():
    s = (ROOT / 'app.py').read_text(encoding='utf-8')
    ast.parse(s)
    assert 'prepare_labor_comparison(base)' in s
    assert 'prepare_labor_comparison(modeled)' in s
    assert 'Staff allocation by department' in s
    assert 'Expected stage-work coverage' in s
    assert 'st.data_editor' not in s
    assert 'file_uploader' not in s
