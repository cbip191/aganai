from flask import Blueprint, render_template, request

from db import get_db

bp = Blueprint("screener", __name__)


@bp.route("/screener")
def screener_page():
    db = get_db()
    filters = {
        "min_fcf": request.args.get("min_fcf", type=float),
        "max_debt": request.args.get("max_debt", type=float),
        "min_revenue": request.args.get("min_revenue", type=float),
        "min_cash": request.args.get("min_cash", type=float),
        "sector": request.args.get("sector", ""),
    }

    query = """
        SELECT f.ticker, c.name, c.sector, mc.market_cap, f.fcf, f.revenue, f.debt, f.cash,
               v.intrinsic_value, v.per_share_value, v.margin_of_safety, v.model_used
        FROM financials f
        LEFT JOIN companies c ON c.ticker = f.ticker
        LEFT JOIN market_caps mc ON mc.ticker = f.ticker AND mc.fetch_date = (
            SELECT MAX(fetch_date) FROM market_caps WHERE ticker = f.ticker
        )
        LEFT JOIN valuations v ON v.ticker = f.ticker
        WHERE f.year = (SELECT MAX(year) FROM financials WHERE ticker = f.ticker)
    """
    params = []
    if filters["sector"]:
        query += " AND c.sector = ?"
        params.append(filters["sector"])
    if filters["min_fcf"] is not None:
        query += " AND f.fcf >= ?"
        params.append(filters["min_fcf"] * 1e6)
    if filters["max_debt"] is not None:
        query += " AND (f.debt IS NULL OR f.debt <= ?)"
        params.append(filters["max_debt"] * 1e6)
    if filters["min_revenue"] is not None:
        query += " AND f.revenue >= ?"
        params.append(filters["min_revenue"] * 1e6)
    if filters["min_cash"] is not None:
        query += " AND f.cash >= ?"
        params.append(filters["min_cash"] * 1e6)

    count_query = "SELECT COUNT(*) FROM (" + query + ")"
    total_count = db.execute(count_query, params).fetchone()[0]

    limit = request.args.get("limit", 100, type=int)
    query += " ORDER BY f.fcf DESC LIMIT ?"
    params.append(limit)
    results = db.execute(query, params).fetchall()

    sectors = db.execute(
        "SELECT DISTINCT sector FROM companies WHERE sector != '' AND sector IS NOT NULL ORDER BY sector"
    ).fetchall()
    sectors = [r[0] for r in sectors]

    db.close()
    return render_template("screener.html", results=results, filters=filters, sectors=sectors,
                           total_count=total_count, current_limit=limit)
