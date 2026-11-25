# AGENT BUILD PROMPT: Demand Forecasting & Inventory Advisor

You are a senior full-stack + ML engineer. Build the complete application described below, phase by phase, in a new repository. Work autonomously, but follow the phase gates and working rules at the end.

---

## 0. HOST ENVIRONMENT (overrides anything below that conflicts)

- **Host OS:** Arch Linux (Hyprland). Use bash. No Windows or WSL assumptions.
- **Python:** managed with `uv`. Create the venv with `uv venv --python 3.12` and pin the version in `.python-version`. If `timesfm` or its dependencies don't support 3.12, choose the nearest supported version and record it in `docs/DECISIONS.md`.
- **GPU:** an NVIDIA GPU is available. Install the CUDA build of PyTorch. At startup, detect the device (`cuda` if available, else `cpu`), log it, and expose it in `/health`. Verify with a small script before relying on it. CPU fallback must always work.
- **Ollama:** already installed and running on the host (systemd) with a Gemma model pulled. Default `OLLAMA_URL=http://localhost:11434` for host development. In Docker Compose, give the `api` service `extra_hosts: ["host.docker.internal:host-gateway"]` and default `OLLAMA_URL=http://host.docker.internal:11434`. Ollama pulls from its own registry, so it needs no Hugging Face token.
- **Hugging Face:** needed only for the TimesFM weights.
  - Read the token from the `HF_TOKEN` env var (in a gitignored `.env`). Never log it or commit it.
  - Provide `scripts/download_models.sh` that downloads the configured TimesFM checkpoint into the standard Hugging Face cache and verifies that it loads. The repo id must be configurable (`TIMESFM_REPO`), defaulting to `google/timesfm-3.0-pytorch`. For the 2.5 fallback, take the checkpoint id from the official TimesFM Hugging Face collection linked in the GitHub README; do not guess it. If the 3.0 download fails with an authorization error, tell me to accept the license on the model page and set `HF_TOKEN`.
  - After the download, the app must run with `HF_HUB_OFFLINE=1` so runtime makes no network calls (Principle 5).
- **Dev workflow:** during Phases 1-5, run the API and web app directly on the host (`make dev`). Use Docker only for Phase 6 packaging.
- **Docker packaging (Phase 6):** mount `~/.cache/huggingface` into the `api` container. Pass the GPU through a Compose device reservation (needs `nvidia-container-toolkit` on the host). Also provide a CPU-only profile that works without it.
- **Playwright:** if its bundled browser download fails on Arch, use the system Chromium instead.

---

## 1. PRODUCT DEFINITION

**One-line pitch:** A tool where a small retailer or online seller uploads their sales history and gets (a) a forecast of demand per product, (b) a concrete stock recommendation (what to reorder, how much, by when), and (c) a plain-English explanation. Everything can run locally so the business's data never leaves their machine.

**Target user:** A non-technical shop owner. No data-science vocabulary in the UI. Say "how much you'll probably sell" not "quantile forecast".

**What it is NOT:** A universal "upload anything" analyst. It handles **sales transaction data** (date, product, quantity, optional revenue/store/promo flag). Reject or gracefully explain anything else.

---

## 2. NON-NEGOTIABLE PRINCIPLES

1. **The LLM never computes numbers.** All numbers come from deterministic Python. The LLM (Gemma via Ollama) only (a) suggests column mappings and (b) writes the plain-English explanation from a structured JSON of facts.
2. **Every sentence in an AI explanation must be traceable.** Build a validator: extract every number from the generated text and check it exists in the facts JSON (with tolerance/rounding rules). If validation fails, regenerate once, then fall back to a deterministic template. Never show unvalidated numbers.
3. **Baselines are mandatory.** No forecast model is shown as "best" unless it was backtested against baselines on that customer's own data. Show the comparison honestly, including when TimesFM loses.
4. **Show uncertainty.** Forecasts are always shown as a range (e.g. 10th-90th percentile band), and stock advice uses the upper quantile tied to the chosen service level.
5. **Privacy by default.** No telemetry, no external network calls that include user data. Gemma runs locally via Ollama. The only allowed network use is downloading model weights/packages at setup.
6. **Do not fabricate library APIs.** Before using TimesFM, statsforecast, Ollama client, etc., inspect the installed package (`help()`, source, README) and use the real, current API. If something differs from what this prompt assumes, adapt and note it in `docs/DECISIONS.md`.
7. **Graceful degradation.** If TimesFM or Ollama is unavailable, the app must still work (baselines + template explanations) and clearly say what is disabled.

---

## 3. TECH STACK

**Backend:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy + SQLite (metadata/jobs/settings), DuckDB + Parquet files (datasets & results), Polars or Pandas (choose one for cleaning; be consistent), `statsforecast` (baselines, intermittent-demand methods), `holidays` (calendar), `scikit-learn`/`lightgbm` only if needed for a boosted baseline, `pytest`.

**Forecasting foundation model:** Google `timesfm`. **Model version must be configurable** via env/settings (`TIMESFM_VERSION=3.0|2.5`).
- **Default: TimesFM 3.0** (checkpoint `google/timesfm-3.0-pytorch`, 330M parameters). It supports native multivariate targets, past-only covariates, past-and-future covariates, and 9 quantiles (0.1 to 0.9) in one forward pass.
- **License constraint (verified in the official README):** the TimesFM 3.0 pretrained weights are under `timesfm-non-commercial-license-v1.0`, restricted to non-commercial, non-production use. Weights up to 2.5 are Apache-2.0. Therefore: show a clear, persistent warning in the Settings UI and the README when 3.0 is selected, and keep **2.5 fully working as a selectable, commercial-safe fallback** (Phase 3 acceptance covers both). Do not remove or hide this warning.
- **API note:** the official README shows 3.0 through a `timesfm3` module (`TimesFM3Evaluator`, `ModelConfig`, `predict_batch(...)` with `past_only_covariates`, `past_future_covariates`, `return_quantiles`). Verify this against the actual installed or cloned package. Determine whether the PyPI release includes `timesfm3` or whether it must be installed from the GitHub repository, and record the result in `docs/DECISIONS.md`. Wrap all of it behind the common `Forecaster` interface so the rest of the app does not depend on the version.
- Use the model's quantile outputs for uncertainty.

**LLM:** Gemma through Ollama's local HTTP API. Model tag configurable (`OLLAMA_MODEL`), endpoint configurable (`OLLAMA_URL`, default `http://localhost:11434`). Use JSON-mode/structured output for column-mapping suggestions.

**Frontend:** Next.js (App Router) + TypeScript, Tailwind CSS, shadcn/ui, Plotly.js (or Recharts if Plotly is too heavy; justify in DECISIONS.md), TanStack Query for data fetching, react-dropzone for upload.

**Infra:** Docker Compose with services `api`, `web`, and optional `ollama`. `.env.example`. A `Makefile` with `dev`, `test`, `lint`, `seed`, `up`.

---

## 4. REPOSITORY STRUCTURE

```
/apps
  /api
    /app
      main.py
      config.py                # env-driven settings
      db.py                    # SQLite/SQLAlchemy
      /routers                 # datasets.py, jobs.py, forecasts.py, inventory.py, insights.py, settings.py, health.py
      /services
        ingestion.py           # read CSV/XLSX, encoding/delimiter sniffing
        column_mapping.py      # heuristic + LLM suggestion
        cleaning.py
        profiling.py           # series classification, data-health report
        calendar.py            # holidays, promo flags
        forecasting/
          base.py              # Forecaster interface
          timesfm_forecaster.py
          baselines.py         # naive, seasonal naive, ETS/ARIMA via statsforecast, Croston/SBA/TSB
          router.py            # choose candidate models per series class
          backtest.py
          metrics.py
        inventory.py           # reorder point, safety stock, order qty
        insights.py            # facts JSON builder
        narrator.py            # Gemma call + validator + template fallback
      /jobs                    # background job runner + progress
      /schemas
    /tests
  /web
    /app                       # pages
    /components
    /lib
/docs  (README.md, ARCHITECTURE.md, DECISIONS.md, USER_GUIDE.md)
/scripts (generate_sample_data.py)
/data  (gitignored: uploads, parquet, sqlite)
docker-compose.yml
Makefile
.env.example
```

---

## 5. DATA PIPELINE (implement exactly in this order)

### 5.1 Ingestion
- Accept `.csv`, `.xlsx`. Sniff encoding and delimiter. Max size configurable (default 100 MB). Stream large files.
- Store the raw upload untouched; write the cleaned result as Parquet. Keep a `dataset` record (id, name, created_at, row counts, status, mapping, health report).

### 5.2 Column mapping
- Required logical fields: `date`, `product` (SKU or name), `quantity`.
- Optional: `revenue`/`price`, `store`, `promo_flag`, `stock_on_hand`.
- Step 1: heuristics (header name matching in English and common variants, dtype detection, date-parse success rate).
- Step 2: send **only column names + 5 sample rows (masked if configured)** to Gemma to propose a mapping as JSON. Validate its JSON against a schema; if invalid or Ollama is down, use heuristics only.
- The user **always confirms** the mapping in the UI before processing.

### 5.3 Cleaning
- Parse dates robustly (day-first vs month-first ambiguity: detect from data, otherwise ask the user).
- Drop exact duplicate rows only if the user opts in (default: warn, don't drop).
- Handle negative quantities as returns: keep them in a separate column, forecast on net-of-returns by default (setting).
- Aggregate to a regular grid (daily, or weekly if the data is sparse; detect and let the user override). **Fill missing periods with 0 only when the product was active** (between its first and last sale); leading gaps before first sale are not zeros.
- Flag outliers (robust z-score / IQR) but **do not delete**; expose a setting to cap them.
- Multi-store: default to forecasting per (product) aggregated across stores; support per (store, product) if the user maps a store column and enables it.

### 5.4 Profiling / data-health report
Produce a JSON report and a UI page with: row/product/date-range counts, missing-value rates, duplicates, gap counts, outlier counts, history length per product, and **series classification** using the Syntetos-Boylan scheme (ADI and CV²): `smooth`, `erratic`, `intermittent`, `lumpy`, plus `short_history` (< configurable minimum, default 8 weekly or 60 daily points) and `inactive` (no sales in the last N periods). Each product gets exactly one class.

### 5.5 Calendar & covariates
- Holiday calendar via the `holidays` package; country configurable (default `IN`, with a settings dropdown). Also allow a user-uploaded CSV of custom events (date, name).
- If a `promo_flag` column exists, use it as a known-future covariate only when the user supplies future promo dates.
- With TimesFM 3.0, pass holiday/event flags (and promo flags when future dates exist) as **past-and-future covariates**. Backtest each model **with and without covariates** and report whether they helped.
- Also test **joint multivariate forecasting** (products grouped by category as multiple targets) against **independent per-product forecasting**. Cross-series modeling can underperform when correlations are weak, so choose per category based on backtest results, not assumption.

### 5.6 Forecast routing
| Series class | Candidate models |
|---|---|
| smooth / erratic (enough history) | TimesFM, seasonal naive, ETS/AutoARIMA (statsforecast) |
| intermittent / lumpy | Croston/SBA/TSB (statsforecast), TimesFM, naive |
| short_history | naive, seasonal naive (only if >= 2 seasons), simple average; label "low confidence" |
| inactive | no forecast; flag "possibly discontinued" |

All models implement a common `Forecaster` interface returning point forecast + quantiles (10/50/90 at minimum; derive quantiles for baselines via conformal/residual methods and document how).

### 5.7 Backtesting
- Rolling-origin evaluation with a configurable number of folds (default 3) and horizon (default 4 weeks or 28 days).
- Metrics per model per product: **MASE**, **WAPE**, **pinball loss** (10/50/90), and **interval coverage** (does the 80% band actually cover ~80%?).
- Pick the best model per product class (and per product where history allows) by a documented rule. Store the full comparison table.
- Surface an aggregate summary: "TimesFM improved accuracy on X% of products vs the best baseline; baselines won on Y%".

### 5.8 Inventory advice (pure Python, no AI)
Inputs per product (with global defaults in settings, per-product overrides in UI/CSV upload): `lead_time_days`, `review_period_days`, `service_level` (default 0.90), `current_stock` (optional), `min_order_qty` (optional), `pack_size` (optional).
Compute:
- Demand over (lead time + review period) from the forecast; use the quantile matching the service level for the upper estimate.
- `safety_stock`, `reorder_point`, `recommended_order_qty` (respect MOQ/pack size), `days_of_cover`, `stockout_risk` (low/medium/high with thresholds documented), `overstock_flag`.
- If `current_stock` is missing, output advice as "order X if your stock is below Y" instead of a quantity.
- Unit-test every formula with hand-calculated cases.

### 5.9 Insights & narration
- `insights.py` builds a **facts JSON** per product and for the whole business: forecast totals, change vs last period, top movers, weekday/seasonal patterns (computed, not guessed), stockout risks, backtest winner and margin, data-quality caveats.
- `narrator.py` sends the facts JSON to Gemma with a strict prompt: plain language, no jargon, no numbers not present in the facts, max N sentences, state uncertainty. Run the validator (Principle 2). Fall back to a deterministic template on failure.
- Output types: (1) product explanation (3-5 sentences), (2) business summary ("this week's action list"), (3) data-quality warnings.

---

## 6. API (FastAPI, all under `/api/v1`, typed with Pydantic, OpenAPI docs enabled)

- `POST /datasets` (upload) -> dataset id + suggested mapping
- `GET /datasets`, `GET /datasets/{id}`, `DELETE /datasets/{id}`
- `PUT /datasets/{id}/mapping` (confirm mapping, triggers processing job)
- `GET /datasets/{id}/health`
- `POST /datasets/{id}/forecast-jobs` (params: horizon, granularity, models, service level) -> job id
- `GET /jobs/{id}` (status, progress %, current step, errors)
- `GET /datasets/{id}/products` (paginated, sortable, filter by class/risk)
- `GET /datasets/{id}/products/{pid}` (history, forecast + quantiles, backtest table, explanation)
- `GET /datasets/{id}/inventory` and `GET /datasets/{id}/inventory.csv`
- `GET /datasets/{id}/summary` (business-level insights + narration)
- `GET/PUT /settings` (defaults, model versions, Ollama endpoint, country)
- `GET /health` (reports availability of TimesFM, Ollama, and disk)

Long tasks run as background jobs with persisted status so a page refresh doesn't lose progress. Consistent error format: `{code, message, hint}`.

---

## 7. FRONTEND: pages, components, UX

Design: clean, calm, professional (think a modern analytics tool, not a toy). Light and dark mode. Fully responsive. Accessible (labels, keyboard navigation, contrast). Every screen has designed **loading, empty, and error states**.

1. **Home / Datasets:** list of datasets with status chips; big "Upload sales file" dropzone; a "Try with sample data" button that loads the generated demo dataset.
2. **Upload -> Confirm columns:** two-column layout: detected columns and sample rows on the left; mapping dropdowns on the right with confidence hints; ambiguous date-format prompt; "Looks right, continue".
3. **Data health:** cards (rows, products, date range), issue list with severity, a chart of history length per product, series-class donut. Each issue has a plain-English "what this means / what we did".
4. **Dashboard (main screen):** KPI cards (forecast total next period vs last period, products at stockout risk, overstock count, forecast confidence); a "This week's actions" list (top items to reorder, with quantities); a total-demand chart with the uncertainty band; the AI business summary.
5. **Products table:** searchable, sortable, filter by risk/class; sparkline per row; risk badges; click-through.
6. **Product detail:** history + forecast chart with shaded 10-90 band and a holiday markers layer; inventory advice panel (reorder point, order qty, days of cover) with editable lead time/service level that recalculates instantly; AI explanation; "How accurate has this been?" panel showing the backtest comparison (chosen model vs baselines) in simple language.
7. **Inventory plan:** exportable table (CSV download), bulk lead-time editing via CSV upload.
8. **Settings:** defaults (lead time, service level, horizon, country), forecasting model choice with the TimesFM license warning, Ollama endpoint + model with a "Test connection" button, privacy statement.
9. **Global:** a persistent job-progress toast for long tasks; a system-status indicator (TimesFM ready / Ollama ready).

Use plain-language copy throughout. No "MASE" in the main UI; show a simple label like "Forecast was off by about 12% on average in past tests" with an info tooltip for the technical detail.

---

## 8. SAMPLE DATA & TESTING

**`scripts/generate_sample_data.py`:** synthetic 2-year daily sales for ~40 products with: weekly + yearly seasonality, trend, festival spikes, promo effects, several intermittent items, a few new products (short history), a discontinued product, injected duplicates, a few outliers, and missing days. Save ground-truth parameters so tests can verify the pipeline recovers the structure.

**Tests (pytest):**
- Ingestion: encodings, delimiters, bad files.
- Cleaning: gap filling only within the active range, returns handling, date-format detection.
- Classification: known ADI/CV² cases land in the right class.
- Metrics: MASE/WAPE/pinball against hand-computed values.
- Inventory: formulas against hand-calculated cases, MOQ/pack-size rounding.
- Narrator validator: rejects text with invented numbers, accepts rounded matches.
- API: end-to-end upload -> mapping -> job -> results on the sample data.
- Frontend: Playwright smoke test of upload -> dashboard -> product detail.

CI-style command `make test` must pass.

---

## 9. PHASES & GATES

After **each phase**: run tests, update docs, commit, then output a short report (what was built, what was verified, known issues, decisions needing my input). **Stop and wait for my go-ahead before the next phase.**

- **Phase 1: Foundations.** Repo, Docker Compose, config, DB, health endpoint, sample data generator, ingestion, column mapping (heuristic first), cleaning, profiling, data-health API. *Accept:* sample data uploads and produces a correct health report.
- **Phase 2: Baselines, backtesting, inventory.** Baseline models, routing, backtest engine, metrics, inventory math. *Accept:* backtest tables generated; inventory unit tests pass. (No TimesFM yet.)
- **Phase 3: TimesFM.** Forecaster wrapper with configurable version, quantiles, batching, device handling (CPU default, GPU if available), covariates if supported. Include it in backtests. *Accept:* comparison shows TimesFM vs baselines honestly on sample data; app still works with TimesFM disabled.
- **Phase 4: Gemma.** Ollama client, LLM column-mapping suggestions, facts builder, narrator, validator, template fallback. *Accept:* validator blocks a deliberately corrupted output in tests; works with Ollama off.
- **Phase 5: Frontend.** All pages in Section 7 wired to the real API. *Accept:* full flow works on sample data in the browser, including error and empty states.
- **Phase 6: Hardening & docs.** Job robustness, large-file handling, timeouts, logging, docker-compose one-command start, README (quick start, hardware notes, model download steps, license notes), USER_GUIDE for shop owners, ARCHITECTURE and DECISIONS docs. *Accept:* fresh clone -> `make up` -> working app following only the README.

---

## 10. WORKING RULES

- Keep functions small, typed, and documented. No dead code, no TODO stubs left in a finished phase.
- Never hardcode paths, ports, model names, or secrets; use config.
- If a requirement is ambiguous or impossible with the real library APIs, choose the most conservative option, record it in `docs/DECISIONS.md`, and flag it in your phase report instead of silently deviating.
- Do not claim something works unless you ran it. Report failing tests honestly.
- Prefer boring, reliable solutions over clever ones.

**Begin with Phase 1. Before writing code, output a short plan: the files you will create and any questions that block you.**