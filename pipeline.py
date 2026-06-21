import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

import requests

from config import DB_PATH, MAX_RETRIES, PROGRESS_LOG_INTERVAL, SEC_RATE_LIMIT
from data_fetcher import get_10k_financials, get_all_tickers, get_company_info, get_market_caps, get_price_history

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("pipeline")

RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
FAILURES_PATH = "failures.json"

pipeline_status = {
    "running": False,
    "paused": False,
    "cancel_requested": False,
    "phase": "",
    "progress": "",
    "succeeded": 0,
    "failed": 0,
    "total": 0,
    "processed": 0,
    "failures": [],
}


def _check_control():
    while pipeline_status["paused"] and not pipeline_status["cancel_requested"]:
        time.sleep(0.5)
    return not pipeline_status["cancel_requested"]


def _reset_status():
    pipeline_status["running"] = True
    pipeline_status["paused"] = False
    pipeline_status["cancel_requested"] = False
    pipeline_status["phase"] = "starting"
    pipeline_status["progress"] = ""
    pipeline_status["succeeded"] = 0
    pipeline_status["failed"] = 0
    pipeline_status["total"] = 0
    pipeline_status["processed"] = 0
    pipeline_status["failures"] = []


def _finish_status(elapsed, failures):
    pipeline_status["running"] = False
    pipeline_status["paused"] = False
    if pipeline_status["cancel_requested"]:
        pipeline_status["phase"] = "cancelled"
        pipeline_status["progress"] = f"Cancelled after {elapsed:.1f}s"
    else:
        pipeline_status["phase"] = "complete"
        pipeline_status["progress"] = f"Done in {elapsed:.1f}s"
    pipeline_status["cancel_requested"] = False
    pipeline_status["failures"] = failures


def _init_db(path):
    db = sqlite3.connect(path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            cik TEXT,
            sector TEXT,
            industry TEXT,
            updated_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_caps (
            ticker TEXT,
            market_cap REAL,
            fetch_date TEXT,
            PRIMARY KEY (ticker, fetch_date)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS financials (
            ticker TEXT,
            year INTEGER,
            operating_cf REAL,
            capex REAL,
            fcf REAL,
            revenue REAL,
            net_income REAL,
            debt REAL,
            cash REAL,
            shares REAL,
            fetched_at TEXT,
            PRIMARY KEY (ticker, year)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            ticker TEXT,
            date TEXT,
            close_price REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    db.commit()
    return db


def update_tickers(tickers_to_update=None):
    log.info("Updating ticker list...")
    _reset_status()
    pipeline_status["phase"] = "updating tickers"
    pipeline_status["progress"] = "Fetching SEC ticker list..."
    t0 = time.time()

    db = _init_db(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()

    all_tickers = get_all_tickers()
    if tickers_to_update:
        tickers_to_update = {t.upper() for t in tickers_to_update}
        all_tickers = [t for t in all_tickers if t["ticker"] in tickers_to_update]

    pipeline_status["total"] = len(all_tickers)
    batch_size = 100
    for i in range(0, len(all_tickers), batch_size):
        if not _check_control():
            break
        batch = all_tickers[i : i + batch_size]
        for t in batch:
            db.execute(
                """INSERT OR REPLACE INTO companies (ticker, name, cik, sector, industry, updated_at)
                   VALUES (?, ?, ?, COALESCE((SELECT sector FROM companies WHERE ticker = ?), ''),
                           COALESCE((SELECT industry FROM companies WHERE ticker = ?), ''), ?)""",
                (t["ticker"], t["name"], t["cik"], t["ticker"], t["ticker"], now),
            )
        db.commit()
        pipeline_status["processed"] = min(i + batch_size, len(all_tickers))
        pipeline_status["progress"] = f"Saving tickers: {pipeline_status['processed']}/{len(all_tickers)}"
    log.info("Saved %d tickers to DB", len(all_tickers))

    if not pipeline_status["cancel_requested"]:
        needs_sector = db.execute(
            "SELECT ticker FROM companies WHERE sector = '' OR sector IS NULL"
        ).fetchall()
        needs_sector = [r[0] for r in needs_sector]
        if tickers_to_update:
            needs_sector = [t for t in needs_sector if t in tickers_to_update]

        if needs_sector:
            pipeline_status["phase"] = "fetching sectors"
            pipeline_status["total"] = len(needs_sector)
            pipeline_status["processed"] = 0
            log.info("Fetching sector info for %d tickers...", len(needs_sector))

            chunk_size = 50
            for i in range(0, len(needs_sector), chunk_size):
                if not _check_control():
                    break
                chunk = needs_sector[i : i + chunk_size]
                info = get_company_info(chunk, chunk_size=chunk_size)
                for ticker, data in info.items():
                    if data.get("sector"):
                        db.execute(
                            "UPDATE companies SET sector = ?, industry = ?, updated_at = ? WHERE ticker = ?",
                            (data["sector"], data.get("industry", ""), now, ticker),
                        )
                db.commit()
                pipeline_status["processed"] = min(i + chunk_size, len(needs_sector))
                pipeline_status["succeeded"] = pipeline_status["processed"]
                pipeline_status["progress"] = f"Sectors: {pipeline_status['processed']}/{len(needs_sector)}"
            log.info("Updated sector info")

    _finish_status(time.time() - t0, [])
    db.close()
    return len(all_tickers)


def _load_completed(db, table):
    rows = db.execute(f"SELECT DISTINCT ticker FROM {table}").fetchall()
    return {r[0] for r in rows}


def _save_financials(db, ticker, data):
    now = datetime.now(timezone.utc).isoformat()
    for year, row in data.items():
        db.execute(
            """INSERT OR REPLACE INTO financials
               (ticker, year, operating_cf, capex, fcf, revenue, net_income, debt, cash, shares, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, year, row.get("operating_cf"), row.get("capex"),
                row.get("fcf"), row.get("revenue"), row.get("net_income"),
                row.get("debt"), row.get("cash"), row.get("shares"), now,
            ),
        )
    db.commit()


def _save_market_cap(db, ticker, cap, fetch_date):
    db.execute(
        "INSERT OR REPLACE INTO market_caps (ticker, market_cap, fetch_date) VALUES (?, ?, ?)",
        (ticker, cap, fetch_date),
    )
    db.commit()


def _is_retryable(error):
    if isinstance(error, requests.exceptions.ConnectionError):
        return True
    if isinstance(error, requests.exceptions.Timeout):
        return True
    if isinstance(error, requests.exceptions.HTTPError):
        resp = error.response
        if resp is not None and resp.status_code in RETRYABLE_STATUS_CODES:
            return True
    return False


def _retry(fn, args, max_retries=MAX_RETRIES):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = fn(*args)
            return True, result
        except Exception as e:
            last_error = e
            if not _is_retryable(e):
                return False, e
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                log.warning("  Retry %d/%d in %ds — %s", attempt + 1, max_retries, delay, e)
                time.sleep(delay)
    return False, last_error


def _fetch_all_financials(tickers, db, refresh=False):
    if refresh:
        remaining = list(tickers)
        log.info("Financials: %d total, refreshing all", len(tickers))
    else:
        completed = _load_completed(db, "financials")
        remaining = [t for t in tickers if t not in completed]
        log.info("Financials: %d total, %d already done, %d remaining",
                 len(tickers), len(completed), len(remaining))

    succeeded = 0
    failures = []
    t0 = time.time()
    last_request_at = 0
    pipeline_status["phase"] = "financials"
    pipeline_status["total"] = len(remaining)
    pipeline_status["processed"] = 0

    for i, ticker in enumerate(remaining):
        if not _check_control():
            break

        now = time.time()
        wait = SEC_RATE_LIMIT - (now - last_request_at)
        if wait > 0:
            time.sleep(wait)

        ok, result = _retry(get_10k_financials, (ticker,))
        last_request_at = time.time()

        if ok and result:
            _save_financials(db, ticker, result)
            succeeded += 1
        else:
            error_str = str(result) if not ok else "empty result"
            failures.append({
                "ticker": ticker,
                "error": error_str,
                "retryable": _is_retryable(result) if isinstance(result, Exception) else False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        pipeline_status["succeeded"] = succeeded
        pipeline_status["failed"] = len(failures)
        pipeline_status["processed"] = i + 1

        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(remaining) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - i - 1) / rate if rate > 0 else 0
            msg = f"Financials: {i+1}/{len(remaining)} done, {len(failures)} failed, {rate:.0f}/min, ETA {eta_min:.0f}min"
            pipeline_status["progress"] = msg
            log.info(msg)

    return failures


def _fetch_all_market_caps(tickers, db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    already_today = db.execute(
        "SELECT DISTINCT ticker FROM market_caps WHERE fetch_date = ?", (today,)
    ).fetchall()
    already_today = {r[0] for r in already_today}
    remaining = [t for t in tickers if t not in already_today]
    log.info("Market caps: %d total, %d already fetched today, %d remaining",
             len(tickers), len(already_today), len(remaining))

    if not remaining:
        return []

    t0 = time.time()
    caps = get_market_caps(remaining)

    for ticker, cap in caps.items():
        _save_market_cap(db, ticker, cap, today)

    failures = []
    for ticker in remaining:
        if ticker not in caps:
            failures.append({
                "ticker": ticker,
                "error": "no market cap returned",
                "retryable": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    elapsed = time.time() - t0
    log.info("Market caps: %d succeeded, %d failed in %.1fs",
             len(caps), len(failures), elapsed)
    return failures


def _fetch_all_prices(tickers, db):
    completed = _load_completed(db, "price_history")
    remaining = [t for t in tickers if t not in completed]
    log.info("Prices: %d total, %d already done, %d remaining",
             len(tickers), len(completed), len(remaining))

    pipeline_status["phase"] = "price history"
    pipeline_status["total"] = len(remaining)
    pipeline_status["processed"] = 0
    pipeline_status["succeeded"] = 0
    pipeline_status["failed"] = 0

    succeeded = 0
    failures = []
    t0 = time.time()

    for i, ticker in enumerate(remaining):
        if not _check_control():
            break

        try:
            rows = get_price_history(ticker)
            if rows:
                for r in rows:
                    db.execute(
                        "INSERT OR REPLACE INTO price_history (ticker, date, close_price, volume) VALUES (?, ?, ?, ?)",
                        (ticker, r["date"], r["close"], r["volume"]),
                    )
                db.commit()
                succeeded += 1
            else:
                failures.append({
                    "ticker": ticker,
                    "error": "no price data returned",
                    "retryable": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            failures.append({
                "ticker": ticker,
                "error": f"{type(e).__name__}: {e}",
                "retryable": _is_retryable(e) if isinstance(e, Exception) else False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        pipeline_status["succeeded"] = succeeded
        pipeline_status["failed"] = len(failures)
        pipeline_status["processed"] = i + 1

        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(remaining) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - i - 1) / rate if rate > 0 else 0
            msg = f"Prices: {i+1}/{len(remaining)} done, {len(failures)} failed, {rate:.0f}/min, ETA {eta_min:.0f}min"
            pipeline_status["progress"] = msg
            log.info(msg)

        time.sleep(0.5)

    return failures


def _save_failures(failures):
    with open(FAILURES_PATH, "w") as f:
        json.dump(failures, f, indent=2)
    log.info("Saved %d failures to %s", len(failures), FAILURES_PATH)


def _load_failures():
    try:
        with open(FAILURES_PATH) as f:
            data = json.load(f)
        tickers = [entry["ticker"] for entry in data]
        log.info("Loaded %d failed tickers from %s", len(tickers), FAILURES_PATH)
        return tickers
    except FileNotFoundError:
        log.warning("No %s found", FAILURES_PATH)
        return []


def run_pipeline(tickers=None, retry_failures=False, refresh_financials=False, fetch_prices=False):
    _reset_status()
    log.info("=" * 60)
    log.info("PIPELINE START")
    log.info("=" * 60)
    t0 = time.time()

    db = _init_db(DB_PATH)

    if retry_failures:
        ticker_list = _load_failures()
    elif tickers:
        ticker_list = [t.upper() for t in tickers]
    else:
        all_tickers = get_all_tickers()
        ticker_list = [t["ticker"] for t in all_tickers]

    log.info("Processing %d tickers", len(ticker_list))

    if not fetch_prices:
        fin_failures = _fetch_all_financials(ticker_list, db, refresh=refresh_financials)

        if not pipeline_status["cancel_requested"]:
            pipeline_status["phase"] = "market caps"
            cap_failures = _fetch_all_market_caps(ticker_list, db)
        else:
            cap_failures = []

        all_failures = fin_failures + cap_failures
    else:
        all_failures = []

    if fetch_prices and not pipeline_status["cancel_requested"]:
        price_failures = _fetch_all_prices(ticker_list, db)
        all_failures.extend(price_failures)

    if all_failures:
        retryable = [f for f in all_failures if f["retryable"]]
        permanent = [f for f in all_failures if not f["retryable"]]
        _save_failures(all_failures)
        log.warning("Failures: %d retryable, %d permanent", len(retryable), len(permanent))
    else:
        log.info("No failures")

    elapsed = time.time() - t0
    _finish_status(elapsed, all_failures)
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE in %.1fs (%.1f min)", elapsed, elapsed / 60)
    log.info("=" * 60)

    db.close()
    return all_failures


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--retry" in flags:
        run_pipeline(retry_failures=True, refresh_financials="--refresh-financials" in flags)
    elif "--fetch-prices" in flags:
        run_pipeline(tickers=args if args else None, fetch_prices=True)
    elif args:
        run_pipeline(tickers=args, refresh_financials="--refresh-financials" in flags)
    else:
        run_pipeline(refresh_financials="--refresh-financials" in flags)
