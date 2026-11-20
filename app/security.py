import hashlib
import secrets


def generate_virtual_key() -> str:
    return "rk_" + secrets.token_urlsafe(32)


def hash_virtual_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
