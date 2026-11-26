# Warehouse Operations Control Tower — Public Read-only Demo (Step 10)

An **8-tab Streamlit demonstration** of warehouse and distribution-center operations performance. The package contains **only synthetic warehouse records** in a frozen SQLite database, with historical data through **November 12, 2026 (UTC)**. This is not a live WMS feed or a production service.

## What visitors can do

Explore the Executive Brief, Overview, Overdue Orders, Quality Control, Inventory, Productivity, Forecast & Alerts, and Delay Risk & Triage tabs. Visitors can filter warehouse/date/shift, review synthetic operational performance, export reports, and change demonstration scenario reference values **in their own session only**.

## What visitors cannot do

No uploading, record editing, case assignment, database synchronization, operational alerts sent externally, logins, or admin functions. The public package excludes the original write-capable exception workflow, data ingestion module, migration utilities, and import history. The reporting layer connects using SQLite's **read-only and immutable** modes. The packaged DB excludes case notes, case-event audit trails, and import records. The original private Step 9 application remains a separate project; **do not deploy that version as public**.

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
- There is no automatic live time refresh. A selected date in the historical dataset is **not today's operational state**.
- The source data is simulated. Any forecast, heuristic alert, risk score, reference or recommendation is illustrative, not a guaranteed decision rule or validated enterprise forecast.
- This project retains the PyArrow compatibility workaround: operational tables use escaped HTML instead of Streamlit dataframe widgets.

## Package files

```
app.py                      # 8-tab public UI: no edits or uploads
analytics.py                # UTC KPI engine + immutable SQLite reader
productivity.py             # stage/shift productivity metrics
forecasting.py              # backtested demand scenario + rule-based alerts
risk_intelligence.py        # retrospective risk triage and evaluation
executive.py                # management summary and printable brief
warehouse_ops_demo.db       # frozen synthetic data (no case or import audits)
requirements.txt            # Python dependencies
.streamlit/config.toml      # optional app configuration
.gitignore                  # excludes local secrets and sidecar DB files
tests/test_public_demo.py    # read-only checks
```

## Future private product

Keep the local/private Step 9 app separate. A multi-user version needs authentication, authorization, a managed database with backups and migrations, audit controls, import governance, and tests before handling operational data. None of those enterprise controls are claimed for this public demo.
