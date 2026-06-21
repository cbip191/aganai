import threading
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, request

from data.fetcher import get_10k_financials, get_market_caps
from data.pipeline import evaluate_valuations, pipeline_status, run_pipeline
from data.store import _save_financials, _save_market_cap
from db import get_db, init_db
from config import DB_PATH

bp = Blueprint("api", __name__)


@bp.route("/api/company/<ticker>/refetch", methods=["POST"])
def api_refetch_ticker(ticker):
    ticker = ticker.upper()
    db = init_db(DB_PATH)
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


@bp.route("/api/company/<ticker>/update", methods=["POST"])
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


@bp.route("/api/companies/bulk-refetch", methods=["POST"])
def api_bulk_refetch():
    if pipeline_status["running"]:
        return jsonify({"error": "Pipeline is already running"}), 409
    data = request.get_json()
    tickers = data.get("tickers", [])
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    thread = threading.Thread(target=run_pipeline, kwargs={"tickers": tickers, "refresh_financials": True}, daemon=True)
    thread.start()
    return jsonify({"message": f"Refetching {len(tickers)} tickers"})


@bp.route("/api/companies/bulk-evaluate", methods=["POST"])
def api_bulk_evaluate():
    if pipeline_status["running"]:
        return jsonify({"error": "Pipeline is already running"}), 409
    data = request.get_json()
    tickers = data.get("tickers", [])
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    thread = threading.Thread(target=evaluate_valuations, kwargs={"tickers": tickers}, daemon=True)
    thread.start()
    return jsonify({"message": f"Evaluating {len(tickers)} tickers"})
