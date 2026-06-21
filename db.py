import sqlite3

from config import DB_PATH


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db(path=None):
    db = sqlite3.connect(path or DB_PATH)

    db.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            cik TEXT,
            sector TEXT,
            industry TEXT,
            status TEXT DEFAULT 'unknown',
            updated_at TEXT
        )
    """)
    try:
        db.execute("ALTER TABLE companies ADD COLUMN status TEXT DEFAULT 'unknown'")
        db.commit()
    except sqlite3.OperationalError:
        pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS market_caps (
            ticker TEXT,
            market_cap REAL,
            fetch_date TEXT,
            PRIMARY KEY (ticker, fetch_date)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS financials (
            ticker TEXT,
            year INTEGER,
            operating_cf REAL,
            capex REAL,
            fcf REAL,
            revenue REAL,
            net_income REAL,
            debt REAL,
            cash REAL,
            shares REAL,
            r_and_d REAL,
            acquisitions REAL,
            total_investment REAL,
            fetched_at TEXT,
            PRIMARY KEY (ticker, year)
        )
    """)
    for col in ["r_and_d", "acquisitions", "total_investment"]:
        try:
            db.execute(f"ALTER TABLE financials ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            ticker TEXT,
            date TEXT,
            close_price REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS sector_metrics (
            sector TEXT PRIMARY KEY,
            growth_rate REAL,
            avg_roi REAL,
            num_companies INTEGER,
            calculated_at TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS investment_metrics (
            ticker TEXT PRIMARY KEY,
            avg_roi REAL,
            sector_avg_roi REAL,
            investment_lag INTEGER,
            effectiveness TEXT,
            roi_trend TEXT,
            calculated_at TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS valuations (
            ticker TEXT PRIMARY KEY,
            intrinsic_value REAL,
            per_share_value REAL,
            market_cap REAL,
            margin_of_safety REAL,
            growth_rate REAL,
            model_used TEXT,
            discount_rate REAL,
            calculated_at TEXT
        )
    """)

    db.commit()
    return db
