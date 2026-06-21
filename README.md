# Aganai — Stock Analytics Engine

A value investing screener that compares every public company's market cap to its intrinsic value using Discounted Cash Flow (DCF) analysis on SEC 10-K filings.

## Methodology

### DCF Valuation (Pure DCF + Net Debt)

```
Intrinsic Value = (Sum of discounted projected FCFs) + (discounted Terminal Value) - Total Debt + Cash

Where:
  FCF = Operating Cash Flow - Capital Expenditures
  Projected FCF(year) = Latest FCF × (1 + growth_rate)^year
  Discounted FCF(year) = Projected FCF(year) / (1 + discount_rate)^year
  Terminal Value = Final Year FCF × (1 + terminal_growth) / (discount_rate - terminal_growth)
```

### Margin of Safety

```
Margin of Safety = (Intrinsic Value - Market Cap) / Intrinsic Value
```

A positive margin means the stock may be undervalued. A margin above 30% is a strong signal in the Graham/Buffett tradition.

### Why Pure DCF + Net Debt?

This approach values a company by its cash generation ability. We subtract total debt and add cash, but do not separately value balance sheet assets (PP&E, investments, etc.) because the cash flows already reflect the return those assets generate. Adding them would risk double-counting.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Data Ingestion                        │
│  config.py — settings from .env                 │
│  data_fetcher.py — yfinance + SEC EDGAR         │
├─────────────────────────────────────────────────┤
│  Layer 2: Analytics Engine (planned)            │
│  dcf_model.py — intrinsic value calculation     │
│  valuation.py — market cap vs intrinsic value   │
│  screener.py — filter and rank stocks           │
├─────────────────────────────────────────────────┤
│  Layer 3: Output (planned)                      │
│  report.py — terminal tables and CSV export     │
│  main.py — CLI entry point                      │
└─────────────────────────────────────────────────┘
```

## Data Sources

### Market Cap — Yahoo Finance (`yfinance`)
- Free, no API key, no hard rate limit
- Batch-fetchable via `yfinance.Tickers()`
- Provides current market cap, price, shares outstanding

### 10-K Financials — SEC EDGAR
- Official source: every public US company files here
- XBRL API at `data.sec.gov` returns structured JSON
- 10+ years of audited annual financial statements
- Free, no API key (requires User-Agent header with name/email)

## XBRL Field Reference

Fields extracted from SEC EDGAR `companyfacts` API:

| Field | XBRL Tag(s) | Purpose |
|---|---|---|
| Operating Cash Flow | `NetCashProvidedByOperatingActivities`, `NetCashProvidedByUsedInOperatingActivities`, `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | FCF calculation |
| Capital Expenditures | `PaymentsToAcquirePropertyPlantAndEquipment` | FCF calculation |
| Revenue | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet` | Growth rate estimation |
| Net Income | `NetIncomeLoss` | Profitability validation |
| Total Debt | `LongTermDebt`, `LongTermDebtNoncurrent` | Subtracted from DCF value |
| Cash | `CashAndCashEquivalentsAtCarryingValue` | Added to DCF value |
| Shares Outstanding | `CommonStockSharesOutstanding`, `EntityCommonStockSharesOutstanding` | Per-share value |

Multiple XBRL tags are listed per field because companies use different tags in their filings. The fetcher tries each in order.

## Configuration

Settings are loaded from a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `DISCOUNT_RATE` | `0.10` | Rate used to discount future cash flows (WACC proxy) |
| `TERMINAL_GROWTH_RATE` | `0.03` | Perpetual growth rate for terminal value |
| `PROJECTION_YEARS` | `10` | Number of years to project FCF forward |
| `SEC_USER_AGENT` | — | Required by SEC: `"YourName your@email.com"` |

## Setup

```bash
git clone https://github.com/cbip191/aganai.git
cd aganai
pip install -r requirements.txt
# Edit .env with your SEC_USER_AGENT
```

## Usage (current)

```python
from data_fetcher import get_all_tickers, get_market_caps, get_10k_financials

# Get all SEC-filing companies (~10,000)
tickers = get_all_tickers()

# Get market caps
caps = get_market_caps(["AAPL", "MSFT", "GOOG"])

# Get 10+ years of 10-K financials
financials = get_10k_financials("AAPL")
```

## Roadmap

- [x] `config.py` — Configuration management
- [x] `data_fetcher.py` — Data ingestion (yfinance + SEC EDGAR)
- [ ] `data_store.py` — SQLite persistence layer
- [ ] `dcf_model.py` — DCF intrinsic value calculation
- [ ] `valuation.py` — Market cap vs intrinsic value comparison
- [ ] `screener.py` — Filter and rank undervalued stocks
- [ ] `report.py` — Terminal tables and CSV export
- [ ] `main.py` — CLI entry point

## Extending the Model

### Adding balance sheet adjustments
To move from Pure DCF + Net Debt to a full balance sheet model, add these XBRL fields to `data_fetcher.py`'s `XBRL_TAGS`:
- `PropertyPlantAndEquipmentNet` — net fixed assets
- `ShortTermInvestments` — liquid financial assets
- `IntangibleAssetsNetExcludingGoodwill` — patents, licenses
- `Goodwill` — acquisition premium

Then adjust the DCF formula in `dcf_model.py` to add non-operating assets to intrinsic value.

### Adding alternative data sources
To swap or add a data source, implement a new fetch function in `data_fetcher.py` that returns the same dict structure. The analytics layer is source-agnostic.

### Adding valuation methods beyond DCF
Create a new model file (e.g. `graham_model.py`) alongside `dcf_model.py`. The screener can combine signals from multiple models.
