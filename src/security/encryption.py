"""Data encryption module for Fortune Agent.

Provides AES-256-GCM encryption for sensitive user data at rest:
- Birth dates and times
- Bazi chart information
- Personal questions and chat history
- User PII

Key management:
- Encryption key from environment variable (ENCRYPTION_KEY)
- Key rotation support via key versioning
- Each encryption operation uses a unique nonce
"""
import os
import base64
import hashlib
import secrets
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class DataEncryptor:
    """AES-256-GCM data encryptor with key rotation support.

    Features:
    - AES-256-GCM authenticated encryption
    - Unique nonce per encryption operation
    - Key rotation via versioned keys
    - User ID hashing for log storage
    """

    # Key derivation salt (fixed, not secret — used for deterministic key derivation)
    _SALT = b"fortune_agent_encryption_v1"

    def __init__(self, encryption_key: Optional[str] = None):
        """Initialize encryptor with key from environment or parameter.

        Key format (from ENCRYPTION_KEY env var):
        - Single key: base64-encoded 32-byte key
        - Versioned: v1:base64key,v2:base64key (current key is last)
        """
        key_str = encryption_key or os.getenv("ENCRYPTION_KEY", "")

        if not key_str:
            # Development mode: generate a deterministic key
            # WARNING: Never rely on this in production!
            self._dev_mode = True
            logger.warning(
                "ENCRYPTION_KEY not set. Using derived development key. "
                "Set ENCRYPTION_KEY environment variable in production."
            )
            self._keys = self._derive_dev_key()
            self._current_version = "dev"
        else:
            self._dev_mode = False
            self._keys, self._current_version = self._parse_keys(key_str)

        self._key_size = 32  # AES-256 = 32 bytes
        self._nonce_size = 12  # GCM standard nonce size

    def _derive_dev_key(self) -> Dict[str, bytes]:
        """Derive a development encryption key from host info."""
        import socket
        host = socket.gethostname()
        key_material = f"fortune_dev_{host}".encode()
        key = hashlib.sha256(key_material).digest()
        return {"dev": key}

    def _parse_keys(self, key_str: str) -> tuple:
        """Parse versioned or single key string.

        Format:
        - Single: base64key (32 bytes when decoded)
        - Versioned: v1:base64key,v2:base64key
        """
        keys = {}
        current_version = "v1"

        if "," in key_str and ":" in key_str:
            # Versioned keys
            for entry in key_str.split(","):
                entry = entry.strip()
                if ":" in entry:
                    version, key_b64 = entry.split(":", 1)
                    key_bytes = base64.b64decode(key_b64)
                    if len(key_bytes) == self._key_size:
                        keys[version.strip()] = key_bytes
                        current_version = version.strip()
        else:
            # Single key
            try:
                key_bytes = base64.b64decode(key_str)
                if len(key_bytes) == self._key_size:
                    keys["v1"] = key_bytes
                else:
                    # Try as raw 32-byte string
                    if len(key_str.encode()) == self._key_size:
                        keys["v1"] = key_str.encode()
                    else:
                        # Fallback: derive from string
                        keys["v1"] = hashlib.sha256(key_str.encode()).digest()
                        logger.warning("Key derived via SHA-256 (not secure base64)")
            except Exception:
                # Fallback: derive from the string
                keys["v1"] = hashlib.sha256(key_str.encode()).digest()
                logger.warning("Key derived via SHA-256 (invalid base64)")

        return keys, current_version

    def encrypt(self, plaintext: str, aad: bytes = b"") -> str:
        """Encrypt plaintext using AES-256-GCM.

        Returns base64-encoded ciphertext with format:
        version:nonce:ciphertext:tag

        Args:
            plaintext: String data to encrypt
            aad: Additional authenticated data (optional)
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = self._keys[self._current_version]
        aesgcm = AESGCM(key)

        # Generate unique nonce
        nonce = secrets.token_bytes(self._nonce_size)

        # Encrypt
        plaintext_bytes = plaintext.encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, aad)

        # Format: nonce (12) + ciphertext + tag (16)
        # AESGCM.encrypt returns nonce|ciphertext|tag concatenated
        # Actually, we pass nonce separately and get ciphertext+tag
        # So aesgcm.encrypt(nonce, data, aad) returns ciphertext+tag (16 byte tag)

        # Store as: version:base64(nonce + ciphertext + tag)
        combined = nonce + ciphertext
        result = f"{self._current_version}:{base64.b64encode(combined).decode()}"

        return result

    def decrypt(self, ciphertext_str: str, aad: bytes = b"") -> Optional[str]:
        """Decrypt AES-256-GCM ciphertext.

        Args:
            ciphertext_str: Encrypted string in format version:base64data
            aad: Additional authenticated data (same as encrypt)

        Returns:
            Decrypted plaintext string, or None if decryption fails
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            # Parse format
            if ":" not in ciphertext_str:
                logger.error("Invalid ciphertext format (no version)")
                return None

            version, b64_data = ciphertext_str.split(":", 1)

            # Get the key for this version
            if version not in self._keys:
                logger.error("Unknown encryption key version: %s", version)
                return None

            key = self._keys[version]
            aesgcm = AESGCM(key)

            # Decode
            combined = base64.b64decode(b64_data)

            # Extract nonce (first 12 bytes)
            nonce = combined[:self._nonce_size]
            ciphertext = combined[self._nonce_size:]

            # Decrypt
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext_bytes.decode("utf-8")

        except Exception as e:
            logger.error("Decryption failed: %s", str(e))
            return None

    def encrypt_user_id(self, user_id: str) -> str:
        """Create a deterministic hash of user ID for log storage.

        Uses HMAC-SHA256 with the encryption key so user IDs
        are pseudonymous in logs but can be traced if needed.
        """
        import hmac

        key = self._keys[self._current_version]
        digest = hmac.new(key, user_id.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest[:12]).decode()  # Truncated for brevity

    def encrypt_dict(self, data: Dict[str, Any], sensitive_fields: list) -> Dict[str, Any]:
        """Encrypt specified fields in a dictionary.

        Args:
            data: Dictionary containing data to encrypt
            sensitive_fields: List of field names to encrypt

        Returns:
            Copy of dict with sensitive fields encrypted
        """
        result = dict(data)
        for field in sensitive_fields:
            if field in result and isinstance(result[field], str) and result[field]:
                result[field] = self.encrypt(result[field])
        return result

    def decrypt_dict(self, data: Dict[str, Any], sensitive_fields: list) -> Dict[str, Any]:
        """Decrypt specified fields in a dictionary."""
        result = dict(data)
        for field in sensitive_fields:
            if field in result and isinstance(result[field], str) and result[field]:
                decrypted = self.decrypt(result[field])
                if decrypted is not None:
                    result[field] = decrypted
        return result

    def rotate_key(self, new_key_b64: str) -> bool:
        """Add a new encryption key for rotation.

        New key will be used for future encryptions.
        Old keys are preserved for decryption of existing data.
        """
        try:
            new_key = base64.b64decode(new_key_b64)
            if len(new_key) != self._key_size:
                logger.error("New key must be 32 bytes (got %d)", len(new_key))
                return False

            # Generate next version number
            existing_versions = [v for v in self._keys.keys() if v.startswith("v")]
            numbers = []
            for v in existing_versions:
                try:
                    numbers.append(int(v[1:]))
                except (ValueError, IndexError):
                    pass
            next_num = max(numbers) + 1 if numbers else 1
            new_version = f"v{next_num}"

            self._keys[new_version] = new_key
            self._current_version = new_version

            logger.info("Encryption key rotated to version %s", new_version)
            return True

        except Exception as e:
            logger.error("Key rotation failed: %s", str(e))
            return False
