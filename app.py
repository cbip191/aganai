import sqlite3
import threading
from datetime import datetime, timezone

from flask import Flask, flash, jsonify, redirect, render_template, request

from config import DB_PATH
from data_fetcher import get_10k_financials, get_market_caps
from pipeline import _init_db, _save_financials, _save_market_cap, pipeline_status, run_pipeline, update_tickers

app = Flask(__name__)
app.secret_key = "aganai-dev-key"


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# --- Pages ---

@app.route("/")
def dashboard():
    db = get_db()
    total_tickers = db.execute("SELECT COUNT(DISTINCT ticker) FROM financials").fetchone()[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_market_caps = db.execute(
        "SELECT COUNT(DISTINCT ticker) FROM market_caps WHERE fetch_date = ?", (today,)
    ).fetchone()[0]
    total_failures = len(pipeline_status.get("failures", []))
    pipeline_state = "Running" if pipeline_status["running"] else "Idle"

    recent_caps = db.execute(
        "SELECT ticker, market_cap FROM market_caps WHERE fetch_date = ? ORDER BY market_cap DESC LIMIT 20",
        (today,),
    ).fetchall()
    db.close()

    return render_template("dashboard.html",
        total_tickers=total_tickers,
        total_market_caps=total_market_caps,
        total_failures=total_failures,
        pipeline_state=pipeline_state,
        recent_caps=recent_caps,
    )


@app.route("/pipeline")
def pipeline_page():
    return render_template("pipeline.html", status=pipeline_status)


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


@app.route("/companies")
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
               (SELECT COUNT(*) FROM financials WHERE ticker = c.ticker) as years
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


@app.route("/company/<ticker>")
def company_detail(ticker):
    ticker = ticker.upper()
    db = get_db()

    company = db.execute(
        "SELECT * FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()

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
            "SELECT fetch_date, market_cap FROM market_caps WHERE ticker = ? ORDER BY fetch_date",
            (ticker,),
        ).fetchall()
        cap_dates = [r["fetch_date"] for r in cap_history]
        cap_values = [round(r["market_cap"] / 1e9, 2) for r in cap_history]

    financials = db.execute(
        "SELECT * FROM financials WHERE ticker = ? ORDER BY year DESC", (ticker,)
    ).fetchall()
    db.close()

    return render_template("company_detail.html",
        ticker=ticker,
        company=company,
        has_price_history=len(hist_rows) > 0 if hist_rows else False,
        cap_dates=cap_dates,
        cap_values=cap_values,
        financials=financials,
    )


@app.route("/screener")
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
        SELECT f.ticker, c.name, c.sector, mc.market_cap, f.fcf, f.revenue, f.debt, f.cash
        FROM financials f
        LEFT JOIN companies c ON c.ticker = f.ticker
        LEFT JOIN market_caps mc ON mc.ticker = f.ticker AND mc.fetch_date = (
            SELECT MAX(fetch_date) FROM market_caps WHERE ticker = f.ticker
        )
        WHERE f.year = (SELECT MAX(year) FROM financials WHERE ticker = f.ticker)
    """
    params = []
    if filters["sector"]:
        query += " AND c.sector = ?"
        params.append(filters["sector"])
    if filters["min_fcf"] is not None:
        query += " AND f.fcf >= ?"
        params.append(filters["min_fcf"] * 1e9)
    if filters["max_debt"] is not None:
        query += " AND (f.debt IS NULL OR f.debt <= ?)"
        params.append(filters["max_debt"] * 1e9)
    if filters["min_revenue"] is not None:
        query += " AND f.revenue >= ?"
        params.append(filters["min_revenue"] * 1e9)
    if filters["min_cash"] is not None:
        query += " AND f.cash >= ?"
        params.append(filters["min_cash"] * 1e9)

    query += " ORDER BY f.fcf DESC"
    results = db.execute(query, params).fetchall()

    sectors = db.execute(
        "SELECT DISTINCT sector FROM companies WHERE sector != '' AND sector IS NOT NULL ORDER BY sector"
    ).fetchall()
    sectors = [r[0] for r in sectors]

    db.close()
    return render_template("screener.html", results=results, filters=filters, sectors=sectors)


# --- API endpoints ---

@app.route("/api/pipeline/status")
def api_pipeline_status():
    return jsonify(pipeline_status)


@app.route("/api/pipeline/pause", methods=["POST"])
def api_pipeline_pause():
    pipeline_status["paused"] = True
    return jsonify({"ok": True})


@app.route("/api/pipeline/resume", methods=["POST"])
def api_pipeline_resume():
    pipeline_status["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/pipeline/cancel", methods=["POST"])
def api_pipeline_cancel():
    pipeline_status["cancel_requested"] = True
    pipeline_status["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")

    tickers_str = request.form.get("tickers", "").strip()
    refresh = bool(request.form.get("refresh_financials"))
    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()] if tickers_str else None

    thread = threading.Thread(
        target=run_pipeline,
        kwargs={"tickers": tickers, "refresh_financials": refresh},
        daemon=True,
    )
    thread.start()
    flash("Pipeline started")
    return redirect("/pipeline")


@app.route("/api/pipeline/retry", methods=["POST"])
def api_pipeline_retry():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")

    thread = threading.Thread(
        target=run_pipeline,
        kwargs={"retry_failures": True},
        daemon=True,
    )
    thread.start()
    flash("Retrying failed tickers")
    return redirect("/pipeline")


@app.route("/api/company/<ticker>/refetch", methods=["POST"])
def api_refetch_ticker(ticker):
    ticker = ticker.upper()
    db = _init_db(DB_PATH)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    financials = get_10k_financials(ticker)
    if financials:
        _save_financials(db, ticker, financials)

    caps = get_market_caps([ticker])
    if ticker in caps:
        _save_market_cap(db, ticker, caps[ticker], today)

    db.close()
    flash(f"Refetched data for {ticker}")
    return redirect(f"/company/{ticker}")


@app.route("/api/company/<ticker>/update", methods=["POST"])
def api_update_ticker(ticker):
    ticker = ticker.upper()
    data = request.get_json()
    year = data.pop("year", None)
    if not year:
        return jsonify({"error": "year required"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT * FROM financials WHERE ticker = ? AND year = ?", (ticker, year)
    ).fetchone()

    if existing:
        updates = []
        params = []
        for field in ["revenue", "net_income", "operating_cf", "capex", "debt", "cash", "shares"]:
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])
        if "operating_cf" in data or "capex" in data:
            ocf = data.get("operating_cf", existing["operating_cf"])
            capex = data.get("capex", existing["capex"])
            if ocf is not None and capex is not None:
                updates.append("fcf = ?")
                params.append(ocf - capex)
        if updates:
            updates.append("fetched_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
            params.extend([ticker, year])
            db.execute(
                f"UPDATE financials SET {', '.join(updates)} WHERE ticker = ? AND year = ?",
                params,
            )
            db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/companies/bulk-refetch", methods=["POST"])
def api_bulk_refetch():
    if pipeline_status["running"]:
        return jsonify({"error": "Pipeline is already running"}), 409

    data = request.get_json()
    tickers = data.get("tickers", [])
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400

    thread = threading.Thread(
        target=run_pipeline,
        kwargs={"tickers": tickers, "refresh_financials": True},
        daemon=True,
    )
    thread.start()
    return jsonify({"message": f"Refetching {len(tickers)} tickers"})


@app.route("/api/pipeline/fetch-prices", methods=["POST"])
def api_fetch_prices():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")

    tickers_str = request.form.get("tickers", "").strip()
    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()] if tickers_str else None

    thread = threading.Thread(
        target=run_pipeline,
        kwargs={"tickers": tickers, "fetch_prices": True},
        daemon=True,
    )
    thread.start()
    flash("Fetching price history")
    return redirect("/pipeline")


@app.route("/api/tickers/update", methods=["POST"])
def api_update_tickers():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")

    thread = threading.Thread(target=update_tickers, daemon=True)
    thread.start()
    flash("Updating ticker list and sector data")
    return redirect("/pipeline")


@app.route("/api/companies/bulk-evaluate", methods=["POST"])
def api_bulk_evaluate():
    data = request.get_json()
    tickers = data.get("tickers", [])
    return jsonify({"message": f"DCF evaluation for {len(tickers)} tickers will be available once dcf_model.py is built"})


if __name__ == "__main__":
    _init_db(DB_PATH)
    app.run(debug=True, port=5001)
