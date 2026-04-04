from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_PASSWORD_HASH_METHOD = "pbkdf2:sha256:150000"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=DEFAULT_PASSWORD_HASH_METHOD)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def password_hash_needs_refresh(password_hash: str) -> bool:
    return bool(password_hash) and not str(password_hash).startswith(f"{DEFAULT_PASSWORD_HASH_METHOD}$")
