import logging
from datetime import datetime, timezone

log = logging.getLogger("sector_analysis")

MIN_YEARS = 7
MIN_COMPANIES = 3


def calculate_sector_growth(db, sector):
    rows = db.execute("""
        SELECT f.ticker, f.year, f.revenue, f.total_investment
        FROM financials f
        JOIN companies c ON c.ticker = f.ticker
        WHERE c.sector = ? AND f.revenue IS NOT NULL
        ORDER BY f.ticker, f.year
    """, (sector,)).fetchall()

    companies = {}
    for r in rows:
        ticker = r[0]
        if ticker not in companies:
            companies[ticker] = []
        companies[ticker].append({
            "year": r[1], "revenue": r[2], "investment": r[3] or 0
        })

    weighted_growths = []
    total_weight = 0

    for ticker, years_data in companies.items():
        if len(years_data) < MIN_YEARS:
            continue
        years_data.sort(key=lambda x: x["year"])

        growths = []
        for i in range(1, len(years_data)):
            prev_rev = years_data[i - 1]["revenue"]
            curr_rev = years_data[i]["revenue"]
            if prev_rev and prev_rev > 0 and curr_rev:
                growths.append(curr_rev / prev_rev - 1)

        if not growths:
            continue

        avg_growth = sum(growths) / len(growths)
        avg_inv = sum(d["investment"] for d in years_data) / len(years_data)
        weight = max(avg_inv, 1)

        weighted_growths.append(avg_growth * weight)
        total_weight += weight

    if total_weight == 0 or len(weighted_growths) < MIN_COMPANIES:
        return {"sector": sector, "growth_rate": None, "num_companies": 0, "years_analyzed": 0}

    growth_rate = sum(weighted_growths) / total_weight
    growth_rate = max(-0.10, min(0.25, growth_rate))

    return {
        "sector": sector,
        "growth_rate": growth_rate,
        "num_companies": len(weighted_growths),
        "years_analyzed": MIN_YEARS,
    }


def calculate_all_sectors(db):
    sectors = db.execute(
        "SELECT DISTINCT sector FROM companies WHERE sector != '' AND sector IS NOT NULL"
    ).fetchall()
    sectors = [r[0] for r in sectors]

    now = datetime.now(timezone.utc).isoformat()
    results = {}

    for sector in sectors:
        result = calculate_sector_growth(db, sector)
        results[sector] = result

        if result["growth_rate"] is not None:
            avg_roi = _calculate_sector_avg_roi(db, sector)
            db.execute(
                """INSERT OR REPLACE INTO sector_metrics
                   (sector, growth_rate, avg_roi, num_companies, calculated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sector, result["growth_rate"], avg_roi, result["num_companies"], now),
            )
    db.commit()

    valid = sum(1 for r in results.values() if r["growth_rate"] is not None)
    log.info("Sector analysis: %d/%d sectors have sufficient data", valid, len(sectors))
    return results


def _calculate_sector_avg_roi(db, sector):
    rows = db.execute("""
        SELECT f.ticker, f.year, f.revenue, f.total_investment
        FROM financials f
        JOIN companies c ON c.ticker = f.ticker
        WHERE c.sector = ? AND f.revenue IS NOT NULL AND f.total_investment IS NOT NULL
              AND f.total_investment > 0
        ORDER BY f.ticker, f.year
    """, (sector,)).fetchall()

    companies = {}
    for r in rows:
        ticker = r[0]
        if ticker not in companies:
            companies[ticker] = []
        companies[ticker].append({"year": r[1], "revenue": r[2], "investment": r[3]})

    all_rois = []
    for ticker, data in companies.items():
        if len(data) < 4:
            continue
        data.sort(key=lambda x: x["year"])
        for i in range(2, len(data)):
            rev_change = data[i]["revenue"] - data[i - 1]["revenue"]
            inv = data[i - 2]["investment"]
            if inv > 0:
                all_rois.append(rev_change / inv)

    if not all_rois:
        return None
    return sum(all_rois) / len(all_rois)
