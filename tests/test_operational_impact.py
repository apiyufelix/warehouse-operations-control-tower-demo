"""Step 17 estimates are scenario based, safe to deploy, and not shipment claims."""
import ast
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labor_balancing import analyze_labor_balance, prepare_labor_comparison
from operational_impact import prepare_operational_impact


@pytest.fixture
def data():
    return pd.read_csv(ROOT / 'data' / 'labor_balance_scenarios.csv')


def test_zero_setup_matches_frictionsless_step16_capacity(data):
    analysis = analyze_labor_balance(data)
    old = prepare_labor_comparison(analysis)['metrics']
    impact = prepare_operational_impact(analysis, transfer_setup_minutes=0)['metrics']
    assert impact['stage_work_covered_before'] == pytest.approx(old['stage_work_covered_before'])
    assert impact['stage_work_covered_after'] == pytest.approx(old['stage_work_covered_after'])
    assert impact['total_transferred_employees'] == 14


def test_positive_setup_reduces_net_estimated_coverage(data):
    analysis = analyze_labor_balance(data)
    immediate = prepare_operational_impact(analysis, transfer_setup_minutes=0)
    delayed = prepare_operational_impact(analysis, transfer_setup_minutes=30)
    assert delayed['metrics']['additional_stage_work_covered'] < immediate['metrics']['additional_stage_work_covered']
    assert delayed['metrics']['uncovered_stage_work_after'] > immediate['metrics']['uncovered_stage_work_after']
    assert delayed['metrics']['at_risk_stages_after'] <= delayed['metrics']['at_risk_stages_before']
    assert delayed['metrics']['stage_work_covered_after'] <= delayed['metrics']['stage_work_units']
    assert delayed['metrics']['additional_stage_work_covered'] >= -1e-8
    assert delayed['stages']['stage_work_covered_after'].le(delayed['stages']['remaining_stage_units'] + 1e-8).all()


def test_ranked_marginal_gains_do_not_double_count(data):
    out = prepare_operational_impact(analyze_labor_balance(data))
    moves = out['transfers_ranked']
    assert len(moves) == 5
    assert list(moves['priority_rank']) == list(range(1, len(moves) + 1))
    assert moves['gain_per_transferred_employee'].is_monotonic_decreasing
    assert (moves['marginal_stage_work_gain'] >= 0).all()
    assert moves['marginal_stage_work_gain'].sum() == pytest.approx(
        out['metrics']['additional_stage_work_covered'], abs=1e-7)


def test_no_transfers_returns_empty_ranking_and_zero_change(data):
    subset = data.query("warehouse_id == 'WH_NC_02' and shift_name == 'Shift C (16–24 UTC)'")
    assert analyze_labor_balance(subset)['transfers'].empty
    impact = prepare_operational_impact(analyze_labor_balance(subset))
    assert impact['transfers_ranked'].empty
    assert impact['metrics']['additional_stage_work_covered'] == pytest.approx(0)
    assert (impact['stages']['staff_before'] == impact['stages']['staff_after']).all()


def test_clearance_and_load_handle_no_staff_and_no_work(data):
    subset = data.head(3).copy()
    subset.loc[subset['process_name'] == 'Picking', 'staff_on_duty'] = 0
    subset.loc[subset['process_name'] == 'Picking', 'minimum_staff'] = 0
    result = prepare_operational_impact(analyze_labor_balance(subset))
    pick = result['stages'].set_index('process_name').loc['Picking']
    assert pd.isna(pick['estimated_clear_hours_before'])
    assert pd.isna(pick['capacity_load_pct_before'])
    assert bool(pick['stage_at_risk_before'])
    subset['remaining_work_units'] = 0
    z = prepare_operational_impact(analyze_labor_balance(subset))
    assert z['metrics']['covered_pct_before'] is None
    assert z['metrics']['covered_pct_after'] is None
    assert z['metrics']['at_risk_stages_before'] == 0
    assert (z['stages']['estimated_clear_hours_before'] == 0).all()


def test_setup_longer_than_shift_prevents_incoming_contribution(data):
    shift = data.query("warehouse_id == 'WH_NC_01' and shift_name == 'Shift A (00–08 UTC)'")
    before = prepare_operational_impact(analyze_labor_balance(shift), transfer_setup_minutes=0)
    after = prepare_operational_impact(analyze_labor_balance(shift), transfer_setup_minutes=120)
    recv_before = before['stages'].set_index('process_name').loc['Packing']
    recv_after = after['stages'].set_index('process_name').loc['Packing']
    assert recv_after['capacity_after'] < recv_before['capacity_after']
    assert recv_after['estimated_clear_hours_after'] > recv_before['estimated_clear_hours_after']
    # Set the entire 2-hour shift as setup, so no inbound worker can help.
    short_shift = analyze_labor_balance(shift, hours_remaining=2)
    stretched = prepare_operational_impact(short_shift, transfer_setup_minutes=120)
    for _, row in stretched['stages'].iterrows():
        if row['incoming_transfers']:
            assert row['capacity_after'] == pytest.approx(row['capacity_before'])


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), 241, 'no'])
def test_rejects_bad_setup_inputs(data, value):
    with pytest.raises(ValueError):
        prepare_operational_impact(analyze_labor_balance(data.head(3)), transfer_setup_minutes=value)


def test_rejects_inconsistent_movement(data):
    outcome = analyze_labor_balance(data.head(3))
    outcome['departments'].loc[outcome['departments'].index[0], 'staff_after'] += 1
    with pytest.raises(ValueError, match='does not match'):
        prepare_operational_impact(outcome)


def test_public_ui_is_read_only_and_has_impact_visuals():
    code = (ROOT / 'app.py').read_text(encoding='utf8')
    ast.parse(code)
    assert 'prepare_operational_impact(' in code
    assert 'Additional stage work coverable' in code
    assert 'Estimated hours to clear remaining stage work' in code
    assert 'gain_per_transferred_employee' in code
    assert 'st.data_editor' not in code
    assert 'file_uploader' not in code
    assert 'from operational_impact import prepare_operational_impact' in code
