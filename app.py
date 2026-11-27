"""Warehouse Operations Performance Monitoring System — Step 10 public read-only demo.

From the project folder:
    pip install -r requirements.txt
    streamlit run app.py

This application uses fully synthetic demonstration data.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analytics import load_tables, prepare_dashboard
from productivity import PROCESSES, SHIFTS, prepare_productivity
from labor_balancing import analyze_labor_balance, prepare_labor_comparison
from operational_impact import prepare_operational_impact
from forecasting import prepare_forecast, prepare_capacity_scenario, prepare_alerts
from risk_intelligence import prepare_risk_intelligence
from executive import DEMO_REFERENCES, export_brief_html, prepare_executive

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "warehouse_ops_demo.db"
LABOR_BALANCE_FILE = ROOT / "data" / "labor_balance_scenarios.csv"

st.set_page_config(
    page_title="Warehouse Operations Control Tower",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container {padding-top: 1.7rem; padding-bottom: 2rem;}
    div[data-testid="stMetric"] {
        border: 1px solid rgba(120,135,155,0.22);
        border-radius: 12px; padding: 0.85rem 1rem;
        background: rgba(120,135,155,0.04);
    }
    div[data-testid="stMetricLabel"] {font-size: 0.85rem;}
    h1 {letter-spacing: -0.035em;}
    .warehouse-data-table {max-height: 490px; overflow: auto; width: 100%; margin: 0.5rem 0 1rem;}
    .warehouse-data-table table {border-collapse: collapse; width: 100%; min-width: 650px; font-size: 0.85rem;}
    .warehouse-data-table th, .warehouse-data-table td {
        padding: 0.55rem 0.7rem; text-align: left;
        border-bottom: 1px solid rgba(120,135,155,0.25); white-space: nowrap;
    }
    .warehouse-data-table th {background: rgba(120,135,155,0.14); position: sticky; top: 0;}
    .warehouse-data-table tr:nth-child(even) {background: rgba(120,135,155,0.04);}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner="Loading synthetic warehouse database…")
def cached_tables(path: str, modified_time: float) -> dict[str, pd.DataFrame]:
    """mtime is part of the cache key, so newly loaded databases refresh."""
    return load_tables(path)


def pretty_number(value: float | int | None, suffix: str = "", decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if suffix:
        return f"{value:,.{decimals}f}{suffix}"
    return f"{value:,.0f}"


def chart_style(fig):
    fig.update_layout(
        margin=dict(l=0, r=12, t=15, b=0),
        legend_title_text="", height=330,
        hovermode="x unified",
    )
    return fig


def render_html_table(frame: pd.DataFrame, formatters: dict | None = None) -> None:
    """Show a scrollable table without Streamlit's Arrow-based dataframe widget.

    Safe for imported text values: pandas escapes HTML in cell contents.
    This is a compatibility mode for machines where Windows Application Control
    blocks PyArrow's compiled DLL. Sorting/filtering is handled by other widgets.
    """
    if frame.empty:
        st.info("No records to display.")
        return
    table_html = frame.to_html(index=False, escape=True, border=0, formatters=formatters)
    st.markdown(
        f'<div class="warehouse-data-table">{table_html}</div>',
        unsafe_allow_html=True,
    )


def download_csv(label: str, frame: pd.DataFrame, key: str) -> None:
    csv_bytes = frame.to_csv(index=False).encode("utf-8")
    st.download_button(label, csv_bytes, file_name=f"{key}.csv", mime="text/csv", key=key)


if not DATABASE.exists():
    st.error("Missing warehouse_ops_demo.db. Re-extract the full public demo package.")
    st.stop()

try:
    tables = cached_tables(str(DATABASE), DATABASE.stat().st_mtime)
except Exception as exc:
    st.error(f"Could not load the SQLite database: {exc}")
    st.stop()

snapshot = tables["inventory"]["snapshot_at"].max()
min_created = tables["orders"]["created_at"].min().date()
max_day = snapshot.date()
initial_start = max(min_created, max_day - timedelta(days=29))

with st.sidebar:
    st.title("📦 Control Tower")
    st.caption("Warehouse Operations Monitoring — Public demo v1.2")
    st.divider()
    warehouses = tables["warehouses"].sort_values("warehouse_name")
    names_to_ids = dict(zip(warehouses["warehouse_name"], warehouses["warehouse_id"]))
    selected_names = st.multiselect(
        "Warehouses", options=list(names_to_ids), default=list(names_to_ids),
        help="Select one or both of the fictional distribution centers.",
    )
    dates = st.date_input(
        "Report date range (UTC)",
        value=(initial_start, max_day),
        min_value=min_created,
        max_value=max_day,
        help="Date range filters daily order intake, shipments, promises due, and QC inspections.",
    )
    st.caption(f"Available history: {min_created:%b %d, %Y} – {max_day:%b %d, %Y}")
    st.divider()
    st.info("**PUBLIC READ-ONLY DEMO**\n\nAll data is synthetic and frozen as of November 12, 2026. No uploads, user accounts, or database edits are available. Labor balancing uses a separate, editable-on-screen scenario that is never saved.")
    with st.expander("How metrics are defined"):
        st.markdown(
            "**Received**: orders created during the date range.  \n"
            "**Shipped**: shipments sent during the date range.  \n"
            "**On-time rate**: orders whose shipment promise is due during the date range "
            "and by the reporting cutoff, shipped on or before promise / all non-cancelled due orders. "
            "Unshipped overdue orders count against the rate.  \n"
            "**Open backlog**: all non-cancelled orders created by the cutoff without a shipment "
            "by the cutoff, regardless of the report's start date.  \n"
            "**QC defect rate**: defective units / inspected units in the date range.  \n"
            "**Inventory**: latest single snapshot only; not historical."
        )

if not selected_names:
    st.warning("Select at least one warehouse in the sidebar.")
    st.stop()
if not isinstance(dates, (tuple, list)) or len(dates) != 2:
    st.warning("Select a start and end date in the sidebar.")
    st.stop()
start_date, end_date = dates
selected_ids = [names_to_ids[name] for name in selected_names]

try:
    results = prepare_dashboard(tables, selected_ids, start_date, end_date)
except (ValueError, KeyError) as exc:
    st.error(f"Cannot produce dashboard: {exc}")
    st.stop()

k = results["metrics"]
asof = results["as_of"]
st.title("Warehouse Operations Performance")
st.info("🌐 **Public demo · Read-only · Synthetic records** — Explore KPIs, quality, inventory, productivity, staffing scenarios, forecasts, and delay risks. No changes can be saved. Historical data is frozen at the labeled reporting cutoff.")
st.caption(
    f"Operations control tower | Report window: {start_date:%b %d, %Y} – {end_date:%b %d, %Y} (UTC) "
    f"| As-of cutoff: {asof:%Y-%m-%d %H:%M} UTC"
)

first, second, third, fourth = st.columns(4)
with first:
    st.metric("Orders received", pretty_number(k["orders_received"]))
with second:
    st.metric("Orders shipped", pretty_number(k["orders_shipped"]))
with third:
    st.metric("On-time shipping", pretty_number(k["on_time_shipping_pct"], "%"),
              help=f'{k["on_time_due_orders"]:,} of {k["orders_due"]:,} orders due in this window shipped on time.')
with fourth:
    st.metric("Overdue open orders", pretty_number(k["overdue_open_asof"]),
              help="Outstanding and past promised ship time as of the report cutoff (full backlog, not just new orders).")

fifth, sixth, seventh, eighth = st.columns(4)
with fifth:
    st.metric("Open backlog (as-of)", pretty_number(k["open_backlog_asof"]))
with sixth:
    st.metric("Avg. shipping cycle", pretty_number(k["average_cycle_hours"], " h"))
with seventh:
    st.metric("QC defect rate", pretty_number(k["qc_defect_rate_pct"], "%", decimals=2))
with eighth:
    st.metric("SKUs at/below reorder", pretty_number(k["skus_below_reorder"]),
              help="Number of SKU/warehouse rows at or below the reorder point, from the current inventory snapshot.")

tab_exec, tab_overview, tab_exceptions, tab_quality, tab_inventory, tab_productivity, tab_labor, tab_forecast, tab_risk = st.tabs(
    ["🏢 Executive brief", "📊 Overview", "🚨 Overdue orders", "🛡️ Quality control", "📦 Inventory",
     "⚙️ Productivity", "⚖️ Labor balancing", "🔮 Forecast & alerts", "🧠 Delay risk & triage"]
)

# These on-screen staffing inputs are standalone simulation records, not WMS
# headcounts. Do not infer employee counts from the labor_shifts hours table.
if LABOR_BALANCE_FILE.exists():
    labor_scenarios = pd.read_csv(LABOR_BALANCE_FILE)
    labor_scenarios = labor_scenarios[labor_scenarios["warehouse_id"].isin(selected_ids)].copy()
else:
    labor_scenarios = pd.DataFrame()

with tab_exec:
    st.subheader("Executive performance summary")
    if not labor_scenarios.empty and end_date == max_day:
        sample_labor_alerts = analyze_labor_balance(labor_scenarios)
        active_moves = int(sample_labor_alerts["transfers"]["employees_to_transfer"].sum())
        remaining_gaps = int(sample_labor_alerts["summary"]["shortage_after"].sum())
        if active_moves:
            st.warning(f"⚖️ **Labor balancing scenario:** {active_moves} cross-trained employee transfers "
                       f"recommended across the selected warehouses' synthetic shifts. "
                       f"Unfilled staffing gaps after transfers: {remaining_gaps}. "
                       "See **⚖️ Labor balancing** for sources, destinations, and staffing assumptions. "
                       "This is not a live WMS alert.")
    st.caption(
        "Read-only management review of synthetic warehouse operations. This summarizes the current report period "
        "and compares it with the immediately preceding equally long period, when complete prior history exists. "
        "All event timestamps and shift labels are UTC."
    )
    with st.expander("Illustrative leadership reference points (editable)"):
        st.caption("These are demonstration thresholds, NOT confirmed company targets or service-level agreements.")
        t1, t2, t3 = st.columns(3)
        ontime_ref = t1.number_input("On-time shipping reference (%)", min_value=0.0, max_value=100.0,
                                      value=DEMO_REFERENCES["on_time_shipping_pct"], step=0.5,
                                      key="exec_target_ot")
        qc_ref = t2.number_input("QC defect upper guide (%)", min_value=0.0, max_value=100.0,
                                  value=DEMO_REFERENCES["qc_defect_rate_pct"], step=0.25,
                                  key="exec_target_qc")
        pick_ref = t3.number_input("Picking reference (units/labor hour)", min_value=0.1, max_value=1000.0,
                                    value=DEMO_REFERENCES["picking_units_per_hour"], step=0.5,
                                    key="exec_target_pick")
    # Public demo does not load or disclose individual case workflow/audit records.
    exec_cases = None
    try:
        e = prepare_executive(
            tables, selected_ids, start_date, end_date,
            targets={"on_time_shipping_pct": ontime_ref, "qc_defect_rate_pct": qc_ref,
                     "picking_units_per_hour": pick_ref},
            workflow_cases=exec_cases,
        )
    except (ValueError, KeyError) as exc:
        st.warning(f"Executive brief unavailable for these filters: {exc}")
    else:
        ec = e["current"]
        comp = e["comparison"].set_index("KPI")
        def exec_change(name, suffix="", decimals=1):
            val = comp.loc[name, "Change"]
            if pd.isna(val):
                return None
            return f"{val:+,.{decimals}f}{suffix} vs prior"

        st.markdown("#### Service, quality, and backlog")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("On-time shipping", pretty_number(ec["on_time_shipping_pct"], "%"),
                  delta=exec_change("On-time shipping", " pp"),
                  help="On-time / due during selected period. Change is in percentage points.")
        m2.metric("Overdue open", f"{ec['overdue_open_asof']:,}",
                  delta=exec_change("Overdue open (as-of)", "", 0), delta_color="inverse",
                  help="Open orders overdue as of the cutoff; not just orders received this period.")
        m3.metric("QC defect rate", pretty_number(ec["qc_defect_rate_pct"], "%", decimals=2),
                  delta=exec_change("QC defect rate", " pp", 2), delta_color="inverse",
                  help="Defective inspected units / all inspected units in the window.")
        m4.metric("Picking units / labor h", pretty_number(e["picking_rate"], decimals=2),
                  delta=exec_change("Picking units/labor h", "", 2),
                  help="Stage-specific throughput; picking stage is not an additional shipment count.")
        n1, n2, n3, n4 = st.columns(4)
        n1.metric("Orders received", f"{ec['orders_received']:,}",
                  delta=exec_change("Orders received", "", 0), delta_color="off")
        n2.metric("Orders shipped", f"{ec['orders_shipped']:,}",
                  delta=exec_change("Orders shipped", "", 0), delta_color="off")
        n3.metric("Open backlog", f"{ec['open_backlog_asof']:,}",
                  delta=exec_change("Open backlog (as-of)", "", 0), delta_color="inverse")
        n4.metric("Next 7-day order forecast", pretty_number(e['forecast7']),
                  help="Estimate from historical synthetic intake; not an observed value or guaranteed volume.")
        if e["prior_start"] is None:
            st.info("No complete preceding equal-length window is available. Period-change values are intentionally blank.")
        else:
            st.caption(f"Comparison period: {e['prior_start']:%b %d, %Y} – {e['prior_end']:%b %d, %Y} UTC. "
                       "Percent rates are compared in percentage points (pp). Latest inventory isn't historically reconstructed.")
        if e["forecast_accuracy_mae"] is None:
            st.info("A demand forecast requires at least 70 complete days of data before the selected cutoff.")
        else:
            st.caption(f"Forecast is based on past order intake; mean warehouse-level daily backtest MAE: "
                       f"{e['forecast_accuracy_mae']:.1f} orders/day. Use it for scenario discussions, not staffing commitments.")

        st.divider()
        st.subheader("Leadership review priorities")
        st.caption("Suggested investigations generated from transparent rules and editable demo references; "
                   "these are not verified root causes or automatic escalations.")
        render_html_table(e["priorities"])
        if e["cases_summary"] is not None:
            s = e["cases_summary"]
            st.caption(f"Latest workflow case records: {s['tracked_active']:,} currently overdue tracked; "
                       f"{s['unresolved']:,} not marked Resolved; {s['unassigned']:,} unassigned; "
                       f"{s['critical']:,} Critical; {s['resolved_but_still_overdue']:,} marked "
                       "Resolved although the source order remains overdue. Workflow timing is real UTC, "
                       "not the historical synthetic event time. The public demo cannot change workflow records.")
        else:
            st.caption("Historical view: current workflow case statuses are intentionally omitted.")

        st.divider()
        left, right = st.columns([3, 2])
        with left:
            st.subheader("Daily fulfillment trend")
            trend = e["daily"].melt(id_vars="date", value_vars=["Orders received", "Orders shipped"],
                                    var_name="Series", value_name="Order count")
            trend_chart = px.line(trend, x="date", y="Order count", color="Series",
                                  color_discrete_map={"Orders received": "#4B83D0", "Orders shipped": "#16A085"})
            st.plotly_chart(chart_style(trend_chart), use_container_width=True)
        with right:
            st.subheader("7-day intake outlook")
            future = e["forecast_daily"]
            if future.empty:
                st.info("Not enough history for a backtested forecast at this cutoff.")
            else:
                forward_chart = px.bar(future, x="date", y="forecast_orders",
                                       labels={"date": "Date (UTC)", "forecast_orders": "Expected orders"},
                                       color_discrete_sequence=["#4B83D0"])
                st.plotly_chart(chart_style(forward_chart), use_container_width=True)

        st.subheader("Warehouse scorecard")
        render_html_table(e["warehouses"].round(2))
        st.caption("Separate process throughput counts can overlap: one order is picked, packed and dispatched. "
                   "Inventory remains a latest-snapshot metric, independent of historical window filters.")
        st.subheader("Process efficiency and queue waits")
        render_html_table(e["processes"].round(2))
        st.caption("Process reference attainment uses illustrative stage-specific rates; "
                   "P90 queue waits are observations from the synthetic activity log.")

        st.divider()
        st.subheader("Executive briefing exports")
        eleft, eright = st.columns(2)
        with eleft:
            st.download_button("Download printable management brief (HTML)",
                               data=export_brief_html(e, selected_names),
                               file_name="warehouse_executive_brief_step8.html",
                               mime="text/html", key="exec_html")
        with eright:
            download_csv("Download executive KPI comparison (CSV)", e["comparison"], "exec_kpi_comparison")
        st.caption("Open the downloaded HTML in a browser and use Print if you need a PDF. "
                   "No external email or WMS actions are performed.")

with tab_overview:
    st.subheader("Daily fulfillment activity")
    st.caption("Incoming orders and shipped orders are separate event measures, not identical order cohorts.")
    daily = results["daily"]
    plot_data = daily.melt(id_vars="date", value_vars=["Orders received", "Orders shipped"],
                           var_name="Event", value_name="Orders")
    fig = px.line(plot_data, x="date", y="Orders", color="Event", markers=False,
                  color_discrete_map={"Orders received": "#4B83D0", "Orders shipped": "#16A085"})
    fig.update_xaxes(title_text="Date (UTC)")
    fig.update_yaxes(title_text="Count", rangemode="tozero")
    st.plotly_chart(chart_style(fig), use_container_width=True)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Warehouse performance")
        render_html_table(results["warehouse_report"])
    with right:
        st.subheader("Reporting notes")
        st.markdown(
            f"- **{k['orders_due']:,} orders** had shipment promises due in this report period.\n"
            f"- **{k['qc_units_inspected']:,} units** were inspected during this report period.\n"
            f"- **{k['open_backlog_asof']:,} orders** remained open as of the cutoff, "
            "including orders created before the report start."
        )

with tab_exceptions:
    st.subheader("Overdue orders — read-only investigation")
    st.caption(
        "Overdue orders are calculated from synthetic source order and shipment records at the selected UTC cutoff. "
        "Severity uses transparent aging / order-priority rules and is not a verified company SLA. "
        "Assignment, editing, audit history and case resolution are deliberately unavailable in this public demo."
    )
    exceptions = results["exceptions"].copy()
    if exceptions.empty:
        st.success("No overdue orders for this warehouse and reporting cutoff.")
    else:
        def demo_severity(overdue_h: float, priority: str) -> str:
            if overdue_h >= 48 or (priority == "SameDay" and overdue_h >= 12):
                return "Critical"
            if overdue_h >= 24 or (priority == "Expedited" and overdue_h >= 8):
                return "High"
            if overdue_h >= 8 or (priority == "SameDay" and overdue_h >= 2):
                return "Medium"
            return "Low"

        exceptions["severity"] = [
            demo_severity(float(hours), str(priority))
            for hours, priority in zip(exceptions["overdue_hours"], exceptions["priority"])
        ]
        c1, c2, c3 = st.columns(3)
        c1.metric("Overdue", f'{len(exceptions):,}')
        c2.metric("Critical (illustrative)", f'{int(exceptions["severity"].eq("Critical").sum()):,}')
        c3.metric("High (illustrative)", f'{int(exceptions["severity"].eq("High").sum()):,}')
        severity_options = ["Critical", "High", "Medium", "Low"]
        chosen_severity = st.multiselect("Severity", severity_options, default=severity_options, key="demo_exception_severity")
        shown = exceptions.loc[exceptions["severity"].isin(chosen_severity)].sort_values("overdue_hours", ascending=False)
        view = shown[["order_id", "warehouse_name", "severity", "priority", "promised_ship_at",
                      "overdue_hours", "units_ordered", "order_value"]].rename(columns={
            "order_id": "Order ID", "warehouse_name": "Warehouse", "severity": "Severity",
            "priority": "Priority", "promised_ship_at": "Promised ship (UTC)",
            "overdue_hours": "Hours overdue", "units_ordered": "Units", "order_value": "Order value",
        })
        st.caption(f"{len(view):,} matching records. This is a historical demo snapshot, not a live action queue.")
        render_html_table(view, formatters={"Order value": lambda v: f"${v:,.2f}"})
        download_csv("Download overdue-order view", view, "demo_overdue_orders")

with tab_quality:
    st.subheader("Quality inspection performance")
    qa, qb, qc_col = st.columns(3)
    qa.metric("Units inspected", pretty_number(k["qc_units_inspected"]))
    qb.metric("Defective units", pretty_number(k["qc_defect_units"]))
    qc_col.metric("Defect rate", pretty_number(k["qc_defect_rate_pct"], "%", decimals=2))
    issues = results["issue_breakdown"]
    if issues.empty:
        st.info("No defective units were recorded in the selected period.")
    else:
        fig = px.bar(issues, x="issue_type", y="defect_units", labels={
            "issue_type": "Issue type", "defect_units": "Defective units"
        }, color_discrete_sequence=["#D68035"])
        st.plotly_chart(chart_style(fig), use_container_width=True)
        st.subheader("Recent inspection failures (up to 200)")
        st.caption("Resolution status reflects the current table, not historical status as of the date filter.")
        render_html_table(results["recent_qc"])

with tab_inventory:
    st.subheader("Inventory reorder monitoring")
    st.caption(f"Current stock snapshot at {results['snapshot_as_of']:%Y-%m-%d %H:%M} UTC. "
               "Inventory is not affected by the historical date range filter.")
    inv = results["inventory"]
    only_low = st.toggle("Show items at/below reorder point only", value=True)
    if only_low:
        inv = inv.loc[inv["stock_flag"] == "Below/at reorder point"]
    view = inv[["warehouse_name", "product_id", "product_name", "category", "on_hand_qty",
                "allocated_qty", "available_qty", "reorder_point", "stock_flag"]].rename(columns={
        "warehouse_name": "Warehouse", "product_id": "SKU", "product_name": "Product",
        "category": "Category", "on_hand_qty": "On hand", "allocated_qty": "Allocated",
        "available_qty": "Available", "reorder_point": "Reorder point", "stock_flag": "Status",
    })
    render_html_table(view)
    download_csv("Download inventory view", view, "inventory_reorder_view")



with tab_productivity:
    st.subheader('Warehouse productivity & shift performance')
    st.caption(
        'Measured by completed synthetic process events and separately recorded synthetic direct labor hours. '
        'Picking, packing, and dispatch touch the same shipped units—do not add their stage volumes as unique shipped units.'
    )
    p_left, p_right = st.columns([2, 3])
    with p_left:
        selected_process = st.selectbox('Process to analyze', options=list(PROCESSES), index=0)
    with p_right:
        prod_shifts = st.multiselect('UTC shift(s)', options=list(SHIFTS), default=list(SHIFTS))
    if not prod_shifts:
        st.info('Choose at least one shift to display productivity results.')
    else:
        p = prepare_productivity(tables, selected_ids, start_date, end_date, selected_process, prod_shifts)
        pm = p['metrics']
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f'{selected_process} units handled', pretty_number(pm['stage_units']),
                  help='Units completed at this ONE process stage during the selected UTC period.')
        c2.metric('Direct labor hours', pretty_number(pm['actual_labor_hours'], decimals=1) + ' h',
                  help='Recorded labor hours assigned to the selected process, warehouses, and shifts.')
        c3.metric('Units per labor hour', pretty_number(pm['units_per_labor_hour'], decimals=2),
                  help='Process-level completed units ÷ actual labor hours. Not whole-facility productivity.')
        c4.metric('Reference-rate attainment', pretty_number(pm['target_attainment_pct'], '%', decimals=1),
                  help=f'Synthetic reference rate for {selected_process}: {pm["reference_units_per_hour"]} units/hour. Not an employer standard.')
        q1, q2, q3 = st.columns(3)
        q1.metric('Completed process events', pretty_number(pm['completed_activities']))
        q2.metric('Avg. preceding queue wait', pretty_number(pm['avg_queue_hours'], ' h', decimals=1))
        q3.metric('90th percentile queue wait', pretty_number(pm['p90_queue_hours'], ' h', decimals=1))
        st.caption(f'Labor hours versus plan: {pretty_number(pm["staffing_vs_plan_pct"], "%", decimals=1)}. '
                   'This is a staffing variance, not an employee utilization or attendance measure.')

        st.divider()
        chart1, chart2 = st.columns([3, 2])
        with chart1:
            st.subheader(f'{selected_process}: daily handled units and productivity')
            daily = p['daily']
            daily_fig = px.line(daily, x='date', y='units_processed', markers=False,
                                labels={'date': 'Date (UTC)', 'units_processed': 'Stage units handled'},
                                color_discrete_sequence=['#477DCD'])
            daily_fig.update_yaxes(rangemode='tozero')
            st.plotly_chart(chart_style(daily_fig), use_container_width=True)
            with st.expander('View daily productivity rate'):
                productivity_fig = px.line(daily, x='date', y='units_per_labor_hour', markers=False,
                                            labels={'date': 'Date (UTC)', 'units_per_labor_hour': 'Units per labor hour'},
                                            color_discrete_sequence=['#16A085'])
                productivity_fig.add_hline(y=pm['reference_units_per_hour'], line_dash='dot',
                                            annotation_text='Illustrative reference', annotation_position='top left')
                st.plotly_chart(chart_style(productivity_fig), use_container_width=True)
        with chart2:
            st.subheader('Completed units by UTC hour')
            hour_fig = px.bar(p['hourly'], x='hour_utc', y='units_processed',
                              labels={'hour_utc': 'Completion hour (UTC)', 'units_processed': 'Stage units'},
                              color_discrete_sequence=['#16A085'])
            hour_fig.update_xaxes(dtick=2)
            st.plotly_chart(chart_style(hour_fig), use_container_width=True)

        st.subheader('Shift performance')
        by_shift = p['by_shift']
        shift_display = by_shift[[
            'shift_name', 'units_processed', 'activities', 'actual_labor_hours',
            'units_per_labor_hour', 'target_attainment_pct', 'p90_queue_hours',
        ]].rename(columns={
            'shift_name': 'Shift (UTC)', 'units_processed': 'Handled units',
            'activities': 'Completed events', 'actual_labor_hours': 'Labor hours',
            'units_per_labor_hour': 'Units / labor hr',
            'target_attainment_pct': 'Reference attainment %',
            'p90_queue_hours': 'P90 queue wait (h)',
        }).round(2)
        render_html_table(shift_display)
        download_csv('Download shift performance', shift_display, 'productivity_by_shift')

        with st.expander('Warehouse × shift comparison'):
            wh_display = p['by_warehouse_shift'][[
                'warehouse_name', 'shift_name', 'units_processed', 'actual_labor_hours',
                'units_per_labor_hour', 'p90_queue_hours',
            ]].rename(columns={
                'warehouse_name': 'Warehouse', 'shift_name': 'Shift (UTC)',
                'units_processed': 'Handled units', 'actual_labor_hours': 'Labor hours',
                'units_per_labor_hour': 'Units / labor hr', 'p90_queue_hours': 'P90 queue wait (h)',
            }).round(2)
            render_html_table(wh_display)
            download_csv('Download warehouse × shift performance', wh_display, 'productivity_wh_shift')

        st.divider()
        st.subheader('Process bottleneck indicators')
        st.caption('Candidate bottlenecks based on the 90th-percentile queue wait before each process. '
                   'These wait times are simulated proxies, not actual WMS scanner records or proof of a causal bottleneck.')
        processes = p['by_process']
        b1, b2 = st.columns([2, 3])
        with b1:
            queue_fig = px.bar(processes, x='process_name', y='p90_queue_hours',
                               labels={'process_name': 'Process', 'p90_queue_hours': 'P90 preceding queue (hours)'},
                               color_discrete_sequence=['#D68C40'])
            st.plotly_chart(chart_style(queue_fig), use_container_width=True)
        with b2:
            process_view = processes[[
                'process_name', 'units_processed', 'actual_labor_hours',
                'units_per_labor_hour', 'reference_units_per_hour',
                'target_attainment_pct', 'p90_queue_hours',
            ]].rename(columns={
                'process_name': 'Process', 'units_processed': 'Stage units',
                'actual_labor_hours': 'Labor hours', 'units_per_labor_hour': 'Units / labor hr',
                'reference_units_per_hour': 'Reference units/hr',
                'target_attainment_pct': 'Attainment %', 'p90_queue_hours': 'P90 queue (h)',
            }).round(2)
            render_html_table(process_view)
            download_csv('Download process comparison', process_view, 'productivity_process_comparison')

        with st.expander('Inspect recent process completions (latest 100)'):
            recent = p['recent_activities'].rename(columns={
                'order_id': 'Order', 'warehouse_id': 'Warehouse ID', 'process_name': 'Process',
                'shift_name': 'Shift (UTC)', 'completed_at': 'Completed at (UTC)',
                'units_processed': 'Handled units', 'queue_minutes': 'Queue wait (min)',
                'processing_minutes': 'Processing time (min)', 'activity_id': 'Activity ID',
            })
            render_html_table(recent)
            download_csv('Download recent process activities', recent, 'recent_productivity_activities')

    st.info('All productivity records are synthetic. Shift windows are UTC (not local North Carolina time); '
            'the final Nov 12 shift is partially recorded to the snapshot cutoff. '
            'Production use would require real scan events, reliable timekeeping, local timezone rules, and validated labor allocation.')

with tab_labor:
    st.subheader("Cross-trained labor balancing & management alerts")
    st.caption("Simulated staffing redistribution across Picking, Packing, and Dispatch. "
               "Every employee is assumed eligible to perform every department's tasks. "
               "Recommended transfers are advisory, never executed automatically.")
    if labor_scenarios.empty:
        st.error("The standalone synthetic staffing scenario is missing. "
                 "Include data/labor_balance_scenarios.csv in your deployment.")
    elif end_date != max_day:
        st.info("This staffing scenario is frozen at **November 12, 2026**, independent of historical "
                "labor-hours reporting. Select November 12 as the report end date to inspect it.")
    else:
        st.warning("**Synthetic labor scenario, not live staffing:** Department headcounts and queued workload "
                   "are illustrative manual inputs, **not** derived from the historical labor-hours table. "
                   "Shift times are UTC. Transfer recommendations assume all workers are cross-trained, "
                   "available, and safe to move within the same warehouse and shift.")
        tune1, tune2, tune3 = st.columns(3)
        with tune1:
            hours_left = st.number_input('Hours remaining per worker', min_value=0.5,
                                         max_value=8.0, value=6.0, step=0.5,
                                         help='How many productive hours remain in the modeled shift?')
        with tune2:
            target_pct = st.slider('Planned productive time (%)', 60, 100, 85, 5,
                                   help='Allow for breaks, movement, and downtime. All inputs are illustrative.')
        with tune3:
            buffer_pct = st.slider('Workload contingency (%)', 0, 30, 10, 5,
                                   help='Reserve extra capacity for interruptions and rework.')
        transfer_setup_minutes = st.slider(
            'Estimated handoff/setup time per transferred employee (minutes)',
            min_value=0, max_value=120, value=15, step=5,
            help='Illustrative delay before transferred workers become productive in the destination department. '
                 'Only affects Step 17 capacity estimates, not transfer eligibility.'
        )
        base = analyze_labor_balance(labor_scenarios, hours_remaining=hours_left,
                                     target_utilization=target_pct / 100,
                                     workload_buffer=buffer_pct / 100)
        base_summaries = base['summary']
        base_moves = base['transfers']
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Suggested transfers', int(base_moves['employees_to_transfer'].sum()))
        c2.metric('Shifts with recommendations', int((base_summaries['transfers_recommended'] > 0).sum()))
        c3.metric('Remaining shortages', int(base_summaries['shortage_after'].sum()),
                  help='Required staff not covered after transfers; no cross-shift moves are assumed.')
        c4.metric('Excess staff remaining', int(base_summaries['surplus_after'].sum()),
                  help='Surplus staff who have no valid shortage recipient in their warehouse and shift.')
        st.markdown('#### Management alert queue')
        brief = base_summaries[['warehouse_name', 'shift_name', 'alert',
                                'transfers_recommended', 'shortage_after', 'surplus_after',
                                'stage_work_risk_reduction']].rename(columns={
            'warehouse_name': 'Warehouse', 'shift_name': 'Shift (UTC)',
            'alert': 'Alert', 'transfers_recommended': 'Move workers',
            'shortage_after': 'Shortage after', 'surplus_after': 'Surplus after',
            'stage_work_risk_reduction': 'Modeled stage-work gap reduced (units)',
        }).round(1)
        render_html_table(brief)
        st.caption('Modeled work-gap reduction counts workload at each process stage, not distinct shipped units or validated savings.')
        if not base_moves.empty:
            st.markdown('#### Recommended transfers across selected warehouses')
            moves_display = base_moves[['warehouse_name', 'shift_name', 'from_department',
                                        'to_department', 'employees_to_transfer']].rename(columns={
                'warehouse_name': 'Warehouse', 'shift_name': 'Shift (UTC)',
                'from_department': 'From', 'to_department': 'To',
                'employees_to_transfer': 'Move employees',
            })
            render_html_table(moves_display)
            download_csv('Download synthetic transfer plan', moves_display, 'labor_balance_suggested_transfers')
        else:
            st.success('No transfers indicated by the current synthetic staffing scenario.')

        # This comparison uses exactly the same stage-work capacity assumptions
        # as the transfer engine, so it is an illustrative planning estimate.
        all_impact = prepare_labor_comparison(base)['metrics']
        st.markdown('#### Before vs. after — all modeled shifts')
        o1, o2, o3, o4 = st.columns(4)
        o1.metric('Stage-work coverage before',
                  f"{all_impact['coverage_pct_before']:.1f}%" if all_impact['coverage_pct_before'] is not None else 'N/A')
        o2.metric('Stage-work coverage after',
                  f"{all_impact['coverage_pct_after']:.1f}%" if all_impact['coverage_pct_after'] is not None else 'N/A',
                  delta=(f"{all_impact['coverage_pct_after'] - all_impact['coverage_pct_before']:+.1f} pp"
                         if all_impact['coverage_pct_before'] is not None else None))
        o3.metric('Modeled stage-work gap reduced', f"{all_impact['stage_work_gap_reduced']:,.0f} units")
        o4.metric('Staff shortages before → after',
                  f"{all_impact['shortage_before']} → {all_impact['shortage_after']}")
        st.caption('Totals are across **six independently modeled warehouse/shift scenarios** and count work at ' 
                   'each process stage, not distinct shipped units. Coverage is a planning estimate, not ' 
                   'measured productivity or a forecast of on-time delivery.')

        # Step 17 includes modeled handoff time; Step 16 deliberately preserves
        # the frictionless comparison for reference.
        global_operational_impact = prepare_operational_impact(
            base, transfer_setup_minutes=transfer_setup_minutes,
        )['metrics']
        st.markdown('#### Step 17 — Impact with transfer handoff time (all modeled shifts)')
        g1, g2, g3, g4 = st.columns(4)
        g1.metric('Additional stage work coverable',
                  f"{global_operational_impact['additional_stage_work_covered']:,.1f}",
                  help='Extra stage-work units the modeled staffing plan can cover before shift end, '
                       'after simulated handoff delay. Not additional orders shipped.')
        g2.metric('Workload stages at capacity risk',
                  f"{global_operational_impact['at_risk_stages_before']} → "
                  f"{global_operational_impact['at_risk_stages_after']}")
        g3.metric('Uncovered stage-work before',
                  f"{global_operational_impact['uncovered_stage_work_before']:,.1f}")
        g4.metric('Uncovered stage-work after',
                  f"{global_operational_impact['uncovered_stage_work_after']:,.1f}")
        st.caption('These results include simulated setup time and may therefore be lower than the '
                   'frictionless Step 16 figures above. At-risk stages are work queues with insufficient '
                   'modeled capacity by shift end—not predicted late orders or customer SLA breaches.')

        st.markdown('#### Test a shift-level reallocation')
        warehouse_options = list(labor_scenarios['warehouse_name'].drop_duplicates())
        local_left, local_right = st.columns(2)
        with local_left:
            chosen_wh = st.selectbox('Warehouse for scenario', warehouse_options, key='labor_wh')
        with local_right:
            chosen_shift = st.selectbox('Shift for scenario', list(SHIFTS), key='labor_shift')
        selected_scenario = labor_scenarios.loc[
            labor_scenarios['warehouse_name'].eq(chosen_wh)
            & labor_scenarios['shift_name'].eq(chosen_shift)
        ].copy()
        st.caption('Adjust on-duty staff, remaining stage work, minimum safe coverage, or process rate. '
                   'Changes run only in this browser session and are never saved to the database.')
        user_rates = {}
        for process in PROCESSES:
            row = selected_scenario[selected_scenario['process_name'].eq(process)]
            if row.empty:
                continue
            idx = row.index[0]
            current = row.iloc[0]
            st.markdown(f'**{process}**')
            u1, u2, u3, u4 = st.columns(4)
            with u1:
                headcount = st.number_input('Staff on duty', 0, 200,
                                            int(current['staff_on_duty']), 1,
                                            key=f'cnt_{process}')
            with u2:
                queued_units = st.number_input('Remaining stage units', 0, 100000,
                                                int(current['remaining_work_units']), 5,
                                                key=f'work_{process}')
            with u3:
                min_staff = st.number_input('Minimum coverage', 0, 200,
                                            int(current['minimum_staff']), 1,
                                            key=f'min_{process}')
            with u4:
                reference = {'Picking': 6.5, 'Packing': 8.0, 'Dispatch': 10.5}[process]
                user_rates[process] = st.number_input('Units / person-hour',
                                                        min_value=0.1, max_value=500.0,
                                                        value=reference, step=0.5,
                                                        key=f'rate_{process}')
            selected_scenario.loc[idx, ['staff_on_duty', 'remaining_work_units', 'minimum_staff']] = (
                int(headcount), int(queued_units), int(min_staff)
            )
        modeled = analyze_labor_balance(selected_scenario,
                                        hours_remaining=hours_left,
                                        target_utilization=target_pct / 100,
                                        workload_buffer=buffer_pct / 100,
                                        rates=user_rates)
        details = modeled['departments'][['process_name', 'staff_on_duty', 'staff_needed',
                                          'surplus_before', 'shortage_before', 'staff_after',
                                          'shortage_after', 'remaining_work_units']].rename(columns={
            'process_name': 'Department', 'staff_on_duty': 'Current employees',
            'staff_needed': 'Employees needed', 'surplus_before': 'Surplus before',
            'shortage_before': 'Shortage before', 'staff_after': 'After transfer',
            'shortage_after': 'Shortage after', 'remaining_work_units': 'Work units remaining',
        })
        render_html_table(details)

        impact = prepare_labor_comparison(modeled)
        impact_rows = impact['departments']
        impact_metrics = impact['metrics']
        st.markdown('#### Before vs. after — selected warehouse and shift')
        i1, i2, i3, i4 = st.columns(4)
        i1.metric('Modeled work coverage before',
                  f"{impact_metrics['coverage_pct_before']:.1f}%"
                  if impact_metrics['coverage_pct_before'] is not None else 'N/A')
        i2.metric('Modeled work coverage after',
                  f"{impact_metrics['coverage_pct_after']:.1f}%"
                  if impact_metrics['coverage_pct_after'] is not None else 'N/A',
                  delta=(f"{impact_metrics['coverage_pct_after'] - impact_metrics['coverage_pct_before']:+.1f} pp"
                         if impact_metrics['coverage_pct_before'] is not None else None))
        i3.metric('Stage-work gap reduced', f"{impact_metrics['stage_work_gap_reduced']:,.1f} units")
        i4.metric('Unfilled positions before → after',
                  f"{impact_metrics['shortage_before']} → {impact_metrics['shortage_after']}")
        left_chart, right_chart = st.columns(2)
        with left_chart:
            st.markdown('**Staff allocation by department**')
            fig_staff = go.Figure()
            fig_staff.add_trace(go.Bar(x=impact_rows['process_name'],
                                       y=impact_rows['staff_on_duty'], name='Before'))
            fig_staff.add_trace(go.Bar(x=impact_rows['process_name'],
                                       y=impact_rows['staff_after'], name='After'))
            fig_staff.add_trace(go.Scatter(x=impact_rows['process_name'],
                                           y=impact_rows['staff_needed'],
                                           mode='lines+markers', name='Required staffing',
                                           line=dict(dash='dot')))
            fig_staff.update_layout(barmode='group', yaxis_title='Employees',
                                    xaxis_title='Department', yaxis=dict(rangemode='tozero'))
            st.plotly_chart(chart_style(fig_staff), use_container_width=True)
        with right_chart:
            st.markdown('**Expected stage-work coverage**')
            fig_coverage = go.Figure()
            fig_coverage.add_trace(go.Bar(x=impact_rows['process_name'],
                                          y=impact_rows['coverage_pct_before'], name='Before'))
            fig_coverage.add_trace(go.Bar(x=impact_rows['process_name'],
                                          y=impact_rows['coverage_pct_after'], name='After'))
            fig_coverage.update_layout(barmode='group', xaxis_title='Department',
                                       yaxis_title='Modeled workload covered (%)',
                                       yaxis=dict(range=[0, 105]))
            st.plotly_chart(chart_style(fig_coverage), use_container_width=True)

        impact_table = impact_rows[[
            'process_name', 'staff_on_duty', 'staff_after', 'staff_change',
            'remaining_work_units', 'work_units_at_risk_before',
            'work_units_at_risk_after', 'stage_work_coverage_change',
        ]].rename(columns={
            'process_name': 'Department', 'staff_on_duty': 'Staff before',
            'staff_after': 'Staff after', 'staff_change': 'Staff change (+/-)',
            'remaining_work_units': 'Remaining stage work',
            'work_units_at_risk_before': 'Uncovered work before',
            'work_units_at_risk_after': 'Uncovered work after',
            'stage_work_coverage_change': 'Additional stage work covered',
        }).round(1)
        render_html_table(impact_table)
        download_csv('Download before/after comparison', impact_table, 'labor_balance_impact')
        st.caption('These are *illustrative capacity estimates* under the selected productivity rates and ' 
                   'hours left, not observed output gains, cost savings, or unique orders. No employees move ' 
                   'in the demo; total staff is conserved within the selected shift. Zero-work departments ' 
                   'show N/A coverage. Managers must verify actual work queues and constraints.')

        st.markdown('#### Projected operational impact (selected shift)')
        operational_impact = prepare_operational_impact(
            modeled, transfer_setup_minutes=transfer_setup_minutes,
        )
        op_stages = operational_impact['stages']
        op = operational_impact['metrics']
        q1, q2, q3, q4 = st.columns(4)
        q1.metric('Additional stage work coverable', f"{op['additional_stage_work_covered']:,.1f}",
                  help='Extra stage-work units covered within this shift if recommendations are followed '
                       'and the setup delay and reference rates hold.')
        q2.metric('Stage queues at risk',
                  f"{op['at_risk_stages_before']} → {op['at_risk_stages_after']}",
                  help='Number of department queues with work projected to remain at shift end.')
        q3.metric('Shift-end work remaining (before)',
                  f"{op['uncovered_stage_work_before']:,.1f}")
        q4.metric('Shift-end work remaining (after)',
                  f"{op['uncovered_stage_work_after']:,.1f}")

        load_left, clear_right = st.columns(2)
        with load_left:
            st.markdown('**Planned workload as % of stage capacity**')
            load_fig = go.Figure()
            for period in ('before', 'after'):
                load_fig.add_trace(go.Bar(
                    x=op_stages['process_name'],
                    y=op_stages[f'capacity_load_pct_{period}'],
                    name=period.title(),
                ))
            load_fig.add_hline(y=100, line_dash='dot', line_color='gray')
            load_fig.update_layout(barmode='group', xaxis_title='Department',
                                   yaxis_title='Workload / stage capacity (%)')
            st.plotly_chart(chart_style(load_fig), use_container_width=True)
        with clear_right:
            st.markdown('**Estimated hours to clear remaining stage work**')
            clear_fig = go.Figure()
            for period in ('before', 'after'):
                clear_fig.add_trace(go.Bar(
                    x=op_stages['process_name'],
                    y=op_stages[f'estimated_clear_hours_{period}'],
                    name=period.title(),
                ))
            clear_fig.add_hline(y=hours_left, line_dash='dot', line_color='gray')
            clear_fig.update_layout(barmode='group', xaxis_title='Department',
                                    yaxis_title='Elapsed hours to clear stage queue')
            st.plotly_chart(chart_style(clear_fig), use_container_width=True)
        st.caption('The dotted load line is 100% capacity; the dotted clearance line is time left '
                   'in the shift. A workload/capacity ratio above 100% signals a modeled overload. '
                   'If a stage has zero staff and work remains, its clearance time is unavailable.')

        op_table = op_stages[[
            'process_name', 'staff_before', 'staff_after', 'remaining_stage_units',
            'capacity_before', 'capacity_after', 'stage_work_covered_before',
            'stage_work_covered_after', 'additional_stage_work_covered',
            'capacity_load_pct_before', 'capacity_load_pct_after',
            'estimated_clear_hours_before', 'estimated_clear_hours_after',
        ]].rename(columns={
            'process_name': 'Department', 'staff_before': 'Staff before',
            'staff_after': 'Staff after', 'remaining_stage_units': 'Stage work queued',
            'capacity_before': 'Stage capacity before',
            'capacity_after': 'Stage capacity after (setup adjusted)',
            'stage_work_covered_before': 'Work coverable before',
            'stage_work_covered_after': 'Work coverable after',
            'additional_stage_work_covered': 'Net change in coverable work',
            'capacity_load_pct_before': 'Capacity load before (%)',
            'capacity_load_pct_after': 'Capacity load after (%)',
            'estimated_clear_hours_before': 'Queue clearance before (h)',
            'estimated_clear_hours_after': 'Queue clearance after (h)',
        }).round(1)
        render_html_table(op_table)
        download_csv('Download operational impact estimates', op_table,
                     'labor_reallocation_operational_impact')

        if not operational_impact['transfers_ranked'].empty:
            st.markdown('**Which recommended transfers offer the most stage-work coverage per employee?**')
            ranked_moves = operational_impact['transfers_ranked'][[
                'priority_rank', 'from_department', 'to_department',
                'employees_to_transfer', 'marginal_stage_work_gain',
                'gain_per_transferred_employee',
            ]].rename(columns={
                'priority_rank': 'Impact rank (among suggested moves)',
                'from_department': 'From', 'to_department': 'To',
                'employees_to_transfer': 'Move employees',
                'marginal_stage_work_gain': 'Additional stage work coverable',
                'gain_per_transferred_employee': 'Stage work gained / moved employee',
            }).round(1)
            render_html_table(ranked_moves)
            download_csv('Download ranked transfer impact', ranked_moves,
                         'labor_reallocation_ranked_moves')
        else:
            st.info('No suggested transfers to rank for this warehouse and shift.')
        st.info('**Interpretation:** These are simulated, stage-specific capacity estimates—not '
                'measured labor productivity, extra shipments, guaranteed delay reductions, or cost savings. '
                'Handoff time is a scenario assumption. Ranked moves are from the existing safe transfer '
                'plan; the ranking does not optimize or authorize personnel movements.')

        if not modeled['transfers'].empty:
            for move in modeled['transfers'].itertuples(index=False):
                st.warning(f'📣 **Transfer recommendation:** Move **{move.employees_to_transfer}** '
                           f'employee(s) from **{move.from_department} → {move.to_department}** '
                           f'within **{move.warehouse_name}, {move.shift_name}**.')
        else:
            st.info('No available within-shift transfers for this scenario.')
        if int(modeled['summary']['shortage_after'].sum()):
            st.error('Some departments remain understaffed after all safe modeled transfers. '
                     'Review overtime, additional staff, revised priorities, or process constraints.')
        elif int(modeled['summary']['surplus_after'].sum()):
            st.info('Excess coverage remains, but no remaining department is short. '
                    'Do not transfer employees solely to make staffing counts equal.')
        st.caption('**Formula:** required employees = max(minimum coverage, ceil(remaining units × '
                   '(1 + contingency) ÷ (units/person-hour × hours left × productive fraction))). '
                   'The tool never recommends reducing a donor below its calculated requirement. '
                   'Managers must confirm breaks, handoffs, equipment, safety, local policy, and actual WMS queues before acting.')

with tab_forecast:
    st.subheader('Demand forecasting & proactive operational watches')
    st.caption('Future order intake is estimated from historical created-at events. The application uses a '
               'walk-forward backtested weekday or rolling-average model per warehouse. Predictions are '
               'illustrative, not guaranteed outcomes; no live WMS feed or notifications are connected.')
    horizon = st.radio('Forecast horizon', [7, 14], index=0, horizontal=True, format_func=lambda n: f'{n} days')
    try:
        fcast = prepare_forecast(tables, selected_ids, end_date, horizon)
        scenario = prepare_capacity_scenario(tables, fcast)
        alerts = prepare_alerts(tables, selected_ids, fcast, scenario)
    except ValueError as exc:
        st.warning(f'Not enough historical data to produce a forecast: {exc}')
    else:
        total_pred = int(fcast['totals']['forecast_orders'].sum())
        high_count = int(alerts['severity'].isin(['Critical', 'High']).sum()) if not alerts.empty else 0
        capacity_watches = int(alerts['category'].eq('Illustrative capacity gap').sum()) if not alerts.empty else 0
        t1, t2, t3, t4 = st.columns(4)
        t1.metric(f'Forecast incoming orders ({horizon}d)', f'{total_pred:,}')
        t2.metric('Avg. forecast orders/day', f'{total_pred / horizon:,.1f}')
        t3.metric('High / critical watches', f'{high_count:,}')
        t4.metric('Capacity watch entries', f'{capacity_watches:,}')
        st.caption(f'Forecasts begin {fcast["totals"]["date"].min():%b %d, %Y} UTC. '
                   f'Training ends on the last FULL day {fcast["last_complete_day"]:%b %d, %Y} UTC. '
                   'The partial latest day is excluded from training. All forecasts are out-of-sample, '
                   'but the synthetic data may not resemble real warehouse demand.')

        st.subheader('Historical intake and forecast')
        recent = fcast['history'].groupby('date', as_index=False)['actual_orders'].sum()
        future = fcast['totals']
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=future['date'], y=future['upper_orders'],
                                 line=dict(width=0), hoverinfo='skip', showlegend=False))
        fig.add_trace(go.Scatter(x=future['date'], y=future['lower_orders'],
                                 fill='tonexty', fillcolor='rgba(71,125,205,.16)',
                                 line=dict(width=0), name='Historical-error band', hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=recent['date'], y=recent['actual_orders'],
                                 name='Historical actual intake', mode='lines+markers',
                                 line=dict(color='#16A085')))
        fig.add_trace(go.Scatter(x=future['date'], y=future['forecast_orders'],
                                 name='Forecast intake', mode='lines+markers',
                                 line=dict(color='#477DCD', dash='dash')))
        fig.update_xaxes(title_text='Date (UTC)')
        fig.update_yaxes(title_text='Incoming orders / day', rangemode='tozero')
        st.plotly_chart(chart_style(fig), use_container_width=True)
        st.caption('Shaded band = point forecast ± 80th percentile of past absolute daily holdout errors '
                   '(summed across selected warehouses). It is a heuristic error envelope, '
                   'NOT a calibrated 80% probability interval.')

        f1, f2 = st.columns([3, 2])
        with f1:
            st.subheader('Daily demand outlook')
            show_forecast = fcast['by_warehouse'][[
                'date', 'warehouse_name', 'forecast_orders', 'lower_orders', 'upper_orders'
            ]].rename(columns={'date': 'Day (UTC)', 'warehouse_name': 'Warehouse',
                              'forecast_orders': 'Forecast orders', 'lower_orders': 'Lower guide',
                              'upper_orders': 'Upper guide'})
            render_html_table(show_forecast)
            download_csv('Download daily forecasts', show_forecast, 'step6_daily_forecast')
        with f2:
            st.subheader('Backtest diagnostics')
            st.caption('One-day-ahead predictions evaluated on the final 28 full historical days. '
                       'The method with lower mean absolute error is selected independently for each warehouse.')
            display_accuracy = fcast['accuracy'][[
                'warehouse_name', 'selected_method', 'mae_orders', 'wape_pct', 'backtest_days'
            ]].rename(columns={'warehouse_name': 'Warehouse', 'selected_method': 'Method',
                              'mae_orders': 'MAE (orders/day)', 'wape_pct': 'WAPE %',
                              'backtest_days': 'Holdout days'})
            render_html_table(display_accuracy)
            download_csv('Download backtest details', fcast['backtest'], 'step6_backtest')

        st.divider()
        st.subheader('Workload and direct labor scenario')
        st.caption('This scenario assumes forecast incoming orders become same-day stage workload, '
                   'with a historical average unit mix and observed process rate. Actual staffing '
                   'depends on backlog, order mix, process lead time, shift coverage, and service levels. '
                   'Usual hours means the last 28 complete days of recorded synthetic labor; '
                   'it is NOT a future staffing schedule.')
        selected_stage = st.selectbox('Stage to estimate', list(PROCESSES), key='forecast_stage')
        stage = scenario.loc[scenario['process_name'].eq(selected_stage)].copy()
        stage_view = stage[[
            'date', 'warehouse_name', 'forecast_orders', 'assumed_units_per_order',
            'historical_stage_units_per_hour', 'required_labor_hours',
            'usual_daily_labor_hours', 'labor_gap_hours'
        ]].rename(columns={
            'date': 'Day (UTC)', 'warehouse_name': 'Warehouse', 'forecast_orders': 'Incoming orders forecast',
            'assumed_units_per_order': 'Avg units/order',
            'historical_stage_units_per_hour': 'Stage units/labor hr',
            'required_labor_hours': 'Estimated needed labor h',
            'usual_daily_labor_hours': 'Usual recorded labor h',
            'labor_gap_hours': 'Illustrative labor gap h',
        })
        if stage.empty or stage['required_labor_hours'].isna().all():
            st.info('Not enough stage history for a labor demand scenario.')
        else:
            by_day = stage.groupby('date', as_index=False)[
                ['required_labor_hours', 'usual_daily_labor_hours']].sum(min_count=1)
            by_day = by_day.melt(id_vars='date', var_name='Measure', value_name='Hours')
            by_day['Measure'] = by_day['Measure'].replace({
                'required_labor_hours': 'Estimated required labor',
                'usual_daily_labor_hours': 'Usual recorded labor',
            })
            fig = px.bar(by_day, x='date', y='Hours', color='Measure', barmode='group',
                         labels={'date': 'Date (UTC)'})
            st.plotly_chart(chart_style(fig), use_container_width=True)
        render_html_table(stage_view)
        download_csv('Download full process workload scenario', scenario, 'step6_labor_scenario')

        st.divider()
        st.subheader('Proactive operational watches')
        st.caption('Rule-based alerts, not model-estimated failure probabilities. Overdue and upcoming '
                   'orders use the selected as-of cutoff. Inventory checks use the latest snapshot only; '
                   'historical views intentionally omit inventory watches. No emails, escalations, or '
                   'actions are automatically sent.')
        if alerts.empty:
            st.success('No watches were triggered by these synthetic data and rules.')
        else:
            a1, a2 = st.columns(2)
            chosen_category = a1.multiselect('Watch category', sorted(alerts['category'].unique()),
                                             default=sorted(alerts['category'].unique()), key='alert_cats')
            chosen_severity = a2.multiselect('Watch severity', ['Critical', 'High', 'Medium', 'Low'],
                                             default=['Critical', 'High', 'Medium', 'Low'], key='alert_severities')
            subset = alerts.loc[alerts['category'].isin(chosen_category)
                                & alerts['severity'].isin(chosen_severity)].copy()
            st.write(f'**{len(subset):,}** rule-based watch entries match the filters.')
            show_alerts = subset.rename(columns={
                'severity': 'Severity', 'category': 'Watch type', 'warehouse': 'Warehouse',
                'reference': 'Reference', 'effective_date': 'As of / forecast day (UTC)',
                'explanation': 'Reason', 'action': 'Suggested review',
            })
            render_html_table(show_alerts)
            download_csv('Download all selected watches', show_alerts, 'step6_proactive_watches')
            with st.expander('Watch rule definitions'):
                st.markdown('''
- **Overdue order:** unshipped, non-cancelled order past the promised ship timestamp.
  High if under 24 hours overdue; Critical if 24+ hours overdue.
- **Upcoming deadline:** unshipped order due within 24 hours; High if due in 8 hours or less,
  otherwise Medium. This is a watchlist, not an assertion that the order will ship late.
- **Inventory reorder:** latest available quantity at/below reorder point; current snapshot only.
- **Capacity watch:** expected workload exceeds usual recorded direct labor hours by more than
  15% and more than 1 hour. High at 30% or more; otherwise Medium.
''')
        st.info('Synthetic data and heuristics only. Validate forecast error and alert thresholds '
                'with operations stakeholders before applying to real decisions.')


with tab_risk:
    st.subheader('Order delay-risk intelligence & action queue')
    st.caption(
        'An explainable historical-cohort score supports triage for orders approaching their shipment promise. '
        'Orders already overdue are labeled as breaches, not predicted outcomes. '
        'Potential drivers are clues to investigate—not proven root causes.'
    )
    lookahead = st.radio('Upcoming-order window', [24, 48, 72], index=1,
                          format_func=lambda n: f'{n} hours', horizontal=True, key='risk_window')
    try:
        intel = prepare_risk_intelligence(tables, selected_ids, end_date, horizon_hours=lookahead)
    except ValueError as exc:
        st.warning(f'Risk analysis unavailable: {exc}')
    else:
        summary = intel['summary']
        m1, m2, m3, m4 = st.columns(4)
        m1.metric('Open orders in scope', pretty_number(summary['open_in_window']))
        m2.metric('Already overdue', pretty_number(summary['already_overdue']))
        m3.metric(f'Upcoming (within {lookahead}h)', pretty_number(summary['upcoming']))
        m4.metric('Escalate / urgent review', pretty_number(summary['urgent_or_escalate']))
        st.caption(f'As of {intel["as_of"]:%Y-%m-%d %H:%M} UTC. '
                   'Latest available stock signals and current order statuses are used only at the latest dataset snapshot. '
                   'Older as-of views do not reconstruct historical inventory or order statuses.')
        queue = intel['orders']
        st.subheader('Prioritized management action queue')
        if queue.empty:
            st.success('No open orders past promise or due during the chosen look-ahead window.')
        else:
            f1, f2, f3 = st.columns(3)
            tier_options = ['Escalate', 'Urgent review', 'Priority review', 'Monitor']
            chosen_tiers = f1.multiselect('Action priority', tier_options,
                                         default=tier_options, key='risk_tier')
            chosen_states = f2.multiselect('Deadline state', ['Already overdue', 'Upcoming'],
                                           default=['Already overdue', 'Upcoming'], key='risk_state')
            chosen_wh = f3.multiselect('Warehouse', sorted(queue['warehouse_name'].dropna().unique()),
                                        default=sorted(queue['warehouse_name'].dropna().unique()),
                                        key='risk_wh')
            filtered_queue = queue.loc[queue['action_tier'].isin(chosen_tiers)
                                       & queue['deadline_state'].isin(chosen_states)
                                       & queue['warehouse_name'].isin(chosen_wh)]
            show = filtered_queue[[
                'order_id', 'warehouse_name', 'priority', 'deadline_state',
                'hours_to_deadline', 'risk_score_pct', 'risk_band', 'units_ordered',
                'action_tier', 'review_signals', 'suggested_action'
            ]].rename(columns={
                'order_id': 'Order ID', 'warehouse_name': 'Warehouse', 'priority': 'Order class',
                'deadline_state': 'Deadline state', 'hours_to_deadline': 'Hours to deadline',
                'risk_score_pct': 'Historical score (%)', 'risk_band': 'Score category',
                'units_ordered': 'Units', 'action_tier': 'Action tier',
                'review_signals': 'Signals to verify', 'suggested_action': 'Recommended follow-up',
            })
            show['Historical score (%)'] = show['Historical score (%)'].map(
                lambda x: f'{x:.1f}' if pd.notna(x) else '— (already late)'
            )
            st.write(f'**{len(show):,} orders** match the triage filters.')
            render_html_table(show)
            download_csv('Download management action queue', show, 'step7_action_queue')
            if not filtered_queue.empty:
                st.subheader('Investigate an order')
                choice = st.selectbox('Order to review', filtered_queue['order_id'].tolist(), key='risk_order')
                record = filtered_queue.loc[filtered_queue['order_id'].eq(choice)].iloc[0]
                c1, c2, c3 = st.columns(3)
                c1.metric('Time to deadline', f'{record["hours_to_deadline"]:,.1f} h')
                c2.metric('Status', record['deadline_state'])
                c3.metric('Action tier', record['action_tier'])
                st.markdown('**Evidence to verify**')
                st.write(record['review_signals'])
                st.markdown('**Recommended next action**')
                st.write(record['suggested_action'])
                if record['deadline_state'] == 'Already overdue' and intel['is_latest_snapshot']:
                    st.info('The public demo does not allow case assignment, status updates, '
                            'root-cause edits, or corrective-action changes.')
        st.divider()
        a, b = st.columns([3, 2])
        with a:
            st.subheader('Diagnostic signals observed')
            signals = intel['diagnostic_counts']
            if signals.empty:
                st.info('No diagnostic signals for the selected warehouse(s).')
            else:
                fig = px.bar(signals.sort_values('orders'), x='orders', y='signal', orientation='h',
                             labels={'orders': 'Orders', 'signal': 'Possible issue / signal'},
                             color_discrete_sequence=['#4B83D0'])
                fig.update_layout(height=370, margin=dict(l=0, r=15, t=15, b=10))
                st.plotly_chart(fig, use_container_width=True)
        with b:
            st.subheader('How to interpret the result')
            st.markdown(
                '- **Already late:** the deadline was breached as of the cutoff; no predictive score is shown.\n'
                '- **Historical score:** a smoothed late-order cohort rate using warehouse/priority, '
                'promised lead time, and order units. It is **not a calibrated probability**.\n'
                '- **Action tier:** orders already late and those near their deadline get priority '
                'independently of the historical score.\n'
                '- **Potential drivers:** operational review signals, not causal findings or verified defects.\n'
                '- **No notifications:** recommended follow-ups are suggestions only; nothing is sent.'
            )
        st.divider()
        st.subheader('Historical out-of-time evaluation')
        evaluation = intel['evaluation']
        e1, e2, e3, e4 = st.columns(4)
        e1.metric('Evaluated orders', pretty_number(evaluation['evaluation_orders']))
        e2.metric('Observed late rate', pretty_number(evaluation['evaluation_late_pct'], '%', decimals=2))
        e3.metric('Cohort Brier score', f'{evaluation["brier_score"]:.4f}')
        e4.metric('Constant-rate baseline', f'{evaluation["baseline_brier"]:.4f}')
        if evaluation['baseline_better']:
            st.warning('The simple historical late-rate baseline performed as well as or better than '
                       'the cohort score on this holdout. Treat risk tiers as unvalidated prioritization clues.')
        else:
            st.info('The cohort score marginally improved Brier error against a constant historical-rate '
                    'baseline on this synthetic holdout. This is not evidence of real-world predictive validity.')
        st.caption(f'Model fitted before {evaluation["holdout_start"]:%Y-%m-%d %H:%M} UTC; '
                   f'holdout promises observed through {evaluation["holdout_end"]:%Y-%m-%d %H:%M} UTC. '
                   f'Train: {evaluation["training_orders"]:,} matured orders, '
                   f'holdout: {evaluation["evaluation_orders"]:,} orders. Lower Brier is better. '
                   'This simulates pre-deadline scoring and is NOT a randomized deployment test.')
        calibration = intel['calibration']
        if not calibration.empty:
            plot_frame = calibration.melt(id_vars='score_band',
                                          value_vars=['average_score', 'actual_late_rate'],
                                          var_name='Measure', value_name='Percentage')
            plot_frame['Measure'] = plot_frame['Measure'].map({
                'average_score': 'Mean score', 'actual_late_rate': 'Observed late rate'
            })
            fig = px.bar(plot_frame, x='score_band', y='Percentage', color='Measure', barmode='group',
                         labels={'score_band': 'Score category', 'Percentage': 'Percent'},
                         title='Holdout: predicted score versus observed late rate')
            st.plotly_chart(chart_style(fig), use_container_width=True)
            with st.expander('Evaluation counts by score category'):
                render_html_table(calibration.round(2).rename(columns={
                    'score_band': 'Category', 'orders': 'Evaluated orders',
                    'average_score': 'Mean score (%)', 'actual_late_rate': 'Late outcomes (%)'
                }))
        st.caption('The underlying events are simulated, and actual root causes have not been labeled. '
                   'Order status and cancellation changes are not event-sourced; historical reconstructions '
                   'are indicative only. Validate definitions, thresholds, data quality and fairness before '
                   'using a system like this for real operating decisions.')

st.divider()
st.caption('Public read-only demonstration • UTC reporting • Frozen synthetic data • Version 1.2.0 '
           '(HTML tables + productivity + forecasts + delay-risk triage)')


