# Demand Forecasting & Inventory Advisor

A local-first sales forecasting and inventory advisor application designed for small retailers and online merchants. Upload your sales history to receive product-level demand forecasts, concrete reorder recommendations (what to reorder, how much, and by when), and plain-English explanations.

All computations, time-series transformations, database storage, and AI summaries run **100% locally on your machine**. Your business data never leaves your environment.

---

## ⚠️ Licensing Notice (TimesFM 3.0 vs. 2.5)

- **TimesFM 3.0 (`google/timesfm-3.0-pytorch`):** Pretrained weights are provided under the **`timesfm-non-commercial-license-v1.0`** (restricted strictly to non-commercial and research use).
- **TimesFM 2.5 (`google/timesfm-2.0-500m-pytorch`):** Released under **`Apache-2.0`** and is fully safe for commercial and production use.
- The application supports both versions. Switch between them seamlessly via environment variables or in the Settings page (`TIMESFM_VERSION=3.0` or `TIMESFM_VERSION=2.5`).

---

## Hardware & Environment

- **Tested OS:** Arch Linux (x86_64, Hyprland).
- **Python Version:** 3.12 managed via `uv` (pinned in `.python-version`).
- **GPU Acceleration:** NVIDIA GPU detected dynamically at startup (tested on NVIDIA RTX 4050 Laptop GPU with CUDA 12.4). Seamless fallback to CPU is automatically enabled when a CUDA GPU is unavailable.
- **Local LLM:** Ollama running locally (`gemma3:4b`) at `http://localhost:11434`.

---

## Quick Start

### 1. Clone & Set Up Environment
```bash
# Clone the repository
git clone <repo-url>
cd "Major project"

# Copy environment template
cp .env.example .env
# Edit .env and configure HF_TOKEN if using TimesFM 3.0
```

### 2. Install Dependencies
```bash
# Create Python 3.12 venv with uv
uv venv --python 3.12

# Install PyTorch with CUDA 12.4
uv pip install torch --index-url https://download.pytorch.org/whl/cu124

# Install application dependencies
uv pip install fastapi "uvicorn[standard]" pydantic pydantic-settings sqlalchemy duckdb polars pyarrow python-multipart openpyxl chardet holidays numpy scipy httpx pytest pytest-asyncio ruff huggingface_hub
```

### 3. Download Models (Optional for Phase 1 / Required for TimesFM)
```bash
chmod +x scripts/download_models.sh
./scripts/download_models.sh
```

### 4. Generate Synthetic Demo Data
```bash
make seed
```
This generates 2 years of synthetic daily sales data for 40 products with known demand classifications across smooth, erratic, intermittent, lumpy, short-history, and inactive patterns.

### 5. Run Tests
```bash
make test
```

### 6. Start the API Server
```bash
make dev-api
```
The FastAPI server will start on [http://localhost:8000](http://localhost:8000).
Interactive Swagger docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Docker Packaging
Run with Docker Compose:
```bash
# Start full stack with GPU reservation:
make up

# Or start in CPU-only mode:
make up-cpu
```

---

## Core Pipeline Architecture

1. **Universal Multi-Format Ingestion:** Auto-sniffs delimiters, encodings, and multi-row pre-headers across `.csv`, `.tsv`, `.txt`, `.xlsx`, `.xls`, `.json`, `.jsonl`, and `.parquet` files while coercing currency/percentage strings into clean numeric columns.
2. **Immediate Row & Column Understanding:** Automatically classifies datasets across **Time-Series** (daily, weekly, monthly, quarterly, yearly — single-series or multi-entity), **ERP Stock Summaries**, and **Cross-Sectional Tabular** data, computing row grain, data types, min/max/avg ranges, and category distributions with zero deadlocks.
3. **Cleaning & Regularization:**
   - Resolves day-first vs month-first date ambiguities and preserves natural cadence (daily through yearly).
   - Separates negative returns and distinguishes additive volumes from non-additive rates/averages.
   - **Active-Period Zero-Filling:** Zeroes are filled strictly between the series' first and last observed period.
   - Outlier detection via IQR & Z-score with optional capping.
4. **Full AI Data Analyst (DuckDB SQL + Polars):**
   - Executes read-only DuckDB SQL queries (`WHERE`, `GROUP BY`, `HAVING`, window functions, ratios, pivots, date bucketing) with automatic schema grounding and SQL self-repair.
   - Built-in correlation/driver discovery, anomaly detection, group comparisons, and multi-turn chat context.
5. **On-Demand Future Predictions & Inventory Advice:**
   - Rolling-origin backtesting across TimesFM and statistical baselines (AutoETS, AutoARIMA, SeasonalNaive, Croston, TSB) for any temporal column or filtered slice, plus deterministic reorder-point and safety-stock math.
6. **Custom CSV / Excel Generation:**
   - Ask in plain English (or via `POST /api/v1/datasets/{id}/query`) to filter, transform, aggregate, or forecast data into a new downloadable `.csv` and `.xlsx` file.
7. **Verified Local AI Narration:** Gemma via Ollama explains computed facts with strict numerical traceability validation.

