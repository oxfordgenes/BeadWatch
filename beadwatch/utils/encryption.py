from cryptography.fernet import Fernet
from pathlib import Path
from config.settings import ENCRYPTION_KEY_PATH


def _ensure_key_exists() -> bytes:
    """Generate encryption key on first run, or load existing key.

    Security note (accepted risk): On Windows the key file inherits the
    parent directory's ACL and is readable by any user with folder access.
    This is acceptable because BeadWatch is a single-user local application;
    the encryption protects credentials at rest (e.g. if the config.db file
    is copied), not against a local administrator.
    """
    key_path = Path(ENCRYPTION_KEY_PATH)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        return key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        return key


def encrypt_password(password: str) -> bytes:
    """Encrypt a password string"""
    key = _ensure_key_exists()
    f = Fernet(key)
    return f.encrypt(password.encode())


def decrypt_password(encrypted: bytes) -> str:
    """Decrypt an encrypted password"""
    key = _ensure_key_exists()
    f = Fernet(key)
    return f.decrypt(encrypted).decode()
