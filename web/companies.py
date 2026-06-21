from flask import Blueprint, render_template, request

from db import get_db

bp = Blueprint("companies", __name__)

SORT_COLUMNS = {
    "ticker": "c.ticker",
    "name": "c.name",
    "sector": "c.sector",
    "market_cap": "mc.market_cap",
    "fcf": "f.fcf",
    "revenue": "f.revenue",
    "debt": "f.debt",
    "cash": "f.cash",
    "years": "years",
}


@bp.route("/companies")
def companies_page():
    db = get_db()
    selected_sectors = request.args.getlist("sector")
    page = request.args.get("page", 1, type=int)
    sort_by = request.args.get("sort", "market_cap")
    sort_dir = request.args.get("dir", "desc")
    per_page = 100

    if sort_by not in SORT_COLUMNS:
        sort_by = "market_cap"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    query = """
        SELECT c.ticker, c.name, c.sector, c.industry,
               mc.market_cap,
               f.fcf, f.revenue, f.debt, f.cash,
               (SELECT COUNT(*) FROM financials WHERE ticker = c.ticker) as years,
               f.data_source
        FROM companies c
        LEFT JOIN financials f ON f.ticker = c.ticker
            AND f.year = (SELECT MAX(year) FROM financials WHERE ticker = c.ticker)
        LEFT JOIN market_caps mc ON mc.ticker = c.ticker AND mc.fetch_date = (
            SELECT MAX(fetch_date) FROM market_caps WHERE ticker = c.ticker
        )
        WHERE 1=1
    """
    params = []
    if selected_sectors:
        placeholders = ",".join("?" for _ in selected_sectors)
        query += f" AND c.sector IN ({placeholders})"
        params.extend(selected_sectors)

    count_query = "SELECT COUNT(*) FROM companies c WHERE 1=1"
    count_params = []
    if selected_sectors:
        placeholders = ",".join("?" for _ in selected_sectors)
        count_query += f" AND c.sector IN ({placeholders})"
        count_params.extend(selected_sectors)
    total_count = db.execute(count_query, count_params).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    sort_col = SORT_COLUMNS[sort_by]
    query += f" ORDER BY {sort_col} {'ASC' if sort_dir == 'asc' else 'DESC'} NULLS LAST LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    rows = db.execute(query, params).fetchall()

    sectors = db.execute(
        "SELECT DISTINCT sector FROM companies WHERE sector != '' AND sector IS NOT NULL ORDER BY sector"
    ).fetchall()
    sectors = [r[0] for r in sectors]

    db.close()
    return render_template("companies.html", companies=rows, sectors=sectors,
                           selected_sectors=selected_sectors, page=page,
                           total_pages=total_pages, total_count=total_count,
                           sort_by=sort_by, sort_dir=sort_dir)


@bp.route("/company/<ticker>")
def company_detail(ticker):
    ticker = ticker.upper()
    db = get_db()

    company = db.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,)).fetchone()

    hist_rows = db.execute("""
        SELECT ph.date, ph.close_price,
               f.shares,
               ph.close_price * f.shares as market_cap
        FROM price_history ph
        LEFT JOIN financials f ON f.ticker = ph.ticker
            AND f.year = (SELECT MAX(year) FROM financials
                          WHERE ticker = ph.ticker AND year <= CAST(SUBSTR(ph.date,1,4) AS INTEGER))
        WHERE ph.ticker = ?
        ORDER BY ph.date
    """, (ticker,)).fetchall()

    if hist_rows:
        cap_dates = [r["date"] for r in hist_rows if r["market_cap"]]
        cap_values = [round(r["market_cap"] / 1e9, 2) for r in hist_rows if r["market_cap"]]
    else:
        cap_history = db.execute(
            "SELECT fetch_date, market_cap FROM market_caps WHERE ticker = ? ORDER BY fetch_date", (ticker,)
        ).fetchall()
        cap_dates = [r["fetch_date"] for r in cap_history]
        cap_values = [round(r["market_cap"] / 1e9, 2) for r in cap_history]

    financials = db.execute("SELECT * FROM financials WHERE ticker = ? ORDER BY year DESC", (ticker,)).fetchall()
    valuation = db.execute("SELECT * FROM valuations WHERE ticker = ?", (ticker,)).fetchone()
    inv_metrics = db.execute("SELECT * FROM investment_metrics WHERE ticker = ?", (ticker,)).fetchone()
    db.close()

    return render_template("company_detail.html",
        ticker=ticker, company=company,
        has_price_history=len(hist_rows) > 0 if hist_rows else False,
        cap_dates=cap_dates, cap_values=cap_values,
        financials=financials, valuation=valuation, inv_metrics=inv_metrics,
    )
