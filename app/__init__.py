from flask import Flask
import os

def create_app(config_class=None):
    from .config import Config

    app = Flask(__name__)
    if config_class is None:
        app.config.from_object(Config)
    else:
        app.config.from_object(config_class)
    if (
        not app.config.get("TESTING")
        and os.environ.get("FLASK_ENV", "").lower() == "production"
        and app.config.get("SECRET_KEY") == "dev-secret-change-me"
    ):
        raise RuntimeError("FLASK_SECRET_KEY must be set in production.")
    project_root = os.path.dirname(os.path.dirname(__file__))
    legacy_db_path = os.path.join(project_root, "greek_chapters.db")
    active_db_path = os.path.abspath(app.config.get("DB_PATH") or "")
    if os.path.exists(legacy_db_path) and os.path.abspath(legacy_db_path) != active_db_path:
        app.logger.warning(
            "Legacy DB detected at %s; app is using %s as canonical DB.",
            legacy_db_path,
            active_db_path,
        )

    # Register blueprints (to be implemented)
    from .routes import main, auth, api, chapters, vendors, institutions
    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(chapters.bp)
    app.register_blueprint(vendors.bp)
    app.register_blueprint(institutions.bp)

    from .database import close_connection
    app.teardown_appcontext(close_connection)

    return app
