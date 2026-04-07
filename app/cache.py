import threading
import time
from typing import Any

from flask import current_app

try:
    from flask_caching import Cache as FlaskCache
except Exception:  # pragma: no cover - optional dependency in some environments
    FlaskCache = None


class SimpleTTLCache:
    def __init__(self, default_timeout: int = 300):
        self.default_timeout = max(int(default_timeout or 0), 1)
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _expires_at(self, timeout: int | None) -> float:
        ttl = self.default_timeout if timeout is None else max(int(timeout or 0), 1)
        return self._now() + ttl

    def _purge_if_expired(self, key: str) -> None:
        record = self._store.get(key)
        if record is None:
            return
        expires_at, _value = record
        if expires_at <= self._now():
            self._store.pop(key, None)

    def get(self, key: str) -> Any:
        with self._lock:
            self._purge_if_expired(key)
            record = self._store.get(key)
            return None if record is None else record[1]

    def set(self, key: str, value: Any, timeout: int | None = None) -> bool:
        with self._lock:
            self._store[key] = (self._expires_at(timeout), value)
        return True

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._store
            self._store.pop(key, None)
        return existed

    def clear(self) -> bool:
        with self._lock:
            self._store.clear()
        return True


def init_cache(app) -> Any:
    cache_type = app.config.get("CACHE_TYPE") or "SimpleCache"
    default_timeout = int(app.config.get("CACHE_DEFAULT_TIMEOUT") or 300)
    cache_config = {
        "CACHE_TYPE": cache_type,
        "CACHE_DEFAULT_TIMEOUT": default_timeout,
        "CACHE_IGNORE_ERRORS": bool(app.config.get("CACHE_IGNORE_ERRORS", True)),
        "CACHE_KEY_PREFIX": app.config.get("CACHE_KEY_PREFIX") or "greek_chapters:",
        "CACHE_REDIS_URL": app.config.get("CACHE_REDIS_URL") or "",
    }

    cache = None
    backend_name = "simple-ttl"
    if FlaskCache is not None:
        try:
            cache = FlaskCache(config=cache_config)
            cache.init_app(app, config=cache_config)
            backend_name = clean_backend_name(cache_type)
        except Exception:
            app.logger.exception("Failed to initialize configured cache backend; falling back to in-memory cache.")
            cache = None
    if cache is None:
        cache = SimpleTTLCache(default_timeout=default_timeout)

    app.extensions["performance_cache"] = cache
    app.extensions["performance_cache_backend"] = backend_name
    return cache


def clean_backend_name(cache_type: str) -> str:
    raw = str(cache_type or "").strip()
    return raw or "SimpleCache"


def get_cache():
    return current_app.extensions.get("performance_cache")
