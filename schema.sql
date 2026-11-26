PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id TEXT PRIMARY KEY,
    warehouse_name TEXT NOT NULL,
    region TEXT NOT NULL,
    capacity_tier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price NUMERIC NOT NULL CHECK(unit_price >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    customer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    promised_ship_at TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('Standard','Expedited','SameDay')),
    order_status TEXT NOT NULL CHECK(order_status IN ('Shipped','Cancelled','Picking','Packed','Exception Hold'))
);
CREATE TABLE IF NOT EXISTS order_lines (
    order_line_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty_ordered INTEGER NOT NULL CHECK(qty_ordered > 0),
    unit_price NUMERIC NOT NULL CHECK(unit_price >= 0),
    UNIQUE(order_id, product_id)
);
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(order_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    carrier TEXT NOT NULL,
    shipped_at TEXT NOT NULL,
    units_shipped INTEGER NOT NULL CHECK(units_shipped > 0)
);
CREATE TABLE IF NOT EXISTS quality_inspections (
    inspection_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    order_line_id TEXT NOT NULL REFERENCES order_lines(order_line_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    inspected_at TEXT NOT NULL,
    qty_inspected INTEGER NOT NULL CHECK(qty_inspected > 0),
    defect_qty INTEGER NOT NULL CHECK(defect_qty >= 0 AND defect_qty <= qty_inspected),
    inspection_result TEXT NOT NULL CHECK(inspection_result IN ('Pass','Fail')),
    issue_type TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK(resolution_status IN ('Not Required','Open','Closed'))
);
CREATE TABLE IF NOT EXISTS inventory (
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    snapshot_at TEXT NOT NULL,
    on_hand_qty INTEGER NOT NULL CHECK(on_hand_qty >= 0),
    allocated_qty INTEGER NOT NULL CHECK(allocated_qty >= 0),
    available_qty INTEGER NOT NULL CHECK(available_qty = on_hand_qty - allocated_qty),
    reorder_point INTEGER NOT NULL CHECK(reorder_point >= 0),
    PRIMARY KEY(warehouse_id,product_id,snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_promised ON orders(promised_ship_at);
CREATE INDEX IF NOT EXISTS idx_orders_status_wh ON orders(order_status,warehouse_id);
CREATE INDEX IF NOT EXISTS idx_order_lines_order ON order_lines(order_id);
CREATE INDEX IF NOT EXISTS idx_shipments_shipped ON shipments(shipped_at);
CREATE INDEX IF NOT EXISTS idx_qc_inspected ON quality_inspections(inspected_at);

-- Step 4: Persistent exception case workflow and audit trail.
-- Workflow timestamps track when the app detects/updates a case, not the historic
-- order date. The application ensures these tables exist at startup as well.
CREATE TABLE IF NOT EXISTS exception_cases (
    order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
    severity TEXT NOT NULL CHECK(severity IN ('Critical','High','Medium','Low')),
    workflow_status TEXT NOT NULL DEFAULT 'New'
      CHECK(workflow_status IN ('New','In Progress','Monitoring','Resolved')),
    assigned_owner TEXT NOT NULL DEFAULT '',
    root_cause TEXT NOT NULL DEFAULT '',
    corrective_action TEXT NOT NULL DEFAULT '',
    resolution_notes TEXT NOT NULL DEFAULT '',
    first_detected_at TEXT NOT NULL,
    sla_due_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    resolved_at TEXT,
    currently_overdue INTEGER NOT NULL DEFAULT 1 CHECK(currently_overdue IN (0,1))
);
CREATE TABLE IF NOT EXISTS exception_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    event_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status_severity ON exception_cases(workflow_status,severity);
CREATE INDEX IF NOT EXISTS idx_cases_current ON exception_cases(currently_overdue);
CREATE INDEX IF NOT EXISTS idx_events_order ON exception_events(order_id,event_at);

-- Step 5: Synthetic process activity and direct labor time records.
-- Each shipment contributes one completed Picking, Packing, and Dispatch event.
-- These stage units must NOT be summed together to describe unique units shipped.
CREATE TABLE IF NOT EXISTS process_activities (
    activity_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    process_name TEXT NOT NULL CHECK(process_name IN ('Picking','Packing','Dispatch')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    shift_name TEXT NOT NULL CHECK(shift_name IN ('Shift A (00–08 UTC)', 'Shift B (08–16 UTC)', 'Shift C (16–24 UTC)')),
    units_processed INTEGER NOT NULL CHECK(units_processed > 0),
    queue_minutes REAL NOT NULL CHECK(queue_minutes >= 0),
    processing_minutes REAL NOT NULL CHECK(processing_minutes >= 0),
    UNIQUE(order_id, process_name)
);
CREATE TABLE IF NOT EXISTS labor_shifts (
    labor_id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    process_name TEXT NOT NULL CHECK(process_name IN ('Picking','Packing','Dispatch')),
    shift_name TEXT NOT NULL CHECK(shift_name IN ('Shift A (00–08 UTC)', 'Shift B (08–16 UTC)', 'Shift C (16–24 UTC)')),
    shift_start_at TEXT NOT NULL,
    shift_end_at TEXT NOT NULL,
    planned_labor_hours REAL NOT NULL CHECK(planned_labor_hours >= 0),
    actual_labor_hours REAL NOT NULL CHECK(actual_labor_hours >= 0),
    UNIQUE(warehouse_id, process_name, shift_start_at)
);
CREATE INDEX IF NOT EXISTS idx_activity_complete ON process_activities(completed_at);
CREATE INDEX IF NOT EXISTS idx_activity_wh_process ON process_activities(warehouse_id,process_name);
CREATE INDEX IF NOT EXISTS idx_labor_start ON labor_shifts(shift_start_at);
CREATE INDEX IF NOT EXISTS idx_labor_wh_process ON labor_shifts(warehouse_id,process_name);

-- Step 9: import history. Existing installations initialize these tables only
-- when a batch is explicitly approved. No existing case history is reset.
CREATE TABLE IF NOT EXISTS data_import_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,
    batch_hash TEXT NOT NULL,
    source_label TEXT NOT NULL,
    inserted_total INTEGER NOT NULL,
    unchanged_total INTEGER NOT NULL,
    updated_total INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS data_import_details (
    run_id INTEGER NOT NULL REFERENCES data_import_runs(run_id),
    table_name TEXT NOT NULL,
    received_rows INTEGER NOT NULL,
    inserted_rows INTEGER NOT NULL,
    unchanged_rows INTEGER NOT NULL,
    updated_rows INTEGER NOT NULL,
    PRIMARY KEY(run_id,table_name)
);
