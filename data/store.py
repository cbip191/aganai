import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("data.store")

FAILURES_PATH = "failures.json"


def _save_financials(db, ticker, data):
    now = datetime.now(timezone.utc).isoformat()
    for year, row in data.items():
        db.execute(
            """INSERT OR REPLACE INTO financials
               (ticker, year, operating_cf, capex, fcf, revenue, net_income, debt, cash, shares,
                r_and_d, acquisitions, total_investment, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, year, row.get("operating_cf"), row.get("capex"),
                row.get("fcf"), row.get("revenue"), row.get("net_income"),
                row.get("debt"), row.get("cash"), row.get("shares"),
                row.get("r_and_d"), row.get("acquisitions"), row.get("total_investment"), now,
            ),
        )
    db.commit()


def _save_market_cap(db, ticker, cap, fetch_date):
    db.execute(
        "INSERT OR REPLACE INTO market_caps (ticker, market_cap, fetch_date) VALUES (?, ?, ?)",
        (ticker, cap, fetch_date),
    )
    db.commit()


def _load_completed(db, table):
    rows = db.execute(f"SELECT DISTINCT ticker FROM {table}").fetchall()
    return {r[0] for r in rows}


def _load_delisted(db):
    rows = db.execute("SELECT ticker FROM companies WHERE status = 'delisted'").fetchall()
    return {r[0] for r in rows}


def _filter_active(tickers, db):
    delisted = _load_delisted(db)
    active = [t for t in tickers if t not in delisted]
    if len(tickers) != len(active):
        log.info("Skipping %d delisted tickers", len(tickers) - len(active))
    return active


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
