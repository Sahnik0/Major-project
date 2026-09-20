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

## 7. Baseline Models & StatsForecast Integration
- Models integrated: `Naive`, `SeasonalNaive` (seasonality $m=7$ for daily, $m=52$ for weekly), `AutoETS`, `AutoARIMA`, `CrostonClassic`, `CrostonSBA`, `TSB`, and `HistoricAverage`.
- Candidate selection routed strictly by Syntetos-Boylan demand classification:
  - `smooth` / `erratic`: Seasonal Naive, AutoETS, AutoARIMA (+ TimesFM).
  - `intermittent` / `lumpy`: CrostonClassic, CrostonSBA, TSB, Naive (+ TimesFM).
  - `short_history`: Naive, HistoricAverage, Seasonal Naive (if length $\ge 2m$), flagged as "low confidence".
  - `inactive`: No forecast generated; flagged as "possibly discontinued".

## 8. Conformal Empirical Residual Quantiles
- For models supporting analytical prediction intervals (`AutoETS`, `AutoARIMA`, `SeasonalNaive` via `statsforecast`), 80% prediction intervals directly map to 10th and 90th percentiles.
- For models without analytical quantiles (`Croston`, `TSB`, moving averages), we use conformal empirical quantile shifts from in-sample 1-step ahead residuals: $\hat{q}_\tau = \max(0.0, \hat{y} + Q_\tau(e_t))$.
- All forecasts enforce a non-negativity constraint ($\max(0.0, \dots)$).

## 9. Deterministic Inventory Optimization
- Pure Python calculation with zero AI generation:
  - Lead time demand: $\mu_L = \sum_{t=1}^L \hat{y}_t$.
  - Review period demand: $\mu_{L+R} = \sum_{t=1}^{L+R} \hat{y}_t$.
  - Safety stock: $SS = \max(0.0, \sum_{t=1}^L \hat{q}_{0.9, t} - \mu_L)$ or $z_{\alpha} \cdot \sigma_L$.
  - Reorder point: $ROP = \mu_L + SS$.
  - Order-up-to target level: $S = \mu_{L+R} + SS$.
  - Order quantity: $Q = \lceil \max(S - \text{current\_stock}, \text{MOQ}) / \text{pack\_size} \rceil \times \text{pack\_size}$ if $\text{current\_stock} \le ROP$, else $0$.
  - Days of cover: $\frac{\text{current\_stock}}{\bar{d}}$.
  - Stockout risk:
    - `high`: $\text{current\_stock} < 0.5 \times ROP$ or $\text{days\_of\_cover} < L$.
    - `medium`: $\text{current\_stock} < ROP$.
    - `low`: $\text{current\_stock} \ge ROP$.
    - `unknown`: if current stock is not provided.
  - Fallback advice formatting when current stock is omitted: "Order $Q^*$ units whenever stock falls below $ROP$ units".


