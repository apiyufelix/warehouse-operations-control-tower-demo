"""Step 8: Executive performance rollup and printable management briefing.

The entire app uses synthetic data and UTC event windows. The executive rollup
reuses source metrics; operational snapshots and workflow cases are deliberately
kept separate from time-window event measures.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd

from analytics import prepare_dashboard
from forecasting import prepare_forecast
from productivity import PROCESSES, REFERENCE_UNITS_PER_HOUR, prepare_productivity

# Editable demonstration *reference points*; not employer KPIs, SLA contracts, or standards.
DEMO_REFERENCES = {"on_time_shipping_pct": 95.0, "qc_defect_rate_pct": 1.0,
                   "picking_units_per_hour": 6.5}


def _percent(value: float | None, places: int = 1) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:.{places}f}%"


def _display_number(value: float | None, places: int = 0) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:,.{places}f}"


def _difference(curr, prior):
    return None if curr is None or prior is None or pd.isna(curr) or pd.isna(prior) else float(curr - prior)


def prepare_executive(
    tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
    start_date: date, end_date: date, *,
    targets: dict[str, float] | None = None,
    workflow_cases: pd.DataFrame | None = None,
) -> dict:
    """Create an executive brief without updating the underlying database.

    Prior-period KPI comparisons use the immediately preceding window with the
    *same number of calendar days*. Do not calculate a comparison if the entire
    preceding window isn't present in the dataset. Stock is latest-only. Case
    status is latest-only and should only be supplied at the latest snapshot.
    """
    if not warehouse_ids or start_date > end_date:
        raise ValueError("Select warehouses and a valid date range")
    selected = tables["warehouses"]["warehouse_id"].isin(warehouse_ids)
    if not selected.any():
        raise ValueError("Unknown warehouse selection")
    targets = dict(DEMO_REFERENCES if targets is None else targets)
    if not 0 <= targets["on_time_shipping_pct"] <= 100:
        raise ValueError("On-time reference must be between 0 and 100")
    if not 0 <= targets["qc_defect_rate_pct"] <= 100 or targets["picking_units_per_hour"] <= 0:
        raise ValueError("QC and productivity references are invalid")

    current = prepare_dashboard(tables, warehouse_ids, start_date, end_date)
    p_current = prepare_productivity(tables, warehouse_ids, start_date, end_date)
    span = (end_date - start_date).days + 1
    prior_end = start_date - timedelta(days=1)
    prior_start = start_date - timedelta(days=span)
    history_begin = tables["orders"]["created_at"].min().date()
    has_full_prior = prior_start >= history_begin
    previous = prepare_dashboard(tables, warehouse_ids, prior_start, prior_end) if has_full_prior else None
    p_previous = prepare_productivity(tables, warehouse_ids, prior_start, prior_end) if has_full_prior else None

    cur = current["metrics"]
    old = previous["metrics"] if previous else {}
    cur_rate = p_current["metrics"]["units_per_labor_hour"]
    old_rate = p_previous["metrics"]["units_per_labor_hour"] if p_previous else None
    rows = [
        ("Orders received", cur["orders_received"], old.get("orders_received"), "count", "higher is volume, not necessarily better"),
        ("Orders shipped", cur["orders_shipped"], old.get("orders_shipped"), "count", "higher is throughput, not necessarily better"),
        ("On-time shipping", cur["on_time_shipping_pct"], old.get("on_time_shipping_pct"), "percentage points", "higher is better"),
        ("QC defect rate", cur["qc_defect_rate_pct"], old.get("qc_defect_rate_pct"), "percentage points", "lower is better"),
        ("Picking units/labor h", cur_rate, old_rate, "rate", "higher is better"),
        ("Open backlog (as-of)", cur["open_backlog_asof"], old.get("open_backlog_asof"), "orders", "lower is better"),
        ("Overdue open (as-of)", cur["overdue_open_asof"], old.get("overdue_open_asof"), "orders", "lower is better"),
    ]
    comparison = pd.DataFrame([{
        "KPI": name, "Current": now, "Prior": before,
        "Change": _difference(now, before), "Change unit": unit, "Interpretation": meaning,
    } for name, now, before, unit, meaning in rows])

    warehouse_rows = []
    selected_warehouses = tables["warehouses"].loc[selected, ["warehouse_id", "warehouse_name"]]
    for r in selected_warehouses.itertuples(index=False):
        wid = r.warehouse_id
        # Independently scoped KPIs guarantee totals reconcile without order-line fanout.
        r_dash = prepare_dashboard(tables, [wid], start_date, end_date)["metrics"]
        r_prod = prepare_productivity(tables, [wid], start_date, end_date)
        warehouse_rows.append({
            "Warehouse": r.warehouse_name,
            "Received": r_dash["orders_received"],
            "Shipped": r_dash["orders_shipped"],
            "On-time %": r_dash["on_time_shipping_pct"],
            "Overdue open": r_dash["overdue_open_asof"],
            "Backlog open": r_dash["open_backlog_asof"],
            "QC defect %": r_dash["qc_defect_rate_pct"],
            "Picking units/labor h": r_prod["metrics"]["units_per_labor_hour"],
        })
    warehouse_view = pd.DataFrame(warehouse_rows)

    process_view = p_current["by_process"][[
        "process_name", "units_processed", "actual_labor_hours",
        "units_per_labor_hour", "p90_queue_hours", "target_attainment_pct",
    ]].rename(columns={
        "process_name": "Process", "units_processed": "Stage units handled",
        "actual_labor_hours": "Labor hours", "units_per_labor_hour": "Units/labor h",
        "p90_queue_hours": "P90 queue wait (h)", "target_attainment_pct": "Demo reference attainment %",
    }).copy()

    # Reuse forecast engine, without interpreting any resulting values as guarantees.
    try:
        forecast = prepare_forecast(tables, warehouse_ids, end_date, horizon=7)
    except ValueError as exc:
        # Early historical windows may have fewer than the 70 complete days
        # required by the existing backtest. Do not fabricate projections.
        if "70 complete calendar days" not in str(exc) and "No sufficient historical" not in str(exc):
            raise
        forecast = None
    forecast7 = int(forecast["totals"]["forecast_orders"].sum()) if forecast is not None else None
    forecast_mae = float(forecast["accuracy"]["mae_orders"].mean()) if forecast is not None else None
    forecast_daily = (forecast["totals"] if forecast is not None else
                      pd.DataFrame(columns=["date", "forecast_orders", "lower_orders", "upper_orders"]))

    source_overdue = int(cur["overdue_open_asof"])
    cases_summary = None
    latest_day = tables["inventory"]["snapshot_at"].max().date()
    if workflow_cases is not None and end_date == latest_day:
        active_ids = set(current["exceptions"]["order_id"])
        active = workflow_cases.loc[workflow_cases["order_id"].isin(active_ids)
                                    & workflow_cases["currently_overdue"].eq(1)].copy()
        cases_summary = {
            "tracked_active": int(len(active)),
            "unassigned": int(active["assigned_owner"].astype(str).str.strip().eq("").sum()),
            "unresolved": int(active["workflow_status"].ne("Resolved").sum()),
            "critical": int(active["severity"].eq("Critical").sum()),
            "resolved_but_still_overdue": int(active["workflow_status"].eq("Resolved").sum()),
        }

    priorities = []
    if source_overdue:
        priorities.append({"Urgency": "High", "Management review": "Reduce past-due shipment backlog",
                           "Evidence": f"{source_overdue:,} unshipped orders are past promise at the selected cutoff.",
                           "Suggested next step": "Assign owners; confirm allocation, QC release and dispatch readiness."})
    if cur["on_time_shipping_pct"] is not None and cur["on_time_shipping_pct"] < targets["on_time_shipping_pct"]:
        priorities.append({"Urgency": "High", "Management review": "Investigate on-time performance",
                           "Evidence": f"{_percent(cur['on_time_shipping_pct'])} vs illustrative {_percent(targets['on_time_shipping_pct'])} reference.",
                           "Suggested next step": "Break down due-date failures by warehouse, shift and process."})
    if cur["qc_defect_rate_pct"] is not None and cur["qc_defect_rate_pct"] > targets["qc_defect_rate_pct"]:
        priorities.append({"Urgency": "Medium", "Management review": "Review quality defects",
                           "Evidence": f"{_percent(cur['qc_defect_rate_pct'], 2)} vs illustrative {_percent(targets['qc_defect_rate_pct'], 2)} reference.",
                           "Suggested next step": "Inspect defect mix and document corrective actions."})
    if cur_rate is not None and cur_rate < targets["picking_units_per_hour"]:
        priorities.append({"Urgency": "Medium", "Management review": "Review picking throughput",
                           "Evidence": f"{_display_number(cur_rate, 2)} units/h vs illustrative {_display_number(targets['picking_units_per_hour'], 2)} reference.",
                           "Suggested next step": "Inspect staffing hours, queue waits and shift-level variation."})
    if cases_summary and cases_summary["unassigned"]:
        priorities.append({"Urgency": "Medium", "Management review": "Assign current exception ownership",
                           "Evidence": f"{cases_summary['unassigned']:,} active cases have no assigned owner.",
                           "Suggested next step": "Review current case assignments in Order exceptions."})
    if end_date == latest_day and cur["skus_below_reorder"]:
        priorities.append({"Urgency": "Medium", "Management review": "Check replenishment exposure",
                           "Evidence": f"{cur['skus_below_reorder']:,} warehouse/SKU rows are at or below reorder point in the latest stock snapshot.",
                           "Suggested next step": "Validate real allocations and replenish only after operational checks."})
    if not priorities:
        priorities.append({"Urgency": "Monitor", "Management review": "Continue routine performance review",
                           "Evidence": "No demo reference exceptions in the selected rollup.",
                           "Suggested next step": "Review warehouse and process differences for emerging changes."})

    return {
        "as_of": current["as_of"], "start_date": start_date, "end_date": end_date,
        "prior_start": prior_start if has_full_prior else None,
        "prior_end": prior_end if has_full_prior else None,
        "current": cur, "previous": old if previous else None,
        "comparison": comparison, "warehouses": warehouse_view,
        "processes": process_view, "daily": current["daily"],
        "priorities": pd.DataFrame(priorities), "targets": targets,
        "picking_rate": cur_rate, "prior_picking_rate": old_rate,
        "forecast7": forecast7, "forecast_accuracy_mae": forecast_mae,
        "forecast_daily": forecast_daily, "cases_summary": cases_summary,
        "inventory_snapshot_at": current["snapshot_as_of"],
    }


def export_brief_html(report: dict, selected_names: list[str]) -> bytes:
    """Standalone printable HTML report; client can print/save to PDF if desired."""
    c = report["current"]
    period = f"{report['start_date']:%Y-%m-%d} to {report['end_date']:%Y-%m-%d} UTC"
    columns = [
        ("Orders received", _display_number(c["orders_received"])),
        ("Orders shipped", _display_number(c["orders_shipped"])),
        ("On-time shipping", _percent(c["on_time_shipping_pct"])),
        ("Overdue open", _display_number(c["overdue_open_asof"])),
        ("QC defect rate", _percent(c["qc_defect_rate_pct"], 2)),
        ("Picking units / labor h", _display_number(report["picking_rate"], 2)),
        ("7-day forecast intake", _display_number(report["forecast7"])),
    ]
    kpi_html = "".join(f"<div class='kpi'><small>{escape(k)}</small><strong>{escape(v)}</strong></div>" for k, v in columns)
    def html_table(frame: pd.DataFrame) -> str:
        return frame.to_html(index=False, escape=True, na_rep="N/A", classes="report-table", border=0,
                             float_format=lambda n: f"{n:,.2f}")
    prev = (f"{report['prior_start']:%Y-%m-%d} to {report['prior_end']:%Y-%m-%d}"
            if report["prior_start"] is not None else "Unavailable (insufficient preceding history)")
    markup = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>Warehouse Executive Brief — Synthetic Demo</title><style>
    body{{font-family:Segoe UI,Arial,sans-serif;color:#182b44;max-width:1100px;margin:28px auto;padding:0 18px}}
    h1{{font-size:25px;margin-bottom:5px}} h2{{font-size:18px;margin-top:25px}}
    .note{{color:#4a5d73;font-size:12px;line-height:1.5}}
    .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}
    .kpi{{padding:14px;border:1px solid #d6e0ed;border-radius:8px}}
    small{{display:block;color:#53687e;margin-bottom:7px}}strong{{font-size:21px}}
    .report-table{{border-collapse:collapse;width:100%;font-size:12px}}
    .report-table th,.report-table td{{border-bottom:1px solid #d8e0e9;padding:8px;text-align:left}}
    .report-table th{{background:#edf3f9}}
    @media print{{body{{margin:0;max-width:none}}h2{{break-after:avoid}}.report-table tr{{break-inside:avoid}}}}
    </style></head><body>
    <h1>Warehouse Operations — Executive Brief</h1>
    <p class="note"><b>DEMONSTRATION — SYNTHETIC DATA ONLY.</b> Warehouse scope: {escape(', '.join(selected_names))}<br>
    Reporting window: {escape(period)}; as-of {escape(str(report['as_of']))}<br>
    Previous equally long window: {escape(prev)}. Workflow cases are current-only; current inventory is not a historical snapshot.</p>
    <div class="grid">{kpi_html}</div>
    <h2>Management review priorities</h2>{html_table(report['priorities'])}
    <h2>Performance compared with preceding period</h2>{html_table(report['comparison'])}
    <h2>Warehouse scorecard</h2>{html_table(report['warehouses'])}
    <h2>Process performance</h2>{html_table(report['processes'])}
    <p class="note">Reference targets are illustrative, not contractual. Picking, packing, and dispatch stage units overlap; do not add them as unique shipments. Seven-day demand is a backtested estimate, not a staffing order or guaranteed forecast. Source data is fixed and does not refresh automatically. Workflow case changes are not inferred from source status.</p>
    </body></html>"""
    return markup.encode("utf-8")
