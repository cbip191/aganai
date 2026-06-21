from flask import Flask

from db import init_db
from config import DB_PATH


def create_app():
    app = Flask(__name__, template_folder="templates")
    app.secret_key = "aganai-dev-key"

    init_db(DB_PATH)

    from web.dashboard import bp as dashboard_bp
    from web.pipeline_routes import bp as pipeline_bp
    from web.companies import bp as companies_bp
    from web.screener import bp as screener_bp
    from web.api import bp as api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(pipeline_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(screener_bp)
    app.register_blueprint(api_bp)

    return app
