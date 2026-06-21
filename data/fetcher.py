import logging
import time
import requests
import yfinance as yf
from config import SEC_USER_AGENT

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("data_fetcher")

SEC_HEADERS = {"User-Agent": SEC_USER_AGENT}

_cik_map = None
_cik_map_loaded_at = None
CIK_CACHE_DAYS = 30


def _load_cik_map():
    global _cik_map, _cik_map_loaded_at
    if _cik_map is not None and _cik_map_loaded_at is not None:
        age = (time.time() - _cik_map_loaded_at) / 86400
        if age < CIK_CACHE_DAYS:
            return _cik_map
    log.info("Fetching SEC ticker list...")
    t0 = time.time()
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = requests.get(url, headers=SEC_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    _cik_map = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        _cik_map[ticker] = {
            "cik": entry["cik_str"],
            "name": entry["title"],
            "ticker": ticker,
        }
    _cik_map_loaded_at = time.time()
    log.info("Loaded %d tickers from SEC in %.1fs", len(_cik_map), time.time() - t0)
    return _cik_map


def get_all_tickers():
    cik_map = _load_cik_map()
    return list(cik_map.values())


def check_listing_status(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        cap = info.get("marketCap")
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if cap and price:
            return "active"
        hist = tk.history(period="5d")
        if hist.empty:
            return "delisted"
        from datetime import datetime, timedelta
        last_date = hist.index[-1].to_pydatetime().replace(tzinfo=None)
        if (datetime.now() - last_date).days > 30:
            return "delisted"
        return "active"
    except Exception:
        return "unknown"


def get_price_history(ticker, period="max", interval="1wk"):
    log.info("%s — fetching price history (period=%s, interval=%s)", ticker, period, interval)
    t0 = time.time()
    tk = yf.Ticker(ticker)
    hist = tk.history(period=period, interval=interval)
    if hist.empty:
        log.warning("%s — no price history returned", ticker)
        return []
    rows = []
    for date, row in hist.iterrows():
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "close": row["Close"],
            "volume": int(row["Volume"]),
        })
    log.info("%s — %d price rows in %.1fs", ticker, len(rows), time.time() - t0)
    return rows


def get_company_info(tickers, chunk_size=100):
    log.info("Fetching company info for %d tickers...", len(tickers))
    t0 = time.time()
    results = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        chunk_num = i // chunk_size + 1
        total_chunks = (len(tickers) + chunk_size - 1) // chunk_size
        log.info("  Info chunk %d/%d (%d tickers)", chunk_num, total_chunks, len(chunk))
        batch = yf.Tickers(" ".join(chunk))
        for ticker in chunk:
            try:
                info = batch.tickers[ticker].info
                results[ticker] = {
                    "sector": info.get("sector", ""),
                    "industry": info.get("industry", ""),
                }
            except Exception:
                continue
        if i + chunk_size < len(tickers):
            time.sleep(1)
    elapsed = time.time() - t0
    log.info("Company info: %d succeeded in %.1fs", len(results), elapsed)
    return results


def get_market_caps(tickers, chunk_size=100):
    log.info("Fetching market caps for %d tickers...", len(tickers))
    t0 = time.time()
    results = {}
    failed = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        chunk_num = i // chunk_size + 1
        total_chunks = (len(tickers) + chunk_size - 1) // chunk_size
        log.info("  Chunk %d/%d (%d tickers)", chunk_num, total_chunks, len(chunk))
        batch = yf.Tickers(" ".join(chunk))
        for ticker in chunk:
            try:
                info = batch.tickers[ticker].info
                cap = info.get("marketCap")
                if cap:
                    results[ticker] = cap
                else:
                    failed.append((ticker, "no marketCap field"))
            except requests.exceptions.ConnectionError as e:
                failed.append((ticker, f"connection error: {e}"))
            except requests.exceptions.Timeout as e:
                failed.append((ticker, f"timeout: {e}"))
            except Exception as e:
                failed.append((ticker, f"{type(e).__name__}: {e}"))
        if i + chunk_size < len(tickers):
            time.sleep(1)
    elapsed = time.time() - t0
    log.info("Market caps: %d succeeded, %d failed in %.1fs", len(results), len(failed), elapsed)
    for ticker, reason in failed:
        log.warning("  %s — %s", ticker, reason)
    return results


XBRL_TAGS = {
    "operating_cf": [
        "NetCashProvidedByOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "CapitalExpenditureDiscontinuedOperations",
    ],
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": [
        "NetIncomeLoss",
    ],
    "debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "shares": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "r_and_d": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "acquisitions": [
        "PaymentsToAcquireBusinessesNetOfCashAcquired",
        "PaymentsToAcquireBusinessesGross",
        "BusinessCombinationConsiderationTransferred1",
    ],
}


def _is_full_year(entry):
    from datetime import datetime

    start = entry.get("start", "")
    end = entry.get("end", "")
    if not start or not end:
        return True
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        days = (d1 - d0).days
        return days > 300
    except ValueError:
        return True


def _extract_annual_values(company_facts, tag_candidates):
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    candidates = []
    for tag in tag_candidates:
        concept = us_gaap.get(tag)
        if not concept:
            continue
        units = concept.get("units", {})
        unit_data = units.get("USD") or units.get("shares") or units.get("pure")
        if not unit_data:
            continue
        annual = {}
        for entry in unit_data:
            if entry.get("form") != "10-K" or entry.get("fp") != "FY":
                continue
            frame = entry.get("frame", "")
            if frame and "Q" in frame:
                continue
            if not _is_full_year(entry):
                continue
            year = entry.get("fy")
            val = entry.get("val")
            if year and val is not None:
                annual[year] = val
        if annual:
            candidates.append(annual)
    if not candidates:
        return {}
    merged = {}
    for c in candidates:
        merged.update(c)
    return merged


def get_yahoo_financials(ticker):
    log.info("%s — fetching financials from Yahoo Finance (fallback)", ticker)
    t0 = time.time()
    try:
        tk = yf.Ticker(ticker)
        cf = tk.cashflow
        bs = tk.balance_sheet
        inc = tk.financials
        if cf is None or cf.empty:
            log.warning("%s — no Yahoo Finance data available", ticker)
            return {}

        result = {}
        for col in cf.columns:
            year = col.year

            def get_val(df, keys):
                if df is None or df.empty:
                    return None
                for k in keys:
                    if k in df.index and col in df.columns:
                        v = df.loc[k, col]
                        if v is not None and str(v) != "nan":
                            return float(v)
                return None

            operating_cf = get_val(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            capex = get_val(cf, ["Capital Expenditure", "Capital Expenditures"])
            if capex and capex < 0:
                capex = abs(capex)

            row = {
                "operating_cf": operating_cf,
                "capex": capex,
                "revenue": get_val(inc, ["Total Revenue", "Revenue"]),
                "net_income": get_val(inc, ["Net Income", "Net Income From Continuing Operations"]),
                "debt": get_val(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"]),
                "cash": get_val(bs, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
                "shares": get_val(bs, ["Ordinary Shares Number", "Share Issued"]),
                "r_and_d": get_val(inc, ["Research And Development", "Research Development"]),
                "acquisitions": get_val(cf, ["Acquisitions Net", "Purchase Of Business"]),
                "data_source": "yahoo_finance",
            }

            if operating_cf is not None and capex is not None:
                row["fcf"] = operating_cf - capex
            else:
                row["fcf"] = None

            inv_parts = [row.get("capex"), row.get("r_and_d"), row.get("acquisitions")]
            inv_sum = sum(abs(v) for v in inv_parts if v is not None)
            row["total_investment"] = inv_sum if any(v is not None for v in inv_parts) else None

            result[year] = row

        log.info("%s — %d years from Yahoo Finance in %.1fs", ticker, len(result), time.time() - t0)
        return result
    except Exception as e:
        log.warning("%s — Yahoo Finance fallback failed: %s", ticker, e)
        return {}


def get_10k_financials(ticker):
    cik_map = _load_cik_map()
    ticker = ticker.upper()
    if ticker not in cik_map:
        log.warning("%s — not found in SEC ticker list", ticker)
        return {}
    cik = str(cik_map[ticker]["cik"]).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    t0 = time.time()
    resp = requests.get(url, headers=SEC_HEADERS)
    if resp.status_code != 200:
        log.warning("%s — SEC EDGAR returned HTTP %d", ticker, resp.status_code)
        return {}
    facts = resp.json()

    field_data = {}
    for field_name, tag_candidates in XBRL_TAGS.items():
        field_data[field_name] = _extract_annual_values(facts, tag_candidates)

    all_years = set()
    for values in field_data.values():
        all_years.update(values.keys())

    result = {}
    for year in sorted(all_years):
        row = {}
        for field_name in XBRL_TAGS:
            row[field_name] = field_data[field_name].get(year)
        ocf = row.get("operating_cf")
        capex = row.get("capex")
        if ocf is not None and capex is not None:
            row["fcf"] = ocf - capex
        else:
            row["fcf"] = None
        inv_parts = [row.get("capex"), row.get("r_and_d"), row.get("acquisitions")]
        inv_sum = sum(v for v in inv_parts if v is not None)
        row["total_investment"] = inv_sum if any(v is not None for v in inv_parts) else None
        result[year] = row

    missing_fields = [f for f, v in field_data.items() if not v]
    years = sorted(result.keys())
    elapsed = time.time() - t0
    if missing_fields:
        log.warning("%s — %d years (%s–%s) in %.1fs, missing fields: %s",
                    ticker, len(years), years[0] if years else "?", years[-1] if years else "?",
                    elapsed, ", ".join(missing_fields))
    else:
        log.info("%s — %d years (%s–%s) in %.1fs, all fields populated",
                 ticker, len(years), years[0] if years else "?", years[-1] if years else "?",
                 elapsed)
    return result
