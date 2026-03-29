import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_key_dir(tmp_path):
    """Provide a temp directory for encryption key storage"""
    key_path = tmp_path / ".beadwatch_key"
    with patch('utils.encryption.ENCRYPTION_KEY_PATH', key_path):
        yield key_path


class TestEncryption:
    def test_round_trip(self, temp_key_dir):
        from utils.encryption import encrypt_password, decrypt_password
        original = "MyS3cretP@ssword!"
        encrypted = encrypt_password(original)
        decrypted = decrypt_password(encrypted)
        assert decrypted == original

    def test_encrypted_is_not_plaintext(self, temp_key_dir):
        from utils.encryption import encrypt_password
        original = "MyS3cretP@ssword!"
        encrypted = encrypt_password(original)
        assert original.encode() not in encrypted

    def test_key_created_on_first_call(self, temp_key_dir):
        from utils.encryption import encrypt_password
        assert not temp_key_dir.exists()
        encrypt_password("test")
        assert temp_key_dir.exists()

    def test_key_reused_on_second_call(self, temp_key_dir):
        from utils.encryption import encrypt_password, decrypt_password
        encrypted1 = encrypt_password("test")
        key_bytes = temp_key_dir.read_bytes()
        # Second call should reuse the same key
        decrypted = decrypt_password(encrypted1)
        assert decrypted == "test"
        assert temp_key_dir.read_bytes() == key_bytes

    def test_different_passwords_different_ciphertexts(self, temp_key_dir):
        from utils.encryption import encrypt_password
        enc1 = encrypt_password("password1")
        enc2 = encrypt_password("password2")
        assert enc1 != enc2

    def test_empty_password(self, temp_key_dir):
        from utils.encryption import encrypt_password, decrypt_password
        encrypted = encrypt_password("")
        assert decrypt_password(encrypted) == ""
