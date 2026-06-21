import logging
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

from config import DB_PATH, MAX_RETRIES, PROGRESS_LOG_INTERVAL, SEC_RATE_LIMIT
from db import init_db
from data.fetcher import get_10k_financials, get_yahoo_financials, get_all_tickers, get_company_info, get_market_caps, get_price_history, check_listing_status
from data.store import _save_financials, _save_market_cap, _load_completed, _filter_active, _save_failures, _load_failures

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("pipeline")

RETRYABLE_STATUS_CODES = {429, 500, 502, 503}

# --- Job system ---

pipeline_jobs = {}
_jobs_lock = threading.Lock()

# Backward compat alias — points to the most recently created job
pipeline_status = {
    "running": False, "paused": False, "cancel_requested": False,
    "phase": "", "progress": "", "succeeded": 0, "failed": 0,
    "total": 0, "processed": 0, "failures": [],
}


def _create_job(job_type):
    job_id = str(uuid.uuid4())[:8]
    job = {
        "id": job_id,
        "type": job_type,
        "running": True,
        "paused": False,
        "cancel_requested": False,
        "phase": "starting",
        "progress": "",
        "succeeded": 0,
        "failed": 0,
        "total": 0,
        "processed": 0,
        "failures": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    with _jobs_lock:
        pipeline_jobs[job_id] = job
    # Update backward compat alias
    pipeline_status.update(job)
    return job_id, job


def _finish_job(job, elapsed, failures):
    job["running"] = False
    job["paused"] = False
    if job["cancel_requested"]:
        job["phase"] = "cancelled"
        job["progress"] = f"Cancelled after {elapsed:.1f}s"
    else:
        job["phase"] = "complete"
        job["progress"] = f"Done in {elapsed:.1f}s"
    job["cancel_requested"] = False
    job["failures"] = failures
    pipeline_status.update(job)


def clear_finished_jobs():
    with _jobs_lock:
        to_remove = [jid for jid, j in pipeline_jobs.items() if not j["running"]]
        for jid in to_remove:
            del pipeline_jobs[jid]
    return len(to_remove)


# --- Shared rate limiter ---

_sec_rate_lock = threading.Lock()
_sec_last_request = 0


def _sec_throttle():
    global _sec_last_request
    with _sec_rate_lock:
        now = time.time()
        wait = SEC_RATE_LIMIT - (now - _sec_last_request)
        if wait > 0:
            time.sleep(wait)
        _sec_last_request = time.time()


# --- Control helpers ---

def _check_control(job):
    while job["paused"] and not job["cancel_requested"]:
        time.sleep(0.5)
    return not job["cancel_requested"]


# --- Retry logic ---

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


# --- Pipeline functions ---

def update_tickers(tickers_to_update=None):
    job_id, job = _create_job("update_tickers")
    log.info("[%s] Updating ticker list...", job_id)
    job["phase"] = "updating tickers"
    job["progress"] = "Fetching SEC ticker list..."
    t0 = time.time()

    db = init_db(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()

    all_tickers = get_all_tickers()
    if tickers_to_update:
        tickers_to_update = {t.upper() for t in tickers_to_update}
        all_tickers = [t for t in all_tickers if t["ticker"] in tickers_to_update]

    job["total"] = len(all_tickers)
    batch_size = 100
    for i in range(0, len(all_tickers), batch_size):
        if not _check_control(job):
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
        job["processed"] = min(i + batch_size, len(all_tickers))
        job["progress"] = f"Saving tickers: {job['processed']}/{len(all_tickers)}"
    log.info("[%s] Saved %d tickers to DB", job_id, len(all_tickers))

    if not job["cancel_requested"]:
        needs_sector = db.execute(
            "SELECT ticker FROM companies WHERE sector = '' OR sector IS NULL"
        ).fetchall()
        needs_sector = [r[0] for r in needs_sector]
        if tickers_to_update:
            needs_sector = [t for t in needs_sector if t in tickers_to_update]

        if needs_sector:
            job["phase"] = "fetching sectors"
            job["total"] = len(needs_sector)
            job["processed"] = 0
            log.info("[%s] Fetching sector info for %d tickers...", job_id, len(needs_sector))

            chunk_size = 50
            for i in range(0, len(needs_sector), chunk_size):
                if not _check_control(job):
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
                job["processed"] = min(i + chunk_size, len(needs_sector))
                job["succeeded"] = job["processed"]
                job["progress"] = f"Sectors: {job['processed']}/{len(needs_sector)}"

    _finish_job(job, time.time() - t0, [])
    db.close()
    return job_id


def scan_listing_status(tickers_to_scan=None):
    job_id, job = _create_job("scan_status")
    log.info("[%s] Scanning listing status...", job_id)
    job["phase"] = "scanning status"
    t0 = time.time()

    db = init_db(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()

    if tickers_to_scan:
        to_scan = [t.upper() for t in tickers_to_scan]
    else:
        rows = db.execute("SELECT ticker FROM companies WHERE status = 'unknown' OR status IS NULL").fetchall()
        to_scan = [r[0] for r in rows]

    job["total"] = len(to_scan)
    active_count = 0
    delisted_count = 0

    for i, ticker in enumerate(to_scan):
        if not _check_control(job):
            break
        status = check_listing_status(ticker)
        db.execute("UPDATE companies SET status = ?, updated_at = ? WHERE ticker = ?", (status, now, ticker))
        if status == "active":
            active_count += 1
        elif status == "delisted":
            delisted_count += 1
        job["succeeded"] = active_count
        job["failed"] = delisted_count
        job["processed"] = i + 1
        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(to_scan) - 1:
            msg = f"Status scan: {i+1}/{len(to_scan)} — {active_count} active, {delisted_count} delisted"
            job["progress"] = msg
            log.info("[%s] %s", job_id, msg)
        if (i + 1) % 10 == 0:
            db.commit()

    db.commit()
    _finish_job(job, time.time() - t0, [])
    db.close()
    return job_id


def _fetch_all_financials(tickers, db, job):
    tickers = _filter_active(tickers, db)
    completed = _load_completed(db, "financials")
    remaining = [t for t in tickers if t not in completed]
    log.info("[%s] Financials: %d remaining", job["id"], len(remaining))

    succeeded = 0
    skipped = 0
    yahoo_count = 0
    failures = []
    t0 = time.time()
    job["phase"] = "financials"
    job["total"] = len(remaining)
    job["processed"] = 0

    for i, ticker in enumerate(remaining):
        if not _check_control(job):
            break
        _sec_throttle()
        ok, result = _retry(get_10k_financials, (ticker,))
        if ok and result:
            _save_financials(db, ticker, result)
            succeeded += 1
            db.execute("UPDATE companies SET status = 'active', updated_at = ? WHERE ticker = ? AND status != 'active'",
                       (datetime.now(timezone.utc).isoformat(), ticker))
        elif ok and not result:
            yahoo_result = get_yahoo_financials(ticker)
            if yahoo_result:
                _save_financials(db, ticker, yahoo_result)
                succeeded += 1
                yahoo_count += 1
                db.execute("UPDATE companies SET status = 'active', updated_at = ? WHERE ticker = ?",
                           (datetime.now(timezone.utc).isoformat(), ticker))
            else:
                skipped += 1
                db.execute("UPDATE companies SET status = 'no_data', updated_at = ? WHERE ticker = ?",
                           (datetime.now(timezone.utc).isoformat(), ticker))
            if (succeeded + skipped) % 50 == 0:
                db.commit()
        else:
            failures.append({"ticker": ticker, "error": str(result),
                             "retryable": _is_retryable(result) if isinstance(result, Exception) else False,
                             "timestamp": datetime.now(timezone.utc).isoformat()})
        job["succeeded"] = succeeded
        job["failed"] = len(failures)
        job["processed"] = i + 1
        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(remaining) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - i - 1) / rate if rate > 0 else 0
            msg = f"Financials: {i+1}/{len(remaining)} — {succeeded} saved ({yahoo_count} Yahoo), {skipped} no data, {len(failures)} failed, {rate:.0f}/min, ETA {eta_min:.0f}min"
            job["progress"] = msg
            log.info("[%s] %s", job["id"], msg)
    return failures


def _fetch_all_market_caps(tickers, db, job):
    tickers = _filter_active(tickers, db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    already_today = {r[0] for r in db.execute("SELECT DISTINCT ticker FROM market_caps WHERE fetch_date = ?", (today,)).fetchall()}
    remaining = [t for t in tickers if t not in already_today]
    if not remaining:
        return []
    t0 = time.time()
    caps = get_market_caps(remaining)
    for ticker, cap in caps.items():
        _save_market_cap(db, ticker, cap, today)
    now = datetime.now(timezone.utc).isoformat()
    failures = []
    for ticker in remaining:
        if ticker not in caps:
            db.execute("UPDATE companies SET status = 'delisted', updated_at = ? WHERE ticker = ? AND (status = 'unknown' OR status IS NULL)", (now, ticker))
            failures.append({"ticker": ticker, "error": "no market cap — marked delisted", "retryable": False, "timestamp": now})
    db.commit()
    log.info("[%s] Market caps: %d succeeded, %d failed in %.1fs", job["id"], len(caps), len(failures), time.time() - t0)
    return failures


def _fetch_all_prices(tickers, db, job):
    tickers = _filter_active(tickers, db)
    completed = _load_completed(db, "price_history")
    remaining = [t for t in tickers if t not in completed]
    job["phase"] = "price history"
    job["total"] = len(remaining)
    job["processed"] = 0
    job["succeeded"] = 0
    job["failed"] = 0
    succeeded = 0
    failures = []
    t0 = time.time()
    for i, ticker in enumerate(remaining):
        if not _check_control(job):
            break
        try:
            rows = get_price_history(ticker)
            if rows:
                for r in rows:
                    db.execute("INSERT OR REPLACE INTO price_history (ticker, date, close_price, volume) VALUES (?, ?, ?, ?)",
                               (ticker, r["date"], r["close"], r["volume"]))
                db.commit()
                succeeded += 1
            else:
                failures.append({"ticker": ticker, "error": "no price data", "retryable": False, "timestamp": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            failures.append({"ticker": ticker, "error": f"{type(e).__name__}: {e}", "retryable": _is_retryable(e), "timestamp": datetime.now(timezone.utc).isoformat()})
        job["succeeded"] = succeeded
        job["failed"] = len(failures)
        job["processed"] = i + 1
        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(remaining) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - i - 1) / rate if rate > 0 else 0
            job["progress"] = f"Prices: {i+1}/{len(remaining)} done, {len(failures)} failed, {rate:.0f}/min, ETA {eta_min:.0f}min"
        time.sleep(0.5)
    return failures


def fetch_all_data(tickers=None):
    job_id, job = _create_job("fetch_all")
    job["phase"] = "fetch all"
    log.info("[%s] FETCH ALL DATA — round-robin per company", job_id)
    t0 = time.time()

    db = init_db(DB_PATH)
    if tickers:
        ticker_list = [t.upper() for t in tickers]
    else:
        ticker_list = [r[0] for r in db.execute("SELECT ticker FROM companies").fetchall()]

    ticker_list = _filter_active(ticker_list, db)
    completed_fin = _load_completed(db, "financials")
    completed_prices = _load_completed(db, "price_history")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    already_caps = {r[0] for r in db.execute("SELECT DISTINCT ticker FROM market_caps WHERE fetch_date = ?", (today,)).fetchall()}

    remaining = [t for t in ticker_list if t not in completed_fin or t not in completed_prices or t not in already_caps]
    job["total"] = len(remaining)

    succeeded = 0
    skipped = 0
    yahoo_count = 0
    failures = []

    for i, ticker in enumerate(remaining):
        if not _check_control(job):
            break
        now_ts = datetime.now(timezone.utc).isoformat()
        ticker_ok = True

        if ticker not in completed_fin:
            _sec_throttle()
            ok, result = _retry(get_10k_financials, (ticker,))
            if ok and result:
                _save_financials(db, ticker, result)
                db.execute("UPDATE companies SET status = 'active', updated_at = ? WHERE ticker = ? AND status != 'active'", (now_ts, ticker))
            elif ok and not result:
                yf_result = get_yahoo_financials(ticker)
                if yf_result:
                    _save_financials(db, ticker, yf_result)
                    yahoo_count += 1
                    db.execute("UPDATE companies SET status = 'active', updated_at = ? WHERE ticker = ?", (now_ts, ticker))
                else:
                    db.execute("UPDATE companies SET status = 'no_data', updated_at = ? WHERE ticker = ?", (now_ts, ticker))
                    skipped += 1
                    ticker_ok = False
            else:
                failures.append({"ticker": ticker, "error": str(result), "retryable": _is_retryable(result) if isinstance(result, Exception) else False, "timestamp": now_ts})
                ticker_ok = False

        if ticker_ok and ticker not in already_caps:
            try:
                caps = get_market_caps([ticker])
                if ticker in caps:
                    _save_market_cap(db, ticker, caps[ticker], today)
            except Exception:
                pass

        if ticker_ok and ticker not in completed_prices:
            try:
                rows = get_price_history(ticker)
                if rows:
                    for r in rows:
                        db.execute("INSERT OR REPLACE INTO price_history (ticker, date, close_price, volume) VALUES (?, ?, ?, ?)",
                                   (ticker, r["date"], r["close"], r["volume"]))
                    db.commit()
            except Exception:
                pass
            time.sleep(0.3)

        if ticker_ok:
            succeeded += 1
        job["succeeded"] = succeeded
        job["failed"] = len(failures)
        job["processed"] = i + 1
        if (i + 1) % PROGRESS_LOG_INTERVAL == 0 or i == len(remaining) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - i - 1) / rate if rate > 0 else 0
            job["progress"] = f"All data: {i+1}/{len(remaining)} — {succeeded} complete ({yahoo_count} Yahoo), {skipped} no data, {len(failures)} failed, {rate:.0f}/min, ETA {eta_min:.0f}min"

    _finish_job(job, time.time() - t0, failures)
    if failures:
        _save_failures(failures)
    db.close()
    return job_id


def evaluate_valuations(tickers=None):
    from analytics.investment import analyze_all_companies
    from analytics.sector import calculate_all_sectors
    from analytics.valuation import evaluate_all, save_valuations

    job_id, job = _create_job("evaluate")
    job["phase"] = "evaluating"
    t0 = time.time()

    db = init_db(DB_PATH)
    if tickers:
        ticker_list = [t.upper() for t in tickers]
    else:
        ticker_list = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM financials").fetchall()]

    job["total"] = len(ticker_list)
    log.info("[%s] Evaluating %d companies...", job_id, len(ticker_list))

    job["progress"] = "Step 1/3: Analyzing sectors..."
    calculate_all_sectors(db)
    if job["cancel_requested"]:
        _finish_job(job, time.time() - t0, [])
        db.close()
        return job_id

    job["progress"] = "Step 2/3: Analyzing investments..."
    analyze_all_companies(db, ticker_list)
    if job["cancel_requested"]:
        _finish_job(job, time.time() - t0, [])
        db.close()
        return job_id

    job["progress"] = "Step 3/3: Computing DCF valuations..."
    results = evaluate_all(db, ticker_list)
    save_valuations(db, results)

    simple = sum(1 for r in results if r["model_used"] == "simple")
    adjusted = sum(1 for r in results if r["model_used"] == "investment-adjusted")
    job["succeeded"] = len(results)
    job["processed"] = len(ticker_list)

    _finish_job(job, time.time() - t0, [])
    job["progress"] = f"Evaluated {len(results)} ({simple} simple, {adjusted} inv-adjusted) in {time.time() - t0:.1f}s"
    db.close()
    return job_id


def run_pipeline(tickers=None, retry_failures=False, refresh_financials=False, fetch_prices=False):
    job_id, job = _create_job("pipeline")
    log.info("[%s] PIPELINE START", job_id)
    t0 = time.time()

    db = init_db(DB_PATH)
    if retry_failures:
        ticker_list = _load_failures()
    elif tickers:
        ticker_list = [t.upper() for t in tickers]
    else:
        all_tickers = get_all_tickers()
        ticker_list = [t["ticker"] for t in all_tickers]

    log.info("[%s] Processing %d tickers", job_id, len(ticker_list))

    if not fetch_prices:
        fin_failures = _fetch_all_financials(ticker_list, db, job)
        if not job["cancel_requested"]:
            job["phase"] = "market caps"
            cap_failures = _fetch_all_market_caps(ticker_list, db, job)
        else:
            cap_failures = []
        all_failures = fin_failures + cap_failures
    else:
        all_failures = []

    if fetch_prices and not job["cancel_requested"]:
        price_failures = _fetch_all_prices(ticker_list, db, job)
        all_failures.extend(price_failures)

    if all_failures:
        _save_failures(all_failures)

    _finish_job(job, time.time() - t0, all_failures)
    db.close()
    return job_id


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
