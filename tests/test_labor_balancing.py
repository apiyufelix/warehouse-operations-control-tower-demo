"""Staff redistribution invariants for synthetic cross-training scenario."""
import ast
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labor_balancing import analyze_labor_balance


@pytest.fixture
def input_scenario():
    return pd.read_csv(ROOT / 'data' / 'labor_balance_scenarios.csv')


def test_synthetic_default_has_explicit_suggested_moves(input_scenario):
    result = analyze_labor_balance(input_scenario)
    assert len(result['summary']) == 6
    assert int(result['transfers']['employees_to_transfer'].sum()) == 14
    a = result['transfers'].query("warehouse_id == 'WH_NC_01' and shift_name == 'Shift A (00–08 UTC)'")
    assert len(a) == 1
    assert (a.iloc[0]['from_department'], a.iloc[0]['to_department'], int(a.iloc[0]['employees_to_transfer'])) == ('Picking', 'Packing', 3)


def test_headcount_conserved_and_minimum_coverage_respected(input_scenario):
    results = analyze_labor_balance(input_scenario)
    d = results['departments']
    totals = d.groupby(['warehouse_id', 'shift_name'])[['staff_on_duty', 'staff_after']].sum()
    assert (totals.staff_on_duty == totals.staff_after).all()
    assert (d.staff_after >= 0).all()
    donors = d[d.staff_after < d.staff_on_duty]
    assert (donors.staff_after >= donors.staff_needed).all()
    assert (donors.staff_after >= donors.minimum_staff).all()
    assert (d.work_units_at_risk_after <= d.work_units_at_risk_before + 1e-8).all()
    assert results['summary'].stage_work_risk_reduction.ge(-1e-8).all()


def test_no_cross_shift_or_cross_warehouse_moves(input_scenario):
    results = analyze_labor_balance(input_scenario)
    d = results['departments']
    for move in results['transfers'].itertuples(index=False):
        assert (d['warehouse_id'].eq(move.warehouse_id) & d['shift_name'].eq(move.shift_name)
                & d['process_name'].eq(move.from_department)).any()
        assert (d['warehouse_id'].eq(move.warehouse_id) & d['shift_name'].eq(move.shift_name)
                & d['process_name'].eq(move.to_department)).any()


def test_no_wasteful_transfer_when_there_is_no_deficit(input_scenario):
    subset = input_scenario[input_scenario.warehouse_id.eq('WH_NC_02') & input_scenario.shift_name.eq('Shift C (16–24 UTC)')]
    results = analyze_labor_balance(subset)
    assert results['transfers'].empty
    assert results['summary'].iloc[0].alert == 'UNALLOCATED STAFF — NO SHORTAGE'


def test_shortage_remains_when_donor_cannot_supply_enough(input_scenario):
    subset = input_scenario[input_scenario.warehouse_id.eq('WH_NC_01') & input_scenario.shift_name.eq('Shift A (00–08 UTC)')]
    results = analyze_labor_balance(subset)
    assert int(results['summary'].iloc[0].shortage_after) == 1


def test_reacts_to_manager_changed_headcount(input_scenario):
    subset = input_scenario[input_scenario.warehouse_id.eq('WH_NC_01') & input_scenario.shift_name.eq('Shift A (00–08 UTC)')].copy()
    subset.loc[subset.process_name.eq('Packing'), 'staff_on_duty'] = 7
    results = analyze_labor_balance(subset)
    assert results['transfers'].empty
    assert int(results['summary'].iloc[0].shortage_after) == 0


@pytest.mark.parametrize('column,value', [('staff_on_duty', -1), ('remaining_work_units', -3), ('minimum_staff', 1.2)])
def test_rejects_invalid_input_values(input_scenario, column, value):
    subset = input_scenario.head(3).copy()
    subset[column] = subset[column].astype(float)
    subset.loc[subset.index[0], column] = value
    with pytest.raises(ValueError):
        analyze_labor_balance(subset)


def test_rejects_missing_rate_and_duplicates(input_scenario):
    subset = input_scenario.head(3).copy()
    with pytest.raises(ValueError):
        analyze_labor_balance(pd.concat([subset, subset.iloc[[0]]]))
    with pytest.raises(ValueError):
        analyze_labor_balance(subset, rates={'Picking': 5.0})


def test_public_app_has_ninth_read_only_tab():
    text = (ROOT/'app.py').read_text(encoding='utf-8')
    ast.parse(text)
    assert '⚖️ Labor balancing' in text
    assert 'from labor_balancing import analyze_labor_balance' in text
    assert 'st.data_editor' not in text
    assert 'file_uploader' not in text
    assert (ROOT/'data'/'labor_balance_scenarios.csv').exists()
