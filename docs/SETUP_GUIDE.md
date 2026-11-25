# Setup Guide

This guide walks you through installing and configuring every dependency required for the **Local AI Data Analyst, Forecasting & Decision Engine** on a fresh machine (Linux, macOS, or Windows WSL2).

Once setup is complete, proceed to the **[Run Guide](./RUN_GUIDE.md)** to launch and operate the application.

---

## 1. System Prerequisites

### Hardware Recommendations
| Component | Minimum (CPU Mode) | Recommended (GPU Mode) |
| :--- | :--- | :--- |
| **CPU** | 4 cores (x86_64 or ARM64) | 8+ cores |
| **RAM** | 8 GB | 16 GB+ |
| **GPU** | None required (automatic CPU fallback) | NVIDIA GPU with 6 GB+ VRAM & CUDA 12.x |
| **Disk Space** | ~5 GB free | ~15 GB free (with Ollama `gemma3:4b` + TimesFM weights) |

### Required Software
1. **Git** (`git --version`)
2. **Python 3.12+** and **`uv`** (fast Python package & virtualenv manager):
   ```bash
   # Install uv (Linux / macOS / WSL2)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Node.js 18.18+ or 20+ LTS** and **npm** (for the Next.js 14 frontend):
   ```bash
   node --version   # v18.18+ or v20+
   npm --version
   ```
4. **Ollama** (for local Chain-of-Thought planning and verified narration; the system also has full deterministic DuckDB/Polars fallbacks if Ollama is offline):
   ```bash
   # Install Ollama on Linux
   curl -fsSL https://ollama.com/install.sh | sh
   ```
5. *(Optional)* **Docker & Docker Compose v2** if you prefer containerized deployment (`docker compose version`).

---

## 2. Clone the Repository & Configure `.env`

```bash
git clone https://github.com/Sahnik0/Major-project.git "Major project"
cd "Major project"

# Copy the template environment configuration
cp .env.example .env
```

### Environment Variables Reference (`.env`)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | FastAPI bind address. |
| `PORT` | `8000` | FastAPI HTTP port. |
| `ENVIRONMENT` | `development` | Runtime mode (`development` or `production`). |
| `DATA_DIR` | `./data` | Root directory for all local storage. |
| `UPLOAD_DIR` | `./data/uploads` | Raw uploaded CSV/TSV/Excel/JSON/Parquet files. |
| `PARQUET_DIR` | `./data/parquet` | Cleaned & regularized Apache Parquet datasets. |
| `EXPORT_DIR` | `./data/exports` | AI-generated custom `.csv` and `.xlsx` export artifacts. |
| `DATABASE_URL` | `sqlite:///./data/sqlite/advisor.db` | Local SQLite metadata, forecast, job, and chat database. |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama HTTP API endpoint. |
| `OLLAMA_MODEL` | `gemma3:4b` | Local Ollama model tag (e.g., `gemma3:4b`, `gemma3:12b`, `qwen2.5:7b`). |
| `TIMESFM_VERSION` | `3.0` | Foundation model version (`3.0` non-commercial or `2.5` Apache-2.0). |
| `TIMESFM_REPO` | `google/timesfm-3.0-pytorch` | Hugging Face repository ID for TimesFM weights. |
| `HF_TOKEN` | *(empty)* | Hugging Face access token (required only when downloading gated TimesFM 3.0 weights). |
| `HF_HUB_OFFLINE` | `0` | Set to `1` after downloading weights to enforce strict 100% air-gapped execution. |
| `MAX_UPLOAD_SIZE_MB` | `100` | Maximum allowed file upload size in MB. |
| `DEFAULT_SERVICE_LEVEL` | `0.90` | Default target service level (90%) for safety-stock math. |
| `DEFAULT_LEAD_TIME_DAYS` | `7` | Default supplier lead time in days. |
| `DEFAULT_REVIEW_PERIOD_DAYS` | `7` | Default order review cycle in days. |
| `DEFAULT_HORIZON` | `28` | Default daily forecast horizon (auto-scales for weekly/monthly/quarterly/yearly series). |

> **Frontend API URL (Optional):** In `apps/web/`, `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000/api/v1`. Override it in `apps/web/.env.local` only if you change the backend host or port.

---

## 3. Set Up the Python Backend (`apps/api`)

All Makefile targets expect a `.venv` virtual environment at the repository root.

### Step 3.1: Create the Python 3.12 Virtual Environment
```bash
uv venv --python 3.12 .venv
```

### Step 3.2: Install PyTorch (Choose GPU or CPU)
- **Option A — NVIDIA GPU with CUDA 12.4 (Recommended if you have an NVIDIA GPU):**
  ```bash
  uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cu124
  ```
- **Option B — CPU-Only Machine (Laptops without NVIDIA GPU / macOS):**
  ```bash
  uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
  ```

### Step 3.3: Install Project & Dev Dependencies
Install all packages declared in [`pyproject.toml`](../pyproject.toml) (`fastapi`, `duckdb`, `polars`, `pyarrow`, `statsforecast`, `openpyxl`, `fastexcel`, `timesfm`, `scipy`, `sqlalchemy`, `pytest`, `ruff`, etc.):

```bash
uv pip install --python .venv/bin/python -e ".[dev]" huggingface_hub
```

Verify the backend environment:
```bash
.venv/bin/python -c "import fastapi, duckdb, polars, statsforecast, torch; print('CUDA available:', torch.cuda.is_available())"
```

---

## 4. Set Up the Next.js Frontend (`apps/web`)

```bash
cd apps/web
npm install
cd ../..
```

Verify that TypeScript and the production Next.js build compile cleanly:
```bash
cd apps/web && npm run build && cd ../..
```

---

## 5. Set Up Local AI Models

### 5.1 Local LLM via Ollama (`gemma3:4b`)
1. Ensure the Ollama service is enabled and running:
   ```bash
   # On systemd Linux systems:
   sudo systemctl enable --now ollama
   # Or start manually in a terminal:
   ollama serve
   ```
2. Pull the default model (`gemma3:4b`, ~3.3 GB):
   ```bash
   ollama pull gemma3:4b
   ```
   *(If you have 16 GB+ VRAM/RAM and want even stronger SQL & Chain-of-Thought reasoning, you can pull a larger local model such as `ollama pull gemma3:12b` and set `OLLAMA_MODEL=gemma3:12b` in `.env` or the UI Settings modal.)*

3. Verify Ollama responds:
   ```bash
   curl http://localhost:11434/api/tags
   ```

### 5.2 Google TimesFM Weights *(Optional — Graceful Baseline Fallback Built-In)*
The forecasting engine works out of the box using `statsforecast` (`AutoETS`, `AutoARIMA`, `SeasonalNaive`, `CrostonSBA`, `TSB`). To also enable **Google TimesFM** in rolling-origin backtest competitions:

1. **For TimesFM 3.0 (Non-commercial / Research):**
   - Visit `https://huggingface.co/google/timesfm-3.0-pytorch` and accept the license terms.
   - Create a Read token at `https://huggingface.co/settings/tokens` and set `HF_TOKEN=hf_...` in `.env`.
2. **For TimesFM 2.5 (Commercial-Safe Apache-2.0):**
   - Set `TIMESFM_VERSION=2.5` and `TIMESFM_REPO=google/timesfm-2.5-200m-pytorch` in `.env`.
3. Run the downloader script:
   ```bash
   chmod +x scripts/download_models.sh
   ./scripts/download_models.sh
   ```
4. *(Optional)* Set `HF_HUB_OFFLINE=1` in `.env` so no network calls are ever attempted at runtime.

---

## 6. Optional: Docker Setup

If you plan to run the stack via Docker Compose:
1. Ensure Docker Engine and `docker compose` are installed.
2. **For GPU Acceleration in Docker:** Install the **NVIDIA Container Toolkit** (`nvidia-ctk`) and configure the Docker runtime:
   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```
3. If you do not have an NVIDIA GPU, no extra toolkit is needed—use the `cpu` profile (`make up-cpu`).

---

## 7. Verify Complete Installation

Run the automated test suite and linter from the repository root:

```bash
# Lint codebase with Ruff
.venv/bin/ruff check apps/

# Run all 47 backend unit, forecasting, DuckDB SQL, CoT, and export tests
make test
```

All checks should pass. Next, open **[RUN_GUIDE.md](./RUN_GUIDE.md)** for instructions on running and using every feature of the platform.
