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

1. **Ingestion:** Delimiter & character encoding auto-sniffing for `.csv` and `.xlsx` files.
2. **Column Mapping:** Heuristic scoring for required (`date`, `product`, `quantity`) and optional attributes (`revenue`, `store`, `promo_flag`, `stock_on_hand`).
3. **Cleaning & Regularization:**
   - Resolves day-first vs month-first date ambiguities.
   - Separates negative returns.
   - **Active-Period Zero-Filling:** Zeroes are filled strictly between the product's first and last observed sale. Pre-launch periods are left untouched.
   - Outlier detection via IQR ($3 \times \text{IQR}$) with optional capping.
4. **Data Health Profiling:** Syntetos-Boylan demand classification (smooth, erratic, intermittent, lumpy, short_history, inactive).
5. **Deterministic Inventory Advice:** Pure Python calculation of reorder points, safety stocks, and order quantities with zero hallucination.
6. **AI Explanation & Validation:** Gemma local narration with strict number verification against factual data.

