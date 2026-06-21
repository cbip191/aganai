import logging
from datetime import datetime, timezone

log = logging.getLogger("investment_analysis")

MIN_YEARS = 7


def estimate_investment_lag(revenues, investments, max_lag=5):
    if len(revenues) < max_lag + 2 or len(investments) < max_lag + 2:
        return 2

    best_lag = 2
    best_corr = -999

    n = min(len(revenues), len(investments))

    for lag in range(0, max_lag + 1):
        rev_changes = []
        inv_values = []
        for i in range(lag + 1, n):
            if revenues[i] is not None and revenues[i - 1] is not None and investments[i - lag] is not None:
                rev_changes.append(revenues[i] - revenues[i - 1])
                inv_values.append(investments[i - lag])

        if len(rev_changes) < 3:
            continue

        mean_r = sum(rev_changes) / len(rev_changes)
        mean_i = sum(inv_values) / len(inv_values)

        cov = sum((r - mean_r) * (i - mean_i) for r, i in zip(rev_changes, inv_values))
        var_r = sum((r - mean_r) ** 2 for r in rev_changes)
        var_i = sum((i - mean_i) ** 2 for i in inv_values)

        if var_r > 0 and var_i > 0:
            corr = cov / (var_r ** 0.5 * var_i ** 0.5)
            if corr > best_corr:
                best_corr = corr
                best_lag = lag

    return best_lag


def calculate_roi_series(revenues, investments, lag):
    rois = []
    for i in range(lag + 1, min(len(revenues), len(investments))):
        if (revenues[i] is not None and revenues[i - 1] is not None
                and investments[i - lag] is not None and investments[i - lag] > 0):
            rev_change = revenues[i] - revenues[i - 1]
            roi = rev_change / investments[i - lag]
            rois.append(roi)
    return rois


def _determine_roi_trend(rois):
    if len(rois) < 4:
        return "stable"
    half = len(rois) // 2
    first_half = sum(rois[:half]) / half
    second_half = sum(rois[half:]) / (len(rois) - half)
    diff = second_half - first_half
    if abs(diff) < 0.05:
        return "stable"
    return "improving" if diff > 0 else "declining"


def analyze_company_investment(db, ticker):
    rows = db.execute("""
        SELECT year, revenue, total_investment FROM financials
        WHERE ticker = ? AND revenue IS NOT NULL
        ORDER BY year
    """, (ticker,)).fetchall()

    if len(rows) < MIN_YEARS:
        return None

    years = [r[0] for r in rows]
    revenues = [r[1] for r in rows]
    investments = [r[2] for r in rows]

    has_investment = any(v and v > 0 for v in investments)
    if not has_investment:
        return None

    lag = estimate_investment_lag(revenues, investments)
    rois = calculate_roi_series(revenues, investments, lag)

    if not rois:
        return None

    avg_roi = sum(rois) / len(rois)
    trend = _determine_roi_trend(rois)

    sector_row = db.execute("""
        SELECT sm.avg_roi FROM sector_metrics sm
        JOIN companies c ON c.sector = sm.sector
        WHERE c.ticker = ?
    """, (ticker,)).fetchone()
    sector_avg_roi = sector_row[0] if sector_row and sector_row[0] else avg_roi

    if sector_avg_roi and sector_avg_roi != 0:
        ratio = avg_roi / sector_avg_roi
        if ratio > 1.15:
            effectiveness = "above"
        elif ratio < 0.85:
            effectiveness = "below"
        else:
            effectiveness = "average"
    else:
        effectiveness = "average"

    return {
        "ticker": ticker,
        "avg_roi": avg_roi,
        "sector_avg_roi": sector_avg_roi,
        "investment_lag": lag,
        "effectiveness": effectiveness,
        "roi_trend": trend,
    }


def analyze_all_companies(db, tickers=None):
    if tickers is None:
        rows = db.execute("SELECT DISTINCT ticker FROM financials").fetchall()
        tickers = [r[0] for r in rows]

    now = datetime.now(timezone.utc).isoformat()
    results = {}
    analyzed = 0

    for ticker in tickers:
        result = analyze_company_investment(db, ticker)
        if result:
            results[ticker] = result
            analyzed += 1
            db.execute(
                """INSERT OR REPLACE INTO investment_metrics
                   (ticker, avg_roi, sector_avg_roi, investment_lag, effectiveness, roi_trend, calculated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ticker, result["avg_roi"], result["sector_avg_roi"],
                 result["investment_lag"], result["effectiveness"], result["roi_trend"], now),
            )

    db.commit()
    log.info("Investment analysis: %d/%d companies analyzed", analyzed, len(tickers))
    return results
