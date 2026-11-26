"""Run the Step 6 forecast/alert calculations without Streamlit or PyArrow."""
from pathlib import Path
import sys
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics import load_tables
from forecasting import prepare_forecast, prepare_capacity_scenario, prepare_alerts

if __name__ == '__main__':
    t = load_tables(ROOT / 'warehouse_ops.db')
    ids = t['warehouses']['warehouse_id'].tolist()
    cutoff_day = t['inventory']['snapshot_at'].max().date()
    forecast = prepare_forecast(t, ids, cutoff_day, horizon=7)
    scenario = prepare_capacity_scenario(t, forecast)
    alerts = prepare_alerts(t, ids, forecast, scenario)
    print('\nStep 6: Synthetic warehouse forecasts (UTC)')
    print(f'Last complete training day: {forecast["last_complete_day"]:%Y-%m-%d}')
    print(forecast['totals'].to_string(index=False))
    print('\nWalk-forward backtest:')
    print(forecast['accuracy'].to_string(index=False))
    print('\nIllustrative labor scenario (first 6 lines):')
    print(scenario.head(6).to_string(index=False))
    print('\nRule-based watch categories:')
    print(alerts['category'].value_counts().to_string() if not alerts.empty else 'None')
    print('\nValidation calculations completed. This script did not change your database.')
