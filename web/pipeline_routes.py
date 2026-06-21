import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request

from data.pipeline import evaluate_valuations, pipeline_status, run_pipeline, scan_listing_status, update_tickers
from db import get_db

bp = Blueprint("pipeline", __name__)


@bp.route("/pipeline")
def pipeline_page():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM companies WHERE status = 'active'").fetchone()[0]
    delisted = db.execute("SELECT COUNT(*) FROM companies WHERE status = 'delisted'").fetchone()[0]
    unknown = db.execute("SELECT COUNT(*) FROM companies WHERE status = 'unknown' OR status IS NULL").fetchone()[0]
    coverage = {
        "total": total,
        "active": active,
        "delisted": delisted,
        "unknown": unknown,
        "financials": db.execute("SELECT COUNT(DISTINCT ticker) FROM financials").fetchone()[0],
        "market_caps": db.execute("SELECT COUNT(DISTINCT ticker) FROM market_caps").fetchone()[0],
        "prices": db.execute("SELECT COUNT(DISTINCT ticker) FROM price_history").fetchone()[0],
        "sectors": db.execute("SELECT COUNT(*) FROM companies WHERE sector != '' AND sector IS NOT NULL").fetchone()[0],
    }
    sectors = db.execute(
        "SELECT DISTINCT sector FROM companies WHERE sector != '' AND sector IS NOT NULL ORDER BY sector"
    ).fetchall()
    sectors = [r[0] for r in sectors]
    db.close()
    return render_template("pipeline.html", status=pipeline_status, coverage=coverage, sectors=sectors)


@bp.route("/api/pipeline/status")
def api_pipeline_status():
    return jsonify(pipeline_status)


@bp.route("/api/pipeline/pause", methods=["POST"])
def api_pipeline_pause():
    pipeline_status["paused"] = True
    return jsonify({"ok": True})


@bp.route("/api/pipeline/resume", methods=["POST"])
def api_pipeline_resume():
    pipeline_status["paused"] = False
    return jsonify({"ok": True})


@bp.route("/api/pipeline/cancel", methods=["POST"])
def api_pipeline_cancel():
    pipeline_status["cancel_requested"] = True
    pipeline_status["paused"] = False
    return jsonify({"ok": True})


@bp.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    tickers_str = request.form.get("tickers", "").strip()
    refresh = bool(request.form.get("refresh_financials"))
    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()] if tickers_str else None
    thread = threading.Thread(target=run_pipeline, kwargs={"tickers": tickers, "refresh_financials": refresh}, daemon=True)
    thread.start()
    flash("Pipeline started")
    return redirect("/pipeline")


@bp.route("/api/pipeline/retry", methods=["POST"])
def api_pipeline_retry():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    thread = threading.Thread(target=run_pipeline, kwargs={"retry_failures": True}, daemon=True)
    thread.start()
    flash("Retrying failed tickers")
    return redirect("/pipeline")


@bp.route("/api/pipeline/fetch-prices", methods=["POST"])
def api_fetch_prices():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    tickers_str = request.form.get("tickers", "").strip()
    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()] if tickers_str else None
    thread = threading.Thread(target=run_pipeline, kwargs={"tickers": tickers, "fetch_prices": True}, daemon=True)
    thread.start()
    flash("Fetching price history")
    return redirect("/pipeline")


@bp.route("/api/pipeline/evaluate", methods=["POST"])
def api_evaluate():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    thread = threading.Thread(target=evaluate_valuations, daemon=True)
    thread.start()
    flash("Running DCF evaluation for all companies with financials")
    return redirect("/pipeline")


@bp.route("/api/pipeline/fetch-missing", methods=["POST"])
def api_fetch_missing():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    data_type = request.form.get("data_type", "financials")
    db = get_db()
    if data_type == "financials":
        existing = {r[0] for r in db.execute("SELECT DISTINCT ticker FROM financials").fetchall()}
    elif data_type == "prices":
        existing = {r[0] for r in db.execute("SELECT DISTINCT ticker FROM price_history").fetchall()}
    elif data_type == "sectors":
        existing = {r[0] for r in db.execute("SELECT ticker FROM companies WHERE sector != '' AND sector IS NOT NULL").fetchall()}
    else:
        existing = set()
    all_tickers = [r[0] for r in db.execute("SELECT ticker FROM companies WHERE status != 'delisted' OR status IS NULL").fetchall()]
    missing = [t for t in all_tickers if t not in existing]
    db.close()
    if not missing:
        flash(f"No missing {data_type} data")
        return redirect("/pipeline")
    if data_type == "sectors":
        thread = threading.Thread(target=update_tickers, kwargs={"tickers_to_update": missing}, daemon=True)
    elif data_type == "prices":
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": missing, "fetch_prices": True}, daemon=True)
    else:
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": missing}, daemon=True)
    thread.start()
    flash(f"Fetching {data_type} for {len(missing)} companies missing data")
    return redirect("/pipeline")


@bp.route("/api/pipeline/fetch-by-sector", methods=["POST"])
def api_fetch_by_sector():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    sectors = request.form.getlist("sectors")
    data_type = request.form.get("data_type", "financials")
    if not sectors:
        flash("Select at least one sector")
        return redirect("/pipeline")
    db = get_db()
    placeholders = ",".join("?" for _ in sectors)
    tickers = [r[0] for r in db.execute(f"SELECT ticker FROM companies WHERE sector IN ({placeholders})", sectors).fetchall()]
    db.close()
    if not tickers:
        flash("No companies in selected sectors")
        return redirect("/pipeline")
    if data_type == "prices":
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": tickers, "fetch_prices": True}, daemon=True)
    else:
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": tickers, "refresh_financials": data_type == "refresh"}, daemon=True)
    thread.start()
    flash(f"Fetching {data_type} for {len(tickers)} companies in {', '.join(sectors)}")
    return redirect("/pipeline")


@bp.route("/api/pipeline/refresh-stale", methods=["POST"])
def api_refresh_stale():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    days = request.form.get("days", 30, type=int)
    data_type = request.form.get("data_type", "financials")
    db = get_db()
    cutoff = datetime.now(timezone.utc)
    cutoff_str = (cutoff - timedelta(days=days)).isoformat()
    if data_type == "financials":
        stale = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM financials WHERE fetched_at < ? OR fetched_at IS NULL", (cutoff_str,)).fetchall()]
    elif data_type == "prices":
        cutoff_date = (cutoff - timedelta(days=days)).strftime("%Y-%m-%d")
        stale = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM price_history GROUP BY ticker HAVING MAX(date) < ?", (cutoff_date,)).fetchall()]
    else:
        stale = []
    db.close()
    if not stale:
        flash(f"No stale {data_type} data (older than {days} days)")
        return redirect("/pipeline")
    if data_type == "prices":
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": stale, "fetch_prices": True}, daemon=True)
    else:
        thread = threading.Thread(target=run_pipeline, kwargs={"tickers": stale, "refresh_financials": True}, daemon=True)
    thread.start()
    flash(f"Refreshing {data_type} for {len(stale)} companies with data older than {days} days")
    return redirect("/pipeline")


@bp.route("/api/tickers/update", methods=["POST"])
def api_update_tickers():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    thread = threading.Thread(target=update_tickers, daemon=True)
    thread.start()
    flash("Updating ticker list and sector data")
    return redirect("/pipeline")


@bp.route("/api/pipeline/scan-status", methods=["POST"])
def api_scan_status():
    if pipeline_status["running"]:
        flash("Pipeline is already running")
        return redirect("/pipeline")
    thread = threading.Thread(target=scan_listing_status, daemon=True)
    thread.start()
    flash("Scanning listing status for unknown tickers")
    return redirect("/pipeline")
