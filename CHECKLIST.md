# Aganai — Project Checklist

## Layer 1: Data Ingestion
- [x] `config.py` — Load settings from .env (discount rate, growth rate, projection years, SEC credentials, DB path, retry limits)
- [x] `data_fetcher.py` — SEC EDGAR ticker list (~10,400 companies with CIK mapping)
- [x] `data_fetcher.py` — 10-K financial data extraction (operating CF, capex, FCF, revenue, net income, debt, cash, shares)
- [x] `data_fetcher.py` — XBRL tag handling with multiple fallback tags per field
- [x] `data_fetcher.py` — Full-year vs quarterly filtering for 10-K entries
- [x] `data_fetcher.py` — Market cap fetching via yfinance (batch, chunked)
- [x] `data_fetcher.py` — Company info fetching (sector, industry) via yfinance
- [x] `data_fetcher.py` — Weekly price history fetching via yfinance
- [x] `data_fetcher.py` — CIK map caching with 30-day expiry

## Layer 1.5: Pipeline & Orchestration
- [x] `pipeline.py` — SQLite database schema (companies, market_caps, financials, price_history)
- [x] `pipeline.py` — Checkpoint/resume (skip already-fetched tickers)
- [x] `pipeline.py` — Retry with exponential backoff (2s/4s/8s, max 3 retries)
- [x] `pipeline.py` — Retryable vs permanent error classification
- [x] `pipeline.py` — SEC rate limiting (0.1s between requests)
- [x] `pipeline.py` — Failure tracking and failures.json export
- [x] `pipeline.py` — Progress logging with ETA and throughput
- [x] `pipeline.py` — Pause/resume/cancel controls
- [x] `pipeline.py` — Incremental DB commits (data visible during processing)
- [x] `pipeline.py` — Update tickers function (SEC list + sector info, no financials)
- [x] `pipeline.py` — Historical price fetching phase
- [x] `pipeline.py` — Market caps stored with daily granularity (historical tracking)
- [x] `pipeline.py` — CLI interface (--retry, --refresh-financials, --fetch-prices)

## Layer 2: Analytics Engine
- [ ] `dcf_model.py` — DCF intrinsic value calculation
- [ ] `dcf_model.py` — FCF growth rate estimation from historical data
- [ ] `dcf_model.py` — Terminal value computation
- [ ] `dcf_model.py` — Discount projected cash flows to present value
- [ ] `valuation.py` — Market cap vs intrinsic value comparison
- [ ] `valuation.py` — Margin of safety calculation
- [ ] `screener.py` — Rank stocks by margin of safety
- [ ] `screener.py` — Filter by sector, min margin of safety, custom criteria

## Layer 3: Output
- [ ] `report.py` — Terminal table output (tabulate)
- [ ] `report.py` — CSV export of screener results
- [ ] `main.py` — Unified CLI entry point for all operations

## Web Application
- [x] `app.py` — Flask server with 5 pages
- [x] Dashboard — Summary stats, quick action buttons (Run Pipeline, Retry, Update Tickers, Fetch Prices)
- [x] Pipeline Control — Progress bar, pause/resume/cancel, run form with ticker input and refresh option
- [x] Companies — Searchable, sortable, paginated table (100/page)
- [x] Companies — Sector filter dropdown with multi-select and search
- [x] Companies — Bulk select with Refetch Selected and Re-evaluate Selected
- [x] Company Detail — Company name, sector, industry display
- [x] Company Detail — Market cap history chart (Chart.js, weekly data from price_history)
- [x] Company Detail — Financials table (all years, all fields)
- [x] Company Detail — Inline edit modal for manual data overrides
- [x] Company Detail — Single ticker refetch button
- [x] Screener — Filter by sector, min FCF, max debt, min revenue, min cash
- [x] Screener — Bulk select with refetch/evaluate actions
- [ ] Screener — DCF Value column (placeholder, shows "—")
- [ ] Screener — Margin of Safety column (placeholder, shows "—")
- [ ] Screener — Sort by DCF value or margin of safety
- [ ] Bulk evaluate — Actually runs DCF calculation (currently returns placeholder message)

## Database
- [x] `companies` table — ticker, name, cik, sector, industry, updated_at
- [x] `market_caps` table — ticker, market_cap, fetch_date (daily historical)
- [x] `financials` table — ticker, year, operating_cf, capex, fcf, revenue, net_income, debt, cash, shares
- [x] `price_history` table — ticker, date, close_price, volume (weekly historical)

## Documentation
- [x] `README.md` — Project overview, methodology (Pure DCF + Net Debt), architecture diagram
- [x] `README.md` — Data sources documentation (yfinance + SEC EDGAR)
- [x] `README.md` — XBRL field reference table
- [x] `README.md` — Configuration variables documentation
- [x] `README.md` — Setup and usage instructions
- [x] `README.md` — Roadmap with completion status
- [x] `README.md` — Extension guide (balance sheet adjustments, alternative sources, new valuation methods)
- [ ] `README.md` — Update roadmap to reflect current state (price history, web app)

## Git & Deployment
- [x] `.gitignore` — Python, .env, DB, cache files
- [x] `requirements.txt` — All dependencies listed
- [x] GitHub repo created (cbip191/aganai)
- [ ] Initial commit with all current code
- [ ] CLAUDE.md for project-specific instructions
