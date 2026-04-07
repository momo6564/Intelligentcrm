from flask import Flask
import os

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API_BASE_URL = "https://openidconnect.googleapis.com/v1/"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"


def _warm_reference_runtime(app) -> None:
    if not app.config.get("EAGER_REFERENCE_BOOTSTRAP"):
        return

    try:
        from .database import (
            get_connection,
            ensure_crm_tables,
            ensure_vendor_table,
            ensure_institutions_table,
            ensure_chapters_table,
        )
        from .services.chapters import fetch_normalized_rows

        with app.app_context():
            conn = get_connection()
            ensure_crm_tables(conn)
            ensure_vendor_table(conn)
            if app.config.get("WARM_INSTITUTIONS_CACHE"):
                ensure_institutions_table(conn)
            ensure_chapters_table(conn, bootstrap_related=False)
            if app.config.get("WARM_CHAPTER_REFERENCE_CACHE"):
                fetch_normalized_rows(force_refresh=True)
        app.logger.info(
            "Reference data warmed with %s cache backend.",
            app.extensions.get("performance_cache_backend") or "unknown",
        )
    except Exception:
        app.logger.exception("Reference data warm-up failed during app startup.")


def create_app(config_class=None):
    from .cache import init_cache
    from .config import resolve_config_class, sync_legacy_config

    app = Flask(__name__)
    if config_class is None:
        config_class = resolve_config_class()
    sync_legacy_config(config_class)
    app.config.from_object(config_class)
    if (
        not app.config.get("TESTING")
        and os.environ.get("FLASK_ENV", "").lower() == "production"
        and app.config.get("SECRET_KEY") == "dev-secret-change-me"
    ):
        raise RuntimeError("FLASK_SECRET_KEY must be set in production.")
    if app.config.get("SESSION_COOKIE_SECURE"):
        redirect_hint = (app.config.get("GOOGLE_REDIRECT_URI") or "").lower()
        if redirect_hint.startswith("http://"):
            app.config["SESSION_COOKIE_SECURE"] = False
    project_root = os.path.dirname(os.path.dirname(__file__))
    legacy_db_path = os.path.join(project_root, "greek_chapters.db")
    active_db_path = os.path.abspath(app.config.get("DB_PATH") or "")
    if os.path.exists(legacy_db_path) and os.path.abspath(legacy_db_path) != active_db_path:
        app.logger.warning(
            "Legacy DB detected at %s; app is using %s as canonical DB.",
            legacy_db_path,
            active_db_path,
        )

    init_cache(app)

    # Register blueprints (to be implemented)
    from .routes import main, auth, api, chapters, vendors, institutions, team, admin, ops, brand
    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(chapters.bp)
    app.register_blueprint(vendors.bp)
    app.register_blueprint(institutions.bp)
    app.register_blueprint(team.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(ops.bp)
    app.register_blueprint(brand.bp)

    # Initialize OAuth (Google)
    try:
        from authlib.integrations.flask_client import OAuth
    except Exception:
        OAuth = None

    google_client_id = app.config.get("GOOGLE_CLIENT_ID") or ""
    google_client_secret = app.config.get("GOOGLE_CLIENT_SECRET") or ""
    if OAuth and google_client_id and google_client_secret:
        oauth = OAuth(app)
        google = oauth.register(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            authorize_url=GOOGLE_AUTHORIZE_URL,
            access_token_url=GOOGLE_TOKEN_URL,
            api_base_url=GOOGLE_API_BASE_URL,
            userinfo_endpoint=GOOGLE_USERINFO_URL,
            jwks_uri=GOOGLE_JWKS_URI,
            client_kwargs={"scope": "openid email profile"},
        )
        app.google_oauth = google
    else:
        app.google_oauth = None

    from .database import close_connection
    app.teardown_appcontext(close_connection)

    _warm_reference_runtime(app)

    return app
