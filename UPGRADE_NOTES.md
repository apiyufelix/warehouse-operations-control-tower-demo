# Step 17 — Labor Reallocation Impact Analysis

Adds read-only operational scenario estimates to the existing **⚖️ Labor balancing** tab. All previous features, nine tabs, and the PyArrow-compatible HTML tables remain.

## Update your existing GitHub/Streamlit project

1. **Back up the repository.** The patch changes `app.py`; if you've customized it since Step 16, merge these changes instead of overwriting your version.
2. Extract the Step 17 patch into the **repository root** (the directory containing `app.py`). It contains:
   - `app.py` (updated UI)
   - `operational_impact.py` (**new** scenario calculation module)
   - `tests/test_operational_impact.py` (**new** tests)
   - `UPGRADE_NOTES.md` (this file)
3. Do **not** replace or remove your existing `labor_balancing.py`, `warehouse_ops_demo.db`, `data/labor_balance_scenarios.csv`, or other modules. This patch builds on the previously working Step 16 application.
4. Test locally with:

   ```powershell
   python -m streamlit run app.py
   ```

   Select November 12, 2026 as the reporting end date, open **⚖️ Labor balancing**, select a warehouse and shift, and review **Step 17 — Projected operational impact**. Move the **handoff/setup delay** slider and verify the capacity and transfer-ranking tables update.
5. Optionally test from the project root (install `pytest` if necessary):

   ```powershell
   python -m pytest -q
   ```

6. Push exactly the app and new module/test (plus documentation if desired):

   ```powershell
   git add app.py operational_impact.py tests/test_operational_impact.py UPGRADE_NOTES.md
   git status
   git commit -m "Add Step 17 labor reallocation operational impact analysis"
   git push origin main
   ```

   The existing synthetic staffing CSV should remain tracked; if you cloned a project without it, copy and explicitly add **only the synthetic file** with `git add -f data/labor_balance_scenarios.csv`.

7. Verify the public Streamlit deployment after GitHub updates. Community Cloud may take time to redeploy; reboot from the app management panel only if needed.

## Interpretation and safety

- **Step 16**: frictionless staffing and work-coverage comparison.
- **Step 17**: fixed-rate processing-capacity scenario with a modeled setup delay for incoming staff.
- The setup time defaults to 15 minutes but is an **editable planning assumption**, not a measured transfer duration.
- **Stage-work volumes may count a unit at multiple departments**. Never claim the summed capacity gain represents extra distinct orders, shipments, cost savings, or achieved productivity gains.
- A stage whose workload exceeds shift-end capacity is a **work-queue risk**, not evidence that a customer's shipping commitment will be missed.
- The plan ranks existing recommendations by projected marginal stage-work gain per moved employee; it does **not** optimize all possible transfers or override required safety, breaks, qualifications, or supervisor approval.
- All inputs are synthetic, session-only, read-only, and do not change the SQLite database. No real staffing changes or automatic alerts are executed.
