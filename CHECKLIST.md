# Aganai — Project Checklist

## Architecture
- [x] Microservice-ready package structure (data/, analytics/, web/)
- [x] `db.py` — Single source of truth for DB schema and connections
- [x] `run.py` — Unified entry point (web server + CLI)
- [x] `ARCHITECTURE.md` — Full documentation of every file, function, route, table, and config
- [x] No circular imports — clear dependency direction (data ← analytics ← web)

## Data Service (`data/`)

### data/fetcher.py — External API Integration
- [x] SEC EDGAR ticker list (~10,400 companies with CIK mapping)
- [x] 10-K financial data extraction (operating CF, capex, FCF, revenue, net income, debt, cash, shares)
- [x] R&D and acquisitions XBRL fields
- [x] XBRL tag handling with multiple fallback tags per field
- [x] Full-year vs quarterly filtering for 10-K entries
- [x] Market cap fetching via yfinance (batch, chunked)
- [x] Company info fetching (sector, industry) via yfinance
- [x] Weekly price history fetching via yfinance
- [x] CIK map caching with 30-day expiry
- [x] Listing status detection (active/delisted via yfinance)

### data/pipeline.py — Orchestration
- [x] Checkpoint/resume (skip already-fetched tickers)
- [x] Retry with exponential backoff (2s/4s/8s, max 3 retries)
- [x] Retryable vs permanent error classification
- [x] SEC rate limiting (0.1s between requests)
- [x] Failure tracking and failures.json export
- [x] Progress logging with ETA and throughput
- [x] Pause/resume/cancel controls
- [x] Incremental DB commits (data visible during processing)
- [x] Update tickers function (SEC list + sector info)
- [x] Historical price fetching phase
- [x] Market caps stored with daily granularity
- [x] Listing status scanning (scan_listing_status)
- [x] Auto-mark delisted on failed market cap fetch
- [x] Skip delisted tickers in all fetch operations
- [x] DCF evaluation orchestration (evaluate_valuations)

### data/store.py — Database I/O
- [x] Save/load financials, market caps, failures
- [x] Load completed tickers, delisted tickers
- [x] Filter active tickers

### Data Coverage (in progress)
- [x] Ticker list loaded (10,433 companies)
- [ ] Listing status scanned (10,433 unknown)
- [ ] Sector/industry data for all companies (551/10,433)
- [ ] 10-K financials for all active companies (in progress)
- [ ] Market cap data for all active companies
- [ ] Price history for all active companies
- [ ] Re-fetch financials with R&D and acquisitions fields

## Analytics Service (`analytics/`)

### analytics/dcf.py — DCF Calculation
- [x] Simple DCF (CAGR-based, fallback for <7 years data)
- [x] Investment-adjusted DCF (sector growth + company ROI + investment lag)
- [x] FCF growth rate estimation (CAGR with -20%/+30% caps)
- [x] Terminal value computation (Gordon Growth Model)
- [x] Discount projected cash flows to present value

### analytics/sector.py — Sector Analysis
- [x] Weighted sector growth rate calculation
- [x] Sector average ROI computation

### analytics/investment.py — Investment Analysis
- [x] Investment lag estimation (cross-correlation, 0-5yr)
- [x] Company ROI series and trend analysis
- [x] Company vs sector ROI effectiveness comparison

### analytics/valuation.py — Valuation Orchestration
- [x] Market cap vs intrinsic value comparison
- [x] Margin of safety calculation
- [x] Batch evaluation and DB persistence

## Web Service (`web/`)

### Pages
- [x] Dashboard — Summary stats, coverage numbers, quick action buttons
- [x] Pipeline Control — Progress bar, pause/resume/cancel, coverage bars, fetch controls
- [x] Companies — Searchable, sortable, paginated (100/page), sector filter dropdown
- [x] Company Detail — Valuation card, market cap chart, financials table, inline edit
- [x] Screener — Filters, DCF Value, Margin of Safety, Model columns, load-more pagination

### UI Controls
- [x] "Fetch Missing" buttons (financials, prices, sectors)
- [x] "Fetch by Sector" with multi-select
- [x] "Fetch Specific Companies" with ticker input
- [x] "Refresh Stale Data" with configurable age
- [x] "Evaluate All Companies" button
- [x] "Scan Unknown" for listing status
- [x] Bulk select with Refetch/Re-evaluate
- [x] Single ticker refetch and manual edit
- [ ] Screener — Sort by DCF value or margin of safety

## Database
- [x] `companies` — ticker, name, cik, sector, industry, status, updated_at
- [x] `financials` — ticker, year, 13 financial fields + fetched_at
- [x] `market_caps` — ticker, market_cap, fetch_date (daily historical)
- [x] `price_history` — ticker, date, close_price, volume (weekly)
- [x] `sector_metrics` — sector, growth_rate, avg_roi, num_companies
- [x] `investment_metrics` — ticker, avg_roi, sector_avg_roi, lag, effectiveness, trend
- [x] `valuations` — ticker, intrinsic_value, per_share_value, margin, model_used

## Documentation
- [x] `README.md` — Project overview, methodology, data sources, setup
- [x] `ARCHITECTURE.md` — Complete documentation of every file, function, route, table, config, and how-to guides
- [x] `CHECKLIST.md` — Project completion tracker
- [ ] `README.md` — Update to reflect new architecture and CLI usage

## Output (Planned)
- [ ] CSV export of screener results
- [ ] Terminal table output

## Git & Deployment
- [x] `.gitignore` — Python, .env, DB, cache files
- [x] `requirements.txt` — All dependencies listed
- [x] GitHub repo created (cbip191/aganai)
- [x] Initial commit
- [ ] Commit microservice refactor
- [ ] CLAUDE.md for project-specific instructions
