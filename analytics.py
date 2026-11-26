"""Warehouse performance calculations for the Step 3 Streamlit dashboard.

Data is a synthetic demonstration. Date windows and as-of snapshots are UTC.
Order-intake and shipment-throughput measures are separate event populations.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

TABLES = (
    "warehouses", "products", "orders", "order_lines", "shipments",
    "quality_inspections", "inventory", "process_activities", "labor_shifts",
)
DATETIME_COLUMNS = {
    "orders": ("created_at", "promised_ship_at"),
    "shipments": ("shipped_at",),
    "quality_inspections": ("inspected_at",),
    "inventory": ("snapshot_at",),
    "process_activities": ("started_at", "completed_at"),
    "labor_shifts": ("shift_start_at", "shift_end_at"),
}


def load_tables(database_path: str | Path) -> dict[str, pd.DataFrame]:
    """Read SQLite tables and parse ISO timestamps as timezone-aware UTC values."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    # Immutable read-only URI: no creation, writes, journal changes, or mutating connections.
    # Package ships a frozen database and never enables uploads or edits.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        result = {name: pd.read_sql_query(f"SELECT * FROM {name}", connection) for name in TABLES}
    for name, columns in DATETIME_COLUMNS.items():
        for column in columns:
            result[name][column] = pd.to_datetime(result[name][column], utc=True, errors="raise")
    return result


def reporting_cutoff(tables: dict[str, pd.DataFrame], end_date: date) -> pd.Timestamp:
    """Inclusive 'as-of' timestamp, capped at the available inventory/demo snapshot."""
    end_of_day = pd.Timestamp(end_date).tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    snapshot = tables["inventory"]["snapshot_at"].max()
    return min(end_of_day, snapshot)


def _date_start(day: date) -> pd.Timestamp:
    return pd.Timestamp(day).tz_localize("UTC")


def _scope(
    tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
    start_date: date, end_date: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    if start_date > end_date:
        raise ValueError("The start date cannot be after the end date")
    if not warehouse_ids:
        raise ValueError("Select at least one warehouse")
    as_of = reporting_cutoff(tables, end_date)
    orders = tables["orders"].loc[
        tables["orders"]["warehouse_id"].isin(warehouse_ids)
    ].copy()
    shipments = tables["shipments"][["order_id", "shipped_at", "units_shipped"]]
    orders = orders.merge(shipments, on="order_id", how="left", validate="one_to_one")
    # Shipment reporting is always based on shipment timestamps, not created_at.
    qc = tables["quality_inspections"].loc[
        tables["quality_inspections"]["warehouse_id"].isin(warehouse_ids)
        & tables["quality_inspections"]["inspected_at"].between(_date_start(start_date), as_of)
    ].copy()
    # Step 9 keeps historical snapshots; KPIs must use only the newest complete snapshot.
    latest_inventory_at = tables["inventory"]["snapshot_at"].max()
    inventory = tables["inventory"].loc[
        tables["inventory"]["warehouse_id"].isin(warehouse_ids)
        & tables["inventory"]["snapshot_at"].eq(latest_inventory_at)
    ].copy()
    return orders, qc, inventory, tables["warehouses"], as_of


def prepare_dashboard(
    tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
    start_date: date, end_date: date,
) -> dict:
    """Compute dashboard KPI definitions, daily trends, and supporting tables."""
    orders, qc, inventory, wh, as_of = _scope(tables, warehouse_ids, start_date, end_date)
    start = _date_start(start_date)
    received = orders["created_at"].between(start, as_of)
    shipped = orders["shipped_at"].between(start, as_of)
    is_due = (
        orders["promised_ship_at"].between(start, as_of)
        & orders["order_status"].ne("Cancelled")
    )
    on_time = (
        orders["shipped_at"].notna()
        & orders["shipped_at"].le(orders["promised_ship_at"])
        & orders["shipped_at"].le(as_of)
    )
    open_asof = (
        orders["created_at"].le(as_of)
        & orders["order_status"].ne("Cancelled")
        & (orders["shipped_at"].isna() | orders["shipped_at"].gt(as_of))
    )
    overdue_asof = open_asof & orders["promised_ship_at"].lt(as_of)

    shipped_orders = orders.loc[shipped].copy()
    shipped_orders["cycle_hours"] = (
        shipped_orders["shipped_at"] - shipped_orders["created_at"]
    ).dt.total_seconds() / 3600
    due_count = int(is_due.sum())
    inspections_qty = int(qc["qty_inspected"].sum())

    metrics = {
        "orders_received": int(received.sum()),
        "orders_shipped": int(shipped.sum()),
        "open_backlog_asof": int(open_asof.sum()),
        "overdue_open_asof": int(overdue_asof.sum()),
        "orders_due": due_count,
        "on_time_due_orders": int((is_due & on_time).sum()),
        "on_time_shipping_pct": (100.0 * int((is_due & on_time).sum()) / due_count) if due_count else None,
        "average_cycle_hours": (float(shipped_orders["cycle_hours"].mean()) if len(shipped_orders) else None),
        "qc_units_inspected": inspections_qty,
        "qc_defect_units": int(qc["defect_qty"].sum()),
        "qc_defect_rate_pct": (100.0 * float(qc["defect_qty"].sum()) / inspections_qty) if inspections_qty else None,
        "skus_below_reorder": int((inventory["available_qty"] <= inventory["reorder_point"]).sum()),
    }

    # Group independent event series into one trend chart without conflating cohorts.
    days = pd.date_range(start=start.normalize(), end=as_of.normalize(), freq="D", tz="UTC")
    daily = pd.DataFrame({"date": days})
    intake_counts = (
        orders.loc[received].groupby(orders.loc[received, "created_at"].dt.normalize())
        .size().rename("Orders received")
    )
    ship_counts = (
        orders.loc[shipped].groupby(orders.loc[shipped, "shipped_at"].dt.normalize())
        .size().rename("Orders shipped")
    )
    daily = daily.join(intake_counts, on="date").join(ship_counts, on="date").fillna(0)
    daily[["Orders received", "Orders shipped"]] = daily[["Orders received", "Orders shipped"]].astype(int)

    # Order-level exception details. Order values are an estimate from order lines.
    order_line_totals = tables["order_lines"].assign(
        line_amount=lambda df: df["qty_ordered"] * df["unit_price"]
    ).groupby("order_id", as_index=False).agg(
        units_ordered=("qty_ordered", "sum"), order_value=("line_amount", "sum")
    )
    exceptions = orders.loc[overdue_asof, [
        "order_id", "warehouse_id", "priority", "created_at", "promised_ship_at"
    ]].copy()
    exceptions = exceptions.merge(order_line_totals, on="order_id", how="left", validate="one_to_one")
    exceptions["overdue_hours"] = ((as_of - exceptions["promised_ship_at"]).dt.total_seconds() / 3600).round(1)
    exceptions = exceptions.merge(wh[["warehouse_id", "warehouse_name"]], on="warehouse_id", how="left")
    exceptions = exceptions.sort_values("overdue_hours", ascending=False)

    # QC rate is based on inspected *units*, not number of inspection rows.
    issue_breakdown = qc.loc[qc["defect_qty"] > 0].groupby(
        "issue_type", as_index=False
    ).agg(defect_units=("defect_qty", "sum"), inspections=("inspection_id", "count"))
    issue_breakdown = issue_breakdown.sort_values("defect_units", ascending=False)
    recent_qc = qc.loc[qc["defect_qty"] > 0, [
        "inspection_id", "order_id", "warehouse_id", "inspected_at",
        "issue_type", "qty_inspected", "defect_qty", "resolution_status",
    ]].sort_values("inspected_at", ascending=False).head(200)

    inventory_view = inventory.merge(
        tables["products"][["product_id", "product_name", "category"]], on="product_id", how="left"
    ).merge(wh[["warehouse_id", "warehouse_name"]], on="warehouse_id", how="left")
    inventory_view["stock_flag"] = inventory_view.apply(
        lambda r: "Below/at reorder point" if r["available_qty"] <= r["reorder_point"] else "Above reorder point",
        axis=1,
    )
    inventory_view = inventory_view.sort_values(["stock_flag", "available_qty"])

    warehouse_report = []
    for _, warehouse in wh.loc[wh["warehouse_id"].isin(warehouse_ids)].iterrows():
        wid = warehouse["warehouse_id"]
        loc = orders["warehouse_id"].eq(wid)
        d = int((loc & is_due).sum())
        warehouse_report.append({
            "Warehouse": warehouse["warehouse_name"],
            "Received": int((loc & received).sum()),
            "Shipped": int((loc & shipped).sum()),
            "Due": d,
            "On-time shipping %": round(100 * int((loc & is_due & on_time).sum()) / d, 2) if d else None,
            "Overdue as of cutoff": int((loc & overdue_asof).sum()),
        })

    return {
        "as_of": as_of, "snapshot_as_of": tables["inventory"]["snapshot_at"].max(),
        "metrics": metrics, "daily": daily, "exceptions": exceptions,
        "issue_breakdown": issue_breakdown, "recent_qc": recent_qc,
        "inventory": inventory_view, "warehouse_report": pd.DataFrame(warehouse_report),
    }
