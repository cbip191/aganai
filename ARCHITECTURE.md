# Aganai — Architecture Documentation

## Overview

Aganai is a value investing stock analytics engine. It fetches financial data from SEC EDGAR and Yahoo Finance, performs DCF (Discounted Cash Flow) valuation, and presents results through a Flask web interface. The codebase is organized into three independent service packages that can be extracted into separate microservices.

## Directory Structure

```
aganai/
├── run.py                  # Entry point — starts web server or runs CLI commands
├── config.py               # Shared configuration — loads .env settings
├── db.py                   # Database — schema, init, connection helpers
│
├── data/                   # SERVICE 1: Data Ingestion
│   ├── __init__.py         # Re-exports public API
│   ├── fetcher.py          # Fetch data from SEC EDGAR and Yahoo Finance
│   ├── pipeline.py         # Orchestrate fetch operations with retry, pause, cancel
│   └── store.py            # Read/write data to SQLite
│
├── analytics/              # SERVICE 2: Analytics Engine
│   ├── __init__.py         # Re-exports public API
│   ├── dcf.py              # DCF intrinsic value calculation (simple + investment-adjusted)
│   ├── sector.py           # Sector growth rate analysis
│   ├── investment.py       # Company investment ROI and lag analysis
│   └── valuation.py        # Orchestrate valuation: sector → investment → DCF → compare
│
├── web/                    # SERVICE 3: Web Interface
│   ├── __init__.py         # Flask app factory (create_app)
│   ├── dashboard.py        # GET / — summary stats and quick actions
│   ├── pipeline_routes.py  # GET /pipeline — pipeline control + all /api/pipeline/* endpoints
│   ├── companies.py        # GET /companies, GET /company/<ticker>
│   ├── screener.py         # GET /screener — filter and rank stocks
│   ├── api.py              # /api/company/*, /api/companies/* — data modification endpoints
│   └── templates/          # Jinja2 HTML templates
│       ├── base.html       # Master layout — nav, styling
│       ├── dashboard.html  # Dashboard page
│       ├── pipeline.html   # Pipeline control page
│       ├── companies.html  # Company list with sort/filter/pagination
│       ├── company_detail.html  # Single company: chart, financials, valuation
│       └── screener.html   # Stock screener with filters
│
├── aganai.db               # SQLite database (gitignored)
├── failures.json           # Last pipeline run failures (gitignored)
├── .env                    # Configuration values (gitignored)
├── .gitignore
├── requirements.txt        # Python dependencies
├── README.md               # Project overview and setup
├── ARCHITECTURE.md         # This file
└── CHECKLIST.md            # Project completion tracker
```

## Import Rules

```
config.py, db.py     ← imported by all services (shared)
data/                ← imports config, db
analytics/           ← imports config, db (NEVER imports data/)
web/                 ← imports config, db, data/, analytics/
run.py               ← imports web/
```

No circular imports. Each service can be extracted into its own process by replacing direct imports with API calls.

---

## Service 1: Data Ingestion (`data/`)

### data/fetcher.py — External API Integration

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `get_all_tickers()` | — | `list[dict]` with ticker, name, cik | Fetch all SEC-filing companies (~10,400) |
| `get_10k_financials(ticker)` | ticker: str | `dict[year: dict]` with operating_cf, capex, fcf, revenue, net_income, debt, cash, shares, r_and_d, acquisitions, total_investment | Extract 10+ years of annual financials from SEC EDGAR XBRL API |
| `get_market_caps(tickers, chunk_size=100)` | tickers: list[str] | `dict[ticker: float]` | Batch-fetch current market caps from yfinance |
| `get_price_history(ticker, period, interval)` | ticker: str, period: str ("max"), interval: str ("1wk") | `list[dict]` with date, close, volume | Fetch weekly historical prices from yfinance |
| `get_company_info(tickers, chunk_size=100)` | tickers: list[str] | `dict[ticker: {sector, industry}]` | Batch-fetch sector/industry from yfinance |
| `check_listing_status(ticker)` | ticker: str | `"active"`, `"delisted"`, or `"unknown"` | Check if a ticker is still actively trading |

Internal helpers:
- `_load_cik_map()` — Fetches and caches SEC CIK-to-ticker mapping (30-day cache)
- `_is_full_year(entry)` — Checks if XBRL entry spans >300 days (not quarterly)
- `_extract_annual_values(company_facts, tag_candidates)` — Parses XBRL JSON, merges multiple tags, returns `{year: value}`

### data/pipeline.py — Orchestration

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `run_pipeline(tickers, retry_failures, refresh_financials, fetch_prices)` | All optional | `list[dict]` failures | Main orchestrator — fetches financials, market caps, optionally prices |
| `update_tickers(tickers_to_update)` | Optional list | int count | Refresh SEC ticker list and sector info |
| `scan_listing_status(tickers_to_scan)` | Optional list | — | Scan unknown tickers, mark active/delisted |
| `evaluate_valuations(tickers)` | Optional list | — | Run sector → investment → DCF analysis pipeline |

Module-level state:
- `pipeline_status` — Dict tracking running/paused/cancel_requested/phase/progress/succeeded/failed/total/processed/failures

Control functions:
- `_check_control()` — Blocks while paused; returns False if cancelled
- `_reset_status()` — Initialize for new run
- `_finish_status(elapsed, failures)` — Mark complete/cancelled

Fetch phases:
- `_fetch_all_financials(tickers, db, refresh)` — Loop with retry, rate limiting, pause/cancel
- `_fetch_all_market_caps(tickers, db)` — Batch fetch, auto-mark delisted on failure
- `_fetch_all_prices(tickers, db)` — Loop with pause/cancel

Retry logic:
- `_retry(fn, args, max_retries)` — Exponential backoff (2s, 4s, 8s)
- `_is_retryable(error)` — True for timeouts, connection errors, HTTP 429/500/502/503

### data/store.py — Database I/O

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `_save_financials(db, ticker, data)` | db connection, ticker str, data dict | — | Insert/replace financials rows for a ticker |
| `_save_market_cap(db, ticker, cap, fetch_date)` | db, ticker, cap float, date str | — | Insert/replace one market cap entry |
| `_load_completed(db, table)` | db, table name | `set[str]` | Get tickers already in a table |
| `_load_delisted(db)` | db | `set[str]` | Get tickers marked as delisted |
| `_filter_active(tickers, db)` | list, db | `list[str]` | Remove delisted tickers from a list |
| `_save_failures(failures)` | list of dicts | — | Write failures.json |
| `_load_failures()` | — | `list[str]` | Read tickers from failures.json |

---

## Service 2: Analytics Engine (`analytics/`)

### analytics/dcf.py — DCF Calculation (Pure Math)

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `calculate_intrinsic_value(financials_by_year, sector_growth, company_analysis, discount_rate, terminal_growth, projection_years)` | financials dict, optional sector/investment data, config values | `dict` with intrinsic_value, per_share_value, growth_rate, model_used, etc. or `None` | Main entry — auto-selects simple or investment-adjusted model |
| `estimate_growth_rate(fcf_history)` | list of FCF values | `float` | CAGR from historical FCF, capped -20% to +30% |
| `project_fcf_simple(current_fcf, growth_rate, years)` | float, float, int | `list[float]` | Project FCF using constant growth rate |
| `project_fcf_investment_adjusted(financials_by_year, sector_growth, company_analysis, years)` | dict, dict, dict, int | `list[float]` or `None` | Project FCF using sector growth, investment effectiveness, and lag |
| `discount_cash_flows(cash_flows, discount_rate)` | list[float], float | `float` | Sum of present values of future cash flows |
| `calculate_terminal_value(final_fcf, terminal_growth, discount_rate, years)` | floats | `float` | Gordon Growth Model discounted to present |

Model selection: uses investment-adjusted if sector_growth and company_analysis are provided AND ≥7 years of data. Otherwise falls back to simple CAGR.

### analytics/sector.py — Sector Analysis

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `calculate_sector_growth(db, sector)` | db, sector str | `dict` with sector, growth_rate, num_companies | Weighted average revenue growth rate for all companies in a sector (weight = investment volume) |
| `calculate_all_sectors(db)` | db | `dict[sector: result]` | Run for all sectors, save to sector_metrics table |

Requires ≥7 years of data per company, ≥3 companies per sector.

### analytics/investment.py — Investment Analysis

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `estimate_investment_lag(revenues, investments, max_lag=5)` | list, list, int | `int` (0-5) | Cross-correlation to find optimal lag between investment and revenue impact |
| `calculate_roi_series(revenues, investments, lag)` | list, list, int | `list[float]` | Annual ROI = revenue_change / investment(year - lag) |
| `analyze_company_investment(db, ticker)` | db, ticker str | `dict` or `None` | Full investment analysis: lag, ROI, trend, effectiveness vs sector |
| `analyze_all_companies(db, tickers)` | db, optional list | `dict[ticker: result]` | Batch analysis, saves to investment_metrics table |

### analytics/valuation.py — Valuation Orchestration

| Function | Parameters | Returns | Purpose |
|---|---|---|---|
| `evaluate_company(ticker, db)` | ticker str, db | `dict` or `None` | Full evaluation: reads financials + sector + investment metrics → DCF → margin of safety |
| `evaluate_all(db, tickers)` | db, optional list | `list[dict]` | Batch evaluation, sorted by margin of safety descending |
| `save_valuations(db, valuations)` | db, list | — | Persist results to valuations table |

---

## Service 3: Web Interface (`web/`)

### Pages

| Route | File | Method | Purpose |
|---|---|---|---|
| `/` | dashboard.py | GET | Summary stats, coverage numbers, quick action buttons |
| `/pipeline` | pipeline_routes.py | GET | Pipeline control: progress bar, coverage bars, fetch controls, pause/resume/cancel |
| `/companies` | companies.py | GET | Paginated company list with sort, search, sector filter, bulk actions |
| `/company/<ticker>` | companies.py | GET | Company detail: valuation card, market cap chart, financials table, inline edit |
| `/screener` | screener.py | GET | Stock screener with filters, DCF values, margin of safety, load-more pagination |

### API Endpoints

| Route | File | Method | Purpose |
|---|---|---|---|
| `/api/pipeline/status` | pipeline_routes.py | GET | Returns pipeline_status as JSON (polled by UI) |
| `/api/pipeline/pause` | pipeline_routes.py | POST | Pause running pipeline |
| `/api/pipeline/resume` | pipeline_routes.py | POST | Resume paused pipeline |
| `/api/pipeline/cancel` | pipeline_routes.py | POST | Cancel running pipeline (saves progress) |
| `/api/pipeline/run` | pipeline_routes.py | POST | Start pipeline (accepts tickers, refresh_financials) |
| `/api/pipeline/retry` | pipeline_routes.py | POST | Retry failed tickers from failures.json |
| `/api/pipeline/fetch-prices` | pipeline_routes.py | POST | Fetch price history |
| `/api/pipeline/evaluate` | pipeline_routes.py | POST | Run DCF evaluation for all companies |
| `/api/pipeline/fetch-missing` | pipeline_routes.py | POST | Fetch data for companies that don't have it |
| `/api/pipeline/fetch-by-sector` | pipeline_routes.py | POST | Fetch data for selected sectors |
| `/api/pipeline/refresh-stale` | pipeline_routes.py | POST | Re-fetch data older than N days |
| `/api/pipeline/scan-status` | pipeline_routes.py | POST | Scan unknown tickers for listing status |
| `/api/tickers/update` | pipeline_routes.py | POST | Refresh SEC ticker list + sector info |
| `/api/company/<ticker>/refetch` | api.py | POST | Re-fetch all data for one company |
| `/api/company/<ticker>/update` | api.py | POST | Manually edit financial fields for a year |
| `/api/companies/bulk-refetch` | api.py | POST | Re-fetch data for selected companies |
| `/api/companies/bulk-evaluate` | api.py | POST | Run DCF for selected companies |

---

## Database Schema

### companies
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK | Stock ticker symbol |
| name | TEXT | | Company name from SEC |
| cik | TEXT | | SEC CIK identifier |
| sector | TEXT | | Business sector from yfinance |
| industry | TEXT | | Industry from yfinance |
| status | TEXT | | "active", "delisted", or "unknown" |
| updated_at | TEXT | | ISO timestamp of last update |

### financials
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK1 | Stock ticker |
| year | INTEGER | PK2 | Fiscal year |
| operating_cf | REAL | | Cash from operations |
| capex | REAL | | Capital expenditures |
| fcf | REAL | | Free cash flow (operating_cf - capex) |
| revenue | REAL | | Total revenue |
| net_income | REAL | | Net income |
| debt | REAL | | Long-term debt |
| cash | REAL | | Cash and equivalents |
| shares | REAL | | Shares outstanding |
| r_and_d | REAL | | R&D expense |
| acquisitions | REAL | | Acquisition payments |
| total_investment | REAL | | capex + r_and_d + acquisitions |
| fetched_at | TEXT | | ISO timestamp |

### market_caps
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK1 | Stock ticker |
| fetch_date | TEXT | PK2 | Date of fetch (YYYY-MM-DD) |
| market_cap | REAL | | Market capitalization in USD |

### price_history
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK1 | Stock ticker |
| date | TEXT | PK2 | Week start date |
| close_price | REAL | | Weekly closing price |
| volume | INTEGER | | Weekly trading volume |

### sector_metrics
| Column | Type | Key | Description |
|---|---|---|---|
| sector | TEXT | PK | Sector name |
| growth_rate | REAL | | Investment-weighted avg revenue growth |
| avg_roi | REAL | | Average ROI across sector |
| num_companies | INTEGER | | Companies with sufficient data |
| calculated_at | TEXT | | ISO timestamp |

### investment_metrics
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK | Stock ticker |
| avg_roi | REAL | | Company's average investment ROI |
| sector_avg_roi | REAL | | Sector average ROI for comparison |
| investment_lag | INTEGER | | Years between investment and revenue impact |
| effectiveness | TEXT | | "above", "below", or "average" vs sector |
| roi_trend | TEXT | | "improving", "declining", or "stable" |
| calculated_at | TEXT | | ISO timestamp |

### valuations
| Column | Type | Key | Description |
|---|---|---|---|
| ticker | TEXT | PK | Stock ticker |
| intrinsic_value | REAL | | DCF-computed intrinsic value |
| per_share_value | REAL | | intrinsic_value / shares |
| market_cap | REAL | | Market cap at time of calculation |
| margin_of_safety | REAL | | (intrinsic - market_cap) / intrinsic |
| growth_rate | REAL | | Growth rate used in projection |
| model_used | TEXT | | "simple" or "investment-adjusted" |
| discount_rate | REAL | | Discount rate used |
| calculated_at | TEXT | | ISO timestamp |

---

## Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `DISCOUNT_RATE` | 0.10 | WACC proxy for discounting future cash flows |
| `TERMINAL_GROWTH_RATE` | 0.03 | Perpetual growth rate for terminal value |
| `PROJECTION_YEARS` | 10 | Years to project FCF forward |
| `SEC_USER_AGENT` | — | Required: "YourName email@example.com" |
| `DB_PATH` | `<project_dir>/aganai.db` | SQLite database path |
| `MAX_RETRIES` | 3 | Retry attempts for failed API calls |
| `SEC_RATE_LIMIT` | 0.1 | Min seconds between SEC requests |
| `PROGRESS_LOG_INTERVAL` | 50 | Log progress every N tickers |

---

## How to Add New Features

### New Analytics Model (e.g. Graham Number)
1. Create `analytics/graham.py` with pure calculation functions
2. Add an orchestration function in `analytics/valuation.py` or create a new one
3. Add the new model to `evaluate_company()` selection logic
4. Import in `analytics/__init__.py`
5. No changes to data/ or web/ needed unless the model requires new data fields

### New Data Source (e.g. quarterly earnings)
1. Add fetch function in `data/fetcher.py`
2. Add save/load functions in `data/store.py`
3. Add the new table definition in `db.py`
4. Add a pipeline phase in `data/pipeline.py`
5. Add UI controls in `web/pipeline_routes.py`

### New Web Page (e.g. sector comparison)
1. Create `web/sector_view.py` with a Flask Blueprint
2. Create `web/templates/sector_view.html`
3. Register the blueprint in `web/__init__.py`
4. Add nav link in `web/templates/base.html`

### New API Endpoint
1. Add to `web/api.py` (data modification) or `web/pipeline_routes.py` (pipeline actions)
2. If it needs a new pipeline operation, add to `data/pipeline.py`

---

## CLI Usage

```bash
python3 run.py                          # Start web server (default)
python3 run.py --web                    # Start web server (explicit)
python3 run.py AAPL MSFT                # Fetch financials + market caps for specific tickers
python3 run.py --fetch-prices AAPL      # Fetch price history
python3 run.py --evaluate               # Run DCF evaluation for all
python3 run.py --evaluate AAPL MSFT     # Evaluate specific tickers
python3 run.py --retry                  # Retry failed tickers
python3 run.py --refresh-financials     # Re-fetch all financials
python3 run.py --scan-status            # Scan unknown tickers for listing status
python3 run.py --update-tickers         # Refresh SEC ticker list
```
