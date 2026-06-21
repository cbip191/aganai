import logging
from datetime import datetime, timezone

from config import DISCOUNT_RATE, PROJECTION_YEARS, TERMINAL_GROWTH_RATE
from analytics.dcf import calculate_intrinsic_value

log = logging.getLogger("valuation")


def evaluate_company(ticker, db):
    rows = db.execute(
        "SELECT * FROM financials WHERE ticker = ? ORDER BY year", (ticker,)
    ).fetchall()
    if not rows:
        return None

    financials_by_year = {}
    for r in rows:
        financials_by_year[r[1]] = {
            "operating_cf": r[2], "capex": r[3], "fcf": r[4],
            "revenue": r[5], "net_income": r[6], "debt": r[7],
            "cash": r[8], "shares": r[9], "r_and_d": r[10],
            "acquisitions": r[11], "total_investment": r[12],
        }

    sector_row = db.execute("""
        SELECT sm.* FROM sector_metrics sm
        JOIN companies c ON c.sector = sm.sector
        WHERE c.ticker = ?
    """, (ticker,)).fetchone()
    sector_growth = None
    if sector_row and sector_row[1] is not None:
        sector_growth = {"growth_rate": sector_row[1], "avg_roi": sector_row[2]}

    inv_row = db.execute(
        "SELECT * FROM investment_metrics WHERE ticker = ?", (ticker,)
    ).fetchone()
    company_analysis = None
    if inv_row:
        company_analysis = {
            "avg_roi": inv_row[1], "sector_avg_roi": inv_row[2],
            "investment_lag": inv_row[3], "effectiveness": inv_row[4],
            "roi_trend": inv_row[5],
        }

    result = calculate_intrinsic_value(
        financials_by_year, sector_growth, company_analysis,
        DISCOUNT_RATE, TERMINAL_GROWTH_RATE, PROJECTION_YEARS,
    )
    if result is None:
        return None

    cap_row = db.execute("""
        SELECT market_cap FROM market_caps WHERE ticker = ?
        ORDER BY fetch_date DESC LIMIT 1
    """, (ticker,)).fetchone()
    market_cap = cap_row[0] if cap_row else None

    margin = None
    if market_cap and result["intrinsic_value"] != 0:
        margin = (result["intrinsic_value"] - market_cap) / abs(result["intrinsic_value"])

    return {
        "ticker": ticker,
        "intrinsic_value": result["intrinsic_value"],
        "per_share_value": result["per_share_value"],
        "market_cap": market_cap,
        "margin_of_safety": margin,
        "growth_rate": result["growth_rate"],
        "model_used": result["model_used"],
        "years_of_data": result["years_of_data"],
    }


def evaluate_all(db, tickers=None):
    if tickers is None:
        rows = db.execute("SELECT DISTINCT ticker FROM financials").fetchall()
        tickers = [r[0] for r in rows]

    results = []
    for ticker in tickers:
        result = evaluate_company(ticker, db)
        if result:
            results.append(result)

    results.sort(key=lambda x: x.get("margin_of_safety") or -999, reverse=True)
    log.info("Evaluated %d/%d companies", len(results), len(tickers))
    return results


def save_valuations(db, valuations):
    now = datetime.now(timezone.utc).isoformat()
    for v in valuations:
        db.execute(
            """INSERT OR REPLACE INTO valuations
               (ticker, intrinsic_value, per_share_value, market_cap, margin_of_safety,
                growth_rate, model_used, discount_rate, calculated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (v["ticker"], v["intrinsic_value"], v["per_share_value"],
             v["market_cap"], v["margin_of_safety"], v["growth_rate"],
             v["model_used"], DISCOUNT_RATE, now),
        )
    db.commit()
    log.info("Saved %d valuations to DB", len(valuations))
