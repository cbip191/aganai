from datetime import datetime, timezone

from flask import Blueprint, render_template

from data.pipeline import pipeline_status
from db import get_db

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def dashboard():
    db = get_db()
    total_companies = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    with_financials = db.execute("SELECT COUNT(DISTINCT ticker) FROM financials").fetchone()[0]
    with_prices = db.execute("SELECT COUNT(DISTINCT ticker) FROM price_history").fetchone()[0]
    with_sector = db.execute("SELECT COUNT(*) FROM companies WHERE sector != '' AND sector IS NOT NULL").fetchone()[0]
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
        total_companies=total_companies,
        with_financials=with_financials,
        with_prices=with_prices,
        with_sector=with_sector,
        total_market_caps=total_market_caps,
        total_failures=total_failures,
        pipeline_state=pipeline_state,
        recent_caps=recent_caps,
    )
