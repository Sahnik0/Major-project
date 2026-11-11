# Run & Operations Guide

This guide covers how to start, use, query, test, and maintain the **Local AI Data Analyst, Forecasting & Decision Engine** in development, production, and Docker environments.

> First time setting up the repo? Complete **[SETUP_GUIDE.md](./SETUP_GUIDE.md)** first.

---

## 1. Running Locally (Recommended for Development)

You will run **three local processes** (Ollama + FastAPI Backend + Next.js Frontend):

### Terminal 1 — Local LLM Service (Ollama)
If Ollama is not already running as a background system service (`systemctl status ollama`):
```bash
ollama serve
```
- **Endpoint:** `http://localhost:11434`

### Terminal 2 — FastAPI Analytical & Forecasting Backend
From the project root (`/path/to/Major project`):
```bash
make dev-api
# Equivalent to:
# .venv/bin/uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8000
```
- **API Base URL:** `http://localhost:8000/api/v1`
- **Interactive Swagger UI:** `http://localhost:8000/docs`
- **ReDoc Reference:** `http://localhost:8000/redoc`
- **Health Check:** `http://localhost:8000/api/v1/health`

### Terminal 3 — Next.js Interactive Web Workspace
From the project root:
```bash
make dev-web
# Equivalent to:
# cd apps/web && npm run dev
```
- **Web App URL:** Open **`http://localhost:3000`** in your browser.

---

## 2. Running in Production Mode (Without Hot-Reload Overhead)

```bash
# 1. Start FastAPI without reload
.venv/bin/uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --workers 1

# 2. Build & start optimized Next.js server (in a second terminal)
cd apps/web
npm run build
npm run start
```
*(Keep `--workers 1` on FastAPI when using SQLite + cached GPU TimesFM weights so a single process manages the GPU context and in-memory DataFrame cache cleanly.)*

---

## 3. Running with Docker Compose

### Option A — Full GPU Stack (FastAPI + Next.js using Host Ollama)
```bash
make up
# Equivalent to: docker compose up --build
```

### Option B — CPU-Only Stack (No NVIDIA GPU Required)
```bash
make up-cpu
# Equivalent to: docker compose --profile cpu up --build
```

### Option C — Include Containerized Ollama Inside Docker
```bash
docker compose --profile ollama up --build -d
docker exec -it $(docker ps -qf "ancestor=ollama/ollama:latest") ollama pull gemma3:4b
```

To stop containers:
```bash
docker compose down
```

---

## 4. Using the Application End-to-End

Open **`http://localhost:3000`** in your browser.

### 4.1 Uploading Any Dataset
- **Zero-Friction Drag & Drop:** Drag any `.csv`, `.tsv`, `.txt`, `.xlsx`, `.xls`, `.json`, `.jsonl`, or `.parquet` file onto the center dropzone, click **+ Upload dataset** in the left sidebar, or click the **Paperclip** icon inside the chat bar.
- **Immediate Readiness (< 1–2s):**
  - The engine automatically sniffs encodings, delimiters, and multi-row report headers, cleans currency/percentage strings (`"$1,250.50"` $\rightarrow$ `1250.5`), profiles **every row and column**, detects row grain, and marks the dataset **`ready`**.
  - **Step 1 Card (`Data Health & Schema`):** Expand **Understood Schema** to view the exact definition, type, value range, or top categories of every column in your file.
  - **Background Forecasting:** If your dataset has a temporal column or stock-summary structure, rolling-origin backtesting (`TimesFM` vs. `AutoETS`, `AutoARIMA`, `SeasonalNaive`, `Croston`, `TSB`) runs automatically in the background and streams **Step 2–4** briefing cards into the chat when complete.
  - **Cross-Sectional Tabular Files:** Files without dates (HR tables, SaaS customer lists, pricing catalogs, survey data) skip time-series forecasting cleanly and unlock immediate SQL, correlation, outlier, and CSV-generation tools.

### 4.2 Asking Any Analytical Question (with Chain-of-Thought)
Click any **dynamic suggestion chip** (auto-generated from your dataset's actual columns and values) or type any question in plain English:
- **Schema & Structure:**
  - *"Explain all rows and columns in this dataset"*
  - *"What does column MA represent?"*
- **SQL & Multi-Condition Analytics (Works on Any Domain):**
  - *"Average Annual_Salary by Team where Experience_Years > 4"*
  - *"Compare house vs unit in type"*
  - *"Show yearly average MA grouped by type and bedrooms"*
  - *"Top 10 Vendors by Profit Amt(In-rate) where Sale Qty > 50"*
- **Correlations & Anomaly Detection:**
  - *"What are the strongest correlations with ARR?"*
  - *"Detect anomalies and extreme outliers in Profit Amt(In-rate)"*
- **Inspecting the Chain-of-Thought Trace:**
  - Above every analytical reply, click **`Thought through N analytical steps • Xms`** to inspect the exact step-by-step reasoning: schema & cell-value grounding, CoT analytical plan, executed DuckDB SQL query, any self-reflection recoveries (such as relaxing strict `=` filters to `ILIKE`), and numerical traceability verification.

### 4.3 Asking for Future Predictions
Ask for predictions on the overall dataset, a specific numeric column, or a filtered subset:
- *"Show me the future prediction"*
- *"Show me the future prediction for MA"*
- *"Predict future temperature_c for Turbine_North over the next 10 days"*
- *"Forecast Revenue for next 6 periods"*

The response renders:
1. Winning backtested model & WAPE error metric
2. Interactive **Historical + Predicted + 80% Confidence Interval** chart
3. Period-by-period forecast table
4. One-click **Download Forecast CSV** and **Download Excel (`.xlsx`)** buttons

### 4.4 Generating New Custom CSV & Excel Files from Prompts
Click the **`Generate CSV`** button in the top header (or ask directly in chat) to transform your uploaded dataset into a brand-new downloadable spreadsheet:
- *"Generate a CSV summarizing average MA by type"*
- *"Create a CSV of all LADIES items where Closing Qty > 10 sorted by Profit Amt descending"*
- *"Generate a CSV of top 25 Enterprise customers by ARR"*
- *"Export a CSV of future predictions"*

You can preview the generated table right inside the chat card and click **Download CSV** or **Download Excel (`.xlsx`)**—or click **Full CSV** in the top bar anytime to export the entire sanitized dataset.

### 4.5 Interactive What-If Sizing (Inventory Datasets)
On retail/inventory datasets, click **Catalog** (or click any product in the Reorder card) to open the **Product Detail Modal**, where you can drag **Supplier Lead Time** and **Target Service Level (80%–99%)** sliders and enter **Current Stock on Hand** to recalculate Reorder Points and Safety Stock live.

---

## 5. Direct CLI / `curl` API Recipes

### Check Hardware, GPU & Ollama Status
```bash
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

### Upload Any File via CLI
```bash
curl -s -X POST http://localhost:8000/api/v1/datasets \
  -F "file=@data/sample_sales.csv" \
  -F "name=My Dataset" | python3 -m json.tool
```

### Ask a Chain-of-Thought Chat Question
```bash
DATASET_ID="<paste-dataset-id>"

curl -s -X POST "http://localhost:8000/api/v1/datasets/${DATASET_ID}/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me the future prediction"}' | python3 -m json.tool
```

### Run Direct Read-Only DuckDB SQL or Generate a Custom CSV via API
```bash
# Execute arbitrary read-only DuckDB SQL against table `data`
curl -s -X POST "http://localhost:8000/api/v1/datasets/${DATASET_ID}/query" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM data LIMIT 10"}' | python3 -m json.tool

# Generate a custom CSV & XLSX export from natural language and get download URLs
curl -s -X POST "http://localhost:8000/api/v1/datasets/${DATASET_ID}/query" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarize total Units_Sold and Revenue by Product_SKU", "export_csv": true}' | python3 -m json.tool
```

---

## 6. Developer Maintenance & Makefile Commands

| Command | Action |
| :--- | :--- |
| `make dev-api` | Start FastAPI server with hot reload on `http://localhost:8000`. |
| `make dev-web` | Start Next.js frontend dev server on `http://localhost:3000`. |
| `make seed` | Generate synthetic 2-year, 40-product dataset at `data/sample_sales.csv`. |
| `make test` | Run all 47 pytest unit, integration, forecasting, SQL, CoT, and export tests. |
| `make lint` | Run `ruff check` across `apps/` and `scripts/`. |
| `make format` | Auto-format Python code with `ruff format`. |
| `make clean` | Wipe `data/uploads/*`, `data/parquet/*`, `data/sqlite/*`, and `.pytest_cache` for a clean slate. |

---

## 7. Troubleshooting

- **"Sorry, I could not query the local engine" in UI:**
  Ensure FastAPI is running on port `8000` (`make dev-api`) and `curl http://localhost:8000/api/v1/health` returns `"status": "ok"`.
- **`ollama_ready: false` in `/api/v1/health`:**
  Start Ollama (`ollama serve`) and verify the configured model tag exists (`ollama list` $\rightarrow$ `ollama pull gemma3:4b`). Note that even when Ollama is offline, the deterministic CoT planner, DuckDB SQL synthesizer, forecasting engine, and CSV exporter continue to work automatically.
- **Port `8000` or `3000` already in use:**
  Check active listeners with `lsof -i :8000` or `lsof -i :3000` and stop the older server process by PID (`kill <PID>`).
- **Generated CSV download opens raw text instead of downloading:**
  Ensure FastAPI is still running so `/api/v1/datasets/{id}/exports/{filename}` can stream the artifact from `data/exports/`.
