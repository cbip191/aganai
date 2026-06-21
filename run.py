import sys


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--web" in flags or not flags and not args:
        from web import create_app
        app = create_app()
        app.run(debug=True, port=5001)
    elif "--evaluate" in flags:
        from data.pipeline import evaluate_valuations
        evaluate_valuations(tickers=args if args else None)
    elif "--fetch-prices" in flags:
        from data.pipeline import run_pipeline
        run_pipeline(tickers=args if args else None, fetch_prices=True)
    elif "--retry" in flags:
        from data.pipeline import run_pipeline
        run_pipeline(retry_failures=True, refresh_financials="--refresh-financials" in flags)
    elif "--refresh-financials" in flags:
        from data.pipeline import run_pipeline
        run_pipeline(tickers=args if args else None, refresh_financials=True)
    elif "--scan-status" in flags:
        from data.pipeline import scan_listing_status
        scan_listing_status()
    elif "--update-tickers" in flags:
        from data.pipeline import update_tickers
        update_tickers()
    elif args:
        from data.pipeline import run_pipeline
        run_pipeline(tickers=args)
    else:
        from web import create_app
        app = create_app()
        app.run(debug=True, port=5001)


if __name__ == "__main__":
    main()
