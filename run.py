from app import create_app
import os

app = create_app()

if __name__ == "__main__":
    debug_flag = os.environ.get("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", "5000"))
    app.run(host=host, port=port, debug=debug_flag)
