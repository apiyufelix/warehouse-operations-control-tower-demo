"""Deterministic synthetic warehouse operations data for dashboard prototyping.

Run from project root: python scripts/generate_data.py
All timestamps are ISO-8601 in UTC (Z).
Inventory is a single current snapshot, not historical stock movement.
"""
from __future__ import annotations
import csv
import math
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
RNG = random.Random(20261112)
START = date(2024, 11, 1)
LAST_DAY = date(2026, 11, 11)
AS_OF = datetime(2026, 11, 12, 22, 0, tzinfo=timezone.utc)
WH = [
    {"warehouse_id": "WH_NC_01", "warehouse_name": "Central NC Fulfillment (Synthetic)", "region": "NC", "capacity_tier": "Large"},
    {"warehouse_id": "WH_NC_02", "warehouse_name": "Piedmont Distribution (Synthetic)", "region": "NC", "capacity_tier": "Medium"},
]
CATEGORIES = ["Apparel", "Footwear", "Accessories", "Home"]
PRODUCTS = []
for i in range(1, 41):
    cat = CATEGORIES[(i - 1) % 4]
    unit_price = round({"Apparel": 48, "Footwear": 88, "Accessories": 27, "Home": 60}[cat] * RNG.uniform(.65, 1.9), 2)
    PRODUCTS.append({"product_id": f"SKU{i:04d}", "product_name": f"{cat} Item {i:02d} (Synthetic)", "category": cat, "unit_price": f"{unit_price:.2f}"})


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def save(name: str, columns: list[str], rows: list[dict]):
    with (DATA / name).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{name}: {len(rows):,} rows")


def weighted_product():
    # Some popular SKUs sell more often, rather than every SKU selling equally.
    return RNG.choices(PRODUCTS, weights=[1.0 / (1 + i * .15) for i in range(len(PRODUCTS))], k=1)[0]


def poisson_draw(lam: float) -> int:
    # Exact algorithm adequate at this small daily volume.
    lbound, k, p = math.exp(-lam), 0, 1.
    while p > lbound:
        k += 1
        p *= RNG.random()
    return k - 1

orders = []
order_lines = []
shipments = []
inspections = []
order_n, line_n, shipment_n, inspection_n = 0, 0, 0, 0
calendar_day = START
while calendar_day <= LAST_DAY:
    weekday_factor = [.97, 1.05, 1.04, 1.07, 1.20, .66, .54][calendar_day.weekday()]
    november_december_peak = 1.58 if calendar_day.month in (11, 12) else 1.0
    spring_peak = 1.18 if calendar_day.month in (4, 5) else 1.0
    annual_growth = 1.00 + .09 * (calendar_day.year - 2024)
    seasonal_mult = weekday_factor * november_december_peak * spring_peak * annual_growth
    for wh, baseline in zip(WH, [22, 13]):
        count = poisson_draw(max(2.0, baseline * seasonal_mult))
        for _ in range(count):
            order_n += 1
            oid = f"ORD{order_n:07d}"
            created = datetime.combine(calendar_day, time(0), timezone.utc) + timedelta(minutes=RNG.randrange(5 * 60, 22 * 60))
            priority = RNG.choices(["Standard", "Expedited", "SameDay"], weights=[81, 16, 3], k=1)[0]
            sla_hours = {"Standard": 48, "Expedited": 24, "SameDay": 12}[priority]
            promised = created + timedelta(hours=sla_hours)
            # Shipping duration varies with warehouse load and urgency; peak periods are slower.
            base = {"Standard": 22, "Expedited": 12, "SameDay": 6}[priority]
            hours_to_ship = max(1.5, RNG.gauss(base, base * .36))
            if november_december_peak > 1 and RNG.random() < .19:
                hours_to_ship += RNG.uniform(14, 52)
            elif RNG.random() < .07:
                hours_to_ship += RNG.uniform(14, 70)
            planned_ship = created + timedelta(hours=hours_to_ship)
            status = "Shipped"
            if RNG.random() < .009:
                status = "Cancelled"
            elif RNG.random() < .0015 and created < AS_OF - timedelta(days=5):
                # Intentionally create a small set of unresolved, aging orders.
                status = "Exception Hold"
            elif planned_ship > AS_OF:
                status = RNG.choice(["Picking", "Packed", "Exception Hold"])
            actual_ship = planned_ship if status == "Shipped" else None
            orders.append({
                "order_id": oid, "warehouse_id": wh["warehouse_id"],
                "customer_id": f"CUST{RNG.randint(1, 850):04d}",
                "created_at": ts(created), "promised_ship_at": ts(promised),
                "priority": priority, "order_status": status,
            })
            n_lines = RNG.choices([1, 2, 3, 4, 5], weights=[32, 34, 20, 10, 4], k=1)[0]
            sku_ids = RNG.sample(range(len(PRODUCTS)), n_lines)
            total_qty = 0
            this_order_lines = []
            for sku_idx in sku_ids:
                p = PRODUCTS[sku_idx]
                line_n += 1
                qty = RNG.choices([1, 2, 3, 4, 5, 8, 10], weights=[28, 31, 18, 11, 7, 3, 2], k=1)[0]
                total_qty += qty
                line = {
                    "order_line_id": f"LINE{line_n:08d}", "order_id": oid,
                    "product_id": p["product_id"], "qty_ordered": qty, "unit_price": p["unit_price"]
                }
                order_lines.append(line)
                this_order_lines.append(line)
            if actual_ship is not None:
                shipment_n += 1
                shipments.append({
                    "shipment_id": f"SHP{shipment_n:07d}", "order_id": oid,
                    "warehouse_id": wh["warehouse_id"], "carrier": RNG.choices(["Carrier A", "Carrier B", "Carrier C"], weights=[44, 34, 22], k=1)[0],
                    "shipped_at": ts(actual_ship), "units_shipped": total_qty,
                })
            # Inspections are performed on a sample of order lines; failed cases are reworked if shipped.
            if RNG.random() < .68 and status != "Cancelled":
                for line in RNG.sample(this_order_lines, RNG.randint(1, min(2, len(this_order_lines)))):
                    inspection_n += 1
                    qty_checked = int(line["qty_ordered"])
                    defect_probability = .013 if november_december_peak == 1 else .024
                    defect_qty = sum(1 for _ in range(qty_checked) if RNG.random() < defect_probability)
                    qc_done = min(AS_OF, created + timedelta(hours=RNG.uniform(1, 8)))
                    is_failure = defect_qty > 0
                    inspections.append({
                        "inspection_id": f"QC{inspection_n:07d}", "order_id": oid,
                        "order_line_id": line["order_line_id"], "warehouse_id": wh["warehouse_id"],
                        "inspected_at": ts(qc_done), "qty_inspected": qty_checked,
                        "defect_qty": defect_qty,
                        "inspection_result": "Fail" if is_failure else "Pass",
                        "issue_type": (RNG.choice(["Damage", "Wrong SKU", "Labeling", "Packaging"]) if is_failure else "None"),
                        "resolution_status": ("Closed" if status == "Shipped" else "Open") if is_failure else "Not Required",
                    })
    calendar_day += timedelta(days=1)

# A single latest snapshot at warehouse/SKU grain; it is not an inventory movement ledger.
inventory = []
for wh in WH:
    for p in PRODUCTS:
        baseline = RNG.randint(80, 520)
        on_hand = max(0, round(baseline * RNG.uniform(.2, 1.6)))
        allocated = min(on_hand, round(on_hand * RNG.uniform(.05, .58)))
        reorder = RNG.randint(40, 135)
        inventory.append({
            "warehouse_id": wh["warehouse_id"], "product_id": p["product_id"],
            "snapshot_at": ts(AS_OF), "on_hand_qty": on_hand,
            "allocated_qty": allocated, "available_qty": on_hand - allocated,
            "reorder_point": reorder,
        })

save("warehouses.csv", list(WH[0]), WH)
save("products.csv", list(PRODUCTS[0]), PRODUCTS)
save("orders.csv", list(orders[0]), orders)
save("order_lines.csv", list(order_lines[0]), order_lines)
save("shipments.csv", list(shipments[0]), shipments)
save("quality_inspections.csv", list(inspections[0]), inspections)
save("inventory.csv", list(inventory[0]), inventory)
print("As-of UTC:", ts(AS_OF))
