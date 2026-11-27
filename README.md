# Warehouse Operations Control Tower — Public Demo + Labor Balancing Impact (Step 17)

A **9-tab Streamlit demonstration** of warehouse and distribution-center operations performance. The package contains **only synthetic warehouse records** in a frozen SQLite database, with historical data through **November 12, 2026 (UTC)**. This is not a live WMS feed or a production service.

## What visitors can do

Explore the Executive Brief, Overview, Overdue Orders, Quality Control, Inventory, Productivity, **Labor Balancing**, Forecast & Alerts, and Delay Risk & Triage tabs. Visitors can filter warehouse/date/shift, review synthetic operational performance, export reports, and change demonstration scenario reference values **in their own session only**.

## What visitors cannot do

No uploading, persistent record editing, case assignment, database synchronization, external alert delivery, logins, or admin functions. The Labor Balancing tab allows what-if inputs, but never writes or assigns staff. The public package excludes the original write-capable exception workflow, data ingestion module, migration utilities, and import history. The reporting layer connects using SQLite's **read-only and immutable** modes. The packaged DB excludes case notes, case-event audit trails, and import records. The original private Step 9 application remains a separate project; **do not deploy that version as public**.

## Local test in VS Code

Extract this folder and run:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open http://localhost:8501. The default report covers the latest 30 calendar days in the frozen dataset; all timestamps are UTC. The expected latest snapshot is 2026-11-12. You may use the test script for a non-GUI smoke test:

```powershell
python -m pip install pytest
python -m pytest tests -q
```

> The Streamlit interface was not run in this project-building environment; visually confirm tabs, tables, charts, CSV downloads, and forecast behavior locally and on the hosted app.

## Deploy publicly using GitHub + Streamlit Community Cloud

1. On GitHub, create a **new empty repository** (suggested: `warehouse-operations-control-tower-demo`) and copy its HTTPS repository URL. Do not initialize it with a README since one is already included.
2. **Use Git in VS Code or the terminal**, not the GitHub browser file-upload page. The synthetic SQLite file is around **44 MB**, which exceeds GitHub's **25 MiB/browser-file upload limit** but is below its 100 MiB Git push limit. In a terminal opened **inside this extracted folder**, run:

   ```powershell
   git init
   git add .
   git commit -m "Publish read-only warehouse operations demo"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/warehouse-operations-control-tower-demo.git
   git push -u origin main
   ```

   Replace `YOUR_USERNAME` and the repository name with your actual GitHub details. If Git prompts you to sign in, use its browser-authentication flow; do not commit tokens or passwords. If your repo already has an initial commit, use the standard clone/commit/push workflow instead of blindly overwriting history. **Do not upload your Step 9 project**, user-edited database, secrets, or employer data.
3. Open https://share.streamlit.io, sign in with GitHub, and choose **Create app**.
4. Select your repository and branch, set the **main file path to `app.py`**, and optionally choose a custom subdomain. Use **Python 3.12** in Advanced settings if offered; no secrets are needed for this frozen public demo.
5. Deploy and open the generated `*.streamlit.app` link. Check all tabs on desktop and mobile. Public visibility can be controlled from app settings. Add the deployed demo URL and GitHub repository to your portfolio and project resume, with the label **synthetic demo**.

See current official docs: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

## Data architecture / security notes

- `warehouse_ops_demo.db` is a synthetic, frozen SQLite database; the app only queries it using `file:...?...mode=ro&immutable=1` and `PRAGMA query_only=ON`.
- All user-facing controls are read-only filters or download actions. There are **no file uploaders and no save buttons** in the public app.
- Source code and database in a **public GitHub repository are publicly downloadable**. This is safe only because these records are synthetic and have no personal or corporate confidential data.
- Community Cloud is for demonstration. Resource limits, availability and persistent-write limitations mean it is not suitable as the present architecture for production warehouse operations.
- There is no automatic live time refresh. A selected date in the historical dataset is **not today's operational state**. The standalone staffing scenario is labeled as frozen on November 12, 2026, and is not inferred from labor-shift hour totals.
- The source data is simulated. Any forecast, heuristic alert, risk score, reference or recommendation is illustrative, not a guaranteed decision rule or validated enterprise forecast.
- This project retains the PyArrow compatibility workaround: operational tables use escaped HTML instead of Streamlit dataframe widgets.

## Package files

```
app.py                      # 9-tab public UI: no persistent edits or uploads
analytics.py                # UTC KPI engine + immutable SQLite reader
productivity.py             # stage/shift productivity metrics
labor_balancing.py          # explainable cross-trained staffing transfer recommendations
operational_impact.py       # handoff-aware stage-capacity and queue-clearance model
data/labor_balance_scenarios.csv  # synthetic headcounts and remaining stage workload
forecasting.py              # backtested demand scenario + rule-based alerts
risk_intelligence.py        # retrospective risk triage and evaluation
executive.py                # management summary and printable brief
warehouse_ops_demo.db       # frozen synthetic data (no case or import audits)
requirements.txt            # Python dependencies
.streamlit/config.toml      # optional app configuration
.gitignore                  # excludes local secrets and sidecar DB files
tests/test_public_demo.py    # read-only checks
tests/test_labor_balancing.py # labor transfer and minimum-staffing tests
```

## Cross-trained labor balancing — management alert prototype

The **⚖️ Labor balancing** tab shows a management-level summary across the selected warehouses and shifts, and allows an interactive what-if simulation for one warehouse/shift. It assumes that staff are cross-trained across Picking, Packing, and Dispatch. **Transfers are recommendations only**, and may never cross warehouses or shifts.

### What it calculates

- **Current employees** is an explicit input headcount, **not** an estimate inferred from historical `labor_shifts` hours.
- **Remaining stage workload** is a synthetic or manually entered queue/work volume for each department.
- **Reference processing rate** starts from illustrative units per worker-hour, independently adjustable for each department.
- **Minimum staffing** is a separately adjustable safety/coverage floor per department.
- **Hours remaining**, **productive time fraction**, and **contingency buffer** are scenario assumptions, not validated labor policies.

For a stage, the formula is:

```
needed_staff = max(
    minimum_staff,
    ceil(remaining_stage_units * (1 + workload_buffer)
         / (units_per_worker_hour * hours_remaining * productive_fraction))
)
surplus = max(current_staff - needed_staff, 0)
shortage = max(needed_staff - current_staff, 0)
```

The recommender transfers the **maximum safe whole-person count** from a surplus department to a shortage department **within the same warehouse and shift**, prioritizing the largest shortages first. Donors cannot fall below their calculated requirement or their minimum coverage. If no donor can fill a shortage, the alert remains open. If staff are excess but no other department is short, the system **does not recommend unnecessary moves**.

### Illustrated output from the default scenario

With 6 hours remaining, 85% productive time, and a 10% workload contingency, the six synthetic warehouse/shift scenarios recommend **14 total transfers** across **five warehouse/shift groups**. For example:

- **Central NC, Shift A:** move **3** employees from **Picking → Packing**, with **1** modeled Packing employee shortage still unmet.
- **Central NC, Shift B:** move **3** employees from **Packing → Dispatch**.
- **Piedmont, Shift B:** move **3** employees from **Packing → Picking**.

These numbers are *not* current real-world overstaffing; they are deliberately designed demonstration inputs. The app compares the indicative stage-level work gap before and after reallocation, but **does not claim measured shipment increases or verified cost savings**. Picking, Packing, and Dispatch count the same underlying units at different stages; do not add their covered-unit figures as unique shipped units.

### What production integration would need

A live WMS/labor planning integration must supply verified on-duty staffing/headcount, work queues by stage, reliable measured process rates, employee availability and break constraints, minimum safe staffing, scan-to-work assignment, and department transfer approvals. The public demo sends **no push notifications or emails** and stores **no what-if modifications**.

## Future private product

Keep the local/private Step 9 app separate. A multi-user version needs authentication, authorization, a managed database with backups and migrations, audit controls, import governance, and tests before handling operational data. None of those enterprise controls are claimed for this public demo.


## Step 16 — Before and after labor reallocation

The Labor balancing tab includes a scenario comparison at both the all-shifts and chosen-shift levels. It charts departmental staffing before and after suggested transfers, shows required coverage and modeled stage-work coverage, and exports a before/after analysis. This is a planning simulation (not realized productivity gains). See `UPGRADE_NOTES.md` for deployment instructions.

## Step 17 — Labor reallocation operational impact (synthetic what-if)

The **⚖️ Labor balancing** tab now also shows **Step 17: projected operational impact** with a user-adjustable **handoff/setup delay** (default 15 minutes) before a transferred employee becomes productive in their destination department. The earlier Step 16 no-delay comparison remains visible as a reference; Step 17 explicitly accounts for the transfer delay and may show smaller gains.

### What is modeled

- **Stage processing capacity before/after** in stage-work units that could be handled with the assumed staffing, reference process rates, productive time fraction, buffer, and hours remaining.
- **Additional stage work coverable within the shift**, capped by remaining workload at each stage; these values **are not actual extra shipped orders or units**, since the same physical unit can be present at Picking, Packing, and Dispatch.
- **Stage capacity load (%):** remaining stage work / modeled processing capacity, so a reading above 100% means the department's workload exceeds its modeled remaining-shift capacity. This is **not measured employee utilization**.
- **Estimated time to clear each stage queue:** a simplified duration using fixed processing rates and a single handoff delay for incoming workers. The model does not estimate customer shipping SLA outcomes.
- **Queue risk before/after:** number of process-stage queues with work still uncovered by the end of the modeled shift, not number of late customer orders.
- **Impact-ranked transfer recommendations:** existing safe, same-warehouse/same-shift moves ranked by incremental stage work coverable **per transferred employee**. Ranking compares *suggested* moves; it does not recompute an optimal staffing allocation.

### Model mechanics

For each stage, capacity **before** transfers is `current_staff * hours_remaining * rate * productive_fraction / (1+buffer)`. Capacity **after** transfers is `((staff_after - incoming_workers) * hours_remaining + incoming_workers * max(0, hours_remaining - setup_minutes/60)) * rate * productive_fraction / (1+buffer)`.

For time-to-clear, existing workers are assumed available immediately and transferred workers start after the setup time. Covered work is the minimum of the stage's remaining workload and estimated processing capacity. Each stage is modeled independently; intermediate buffer constraints, transportation/handling delays, work-release sequencing, fatigue, machine outages, quality issues, and actual labor availability are not modeled.

### Default demonstration outcomes

At 6 hours remaining, 85% productive time, 10% buffer, and 15 minutes transfer setup, the six **synthetic** shift scenarios contain 14 recommended employee movements and **approximately 453.6 additional stage-work units coverable** under the model. The count of process-stage queues with uncovered work drops from **5 to 1**. These are illustrative what-if comparisons, **not observed production improvements**.

All changes are in browser session only; no personnel are moved and the public app does not store edits. See `UPGRADE_NOTES.md` for deployment and verification.
