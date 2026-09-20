# Architectural Decisions & Technical Log

This document records key technical decisions, trade-offs, and library compatibility findings made throughout the development of the Demand Forecasting & Inventory Advisor.

---

## 1. Environment & Runtime
- **Host OS:** Arch Linux (x86_64, Hyprland).
- **Python Version:** Python 3.12 managed with `uv` (`uv venv --python 3.12`), pinned in `.python-version`.
- **GPU & PyTorch:** NVIDIA GeForce RTX 4050 Laptop GPU (6GB VRAM). PyTorch installed with CUDA 12.4 wheel index (`https://download.pytorch.org/whl/cu124`). Dynamic device detection checks `torch.cuda.is_available()` on startup, logs device details, and exposes device state in `/api/v1/health`. Robust CPU fallback is enforced if CUDA initialization fails.

## 2. Data Processing: Polars vs. Pandas
- **Decision:** **Polars** was chosen for all dataset ingestion, cleaning, regularization, and Parquet persistence.
- **Rationale:**
  - High performance and multi-threaded execution on memory-constrained machines.
  - Native Apache Arrow integration, matching DuckDB and Parquet file formats with zero-copy transfers.
  - Clean, expressive expressions for window operations, date range generation, and rolling calculations.

## 3. Storage Architecture
- **Application Metadata & Job State:** SQLite via SQLAlchemy. Kept lightweight in `data/sqlite/advisor.db` for tracking dataset records, background job progress, and user settings.
- **Datasets & Results:** Parquet files in `data/parquet/{dataset_id}.parquet`.
- **Analytics Query Engine:** DuckDB for querying raw/cleaned Parquet tables quickly without loading full datasets into Python memory.

## 4. Syntetos-Boylan Classification Scheme
- Every product time series is classified into exactly one of:
  - `inactive`: No sales recorded in the last $N$ periods (default: 60 days).
  - `short_history`: Fewer than minimum threshold points (default: 60 daily or 8 weekly points).
  - `smooth`: $\text{ADI} < 1.32$ and $CV^2 < 0.49$.
  - `erratic`: $\text{ADI} < 1.32$ and $CV^2 \ge 0.49$.
  - `intermittent`: $\text{ADI} \ge 1.32$ and $CV^2 < 0.49$.
  - `lumpy`: $\text{ADI} \ge 1.32$ and $CV^2 \ge 0.49$.
- ADI is defined as $\frac{\text{total active periods}}{\text{non-zero demand periods}}$.
- $CV^2$ is defined as $\left(\frac{\sigma}{\mu}\right)^2$ of non-zero demand periods.

## 5. TimesFM Foundation Model & Licensing
- **Default:** TimesFM 3.0 (`google/timesfm-3.0-pytorch`), under `timesfm-non-commercial-license-v1.0`. Persistent warning shown in UI and docs.
- **Commercial Fallback:** TimesFM 2.5 (Apache-2.0).
- Both models are encapsulated behind the unified `Forecaster` interface.

## 6. LLM Integration & Determinism
- Ollama endpoint (`http://localhost:11434`), default model `gemma3:4b`.
- LLM is strictly restricted to text explanations and column mapping hints; numerical forecasts and inventory numbers are strictly calculated by deterministic Python formulas.
