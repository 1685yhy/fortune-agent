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

算法审计（2026-09-10 k20 收口确认）：本模块为 AES-256-GCM 认证加密
（AES-256 = 32 字节密钥，GCM 标准 12 字节 nonce，cryptography AESGCM），
不存在 XOR/自定义混淆路径；密钥轮换 = v1:key,v2:key 版本化解析、末键为
当前加密钥、旧键保留供解密。
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
        - Versioned: v1:base64key,v2:base64key (current key is last)；
          单个带标签键 "v1:base64key" 亦合法（k20 审计确认）
        """
        # Bugfix: _key_size/_nonce_size 必须在 _parse_keys 之前初始化，
        # 否则 _parse_keys 读 self._key_size 抛 AttributeError，
        # 被 except 吞掉后走 SHA-256 派生兜底（日志出现 "invalid base64" 误报）。
        self._key_size = 32  # AES-256 = 32 bytes
        self._nonce_size = 12  # GCM standard nonce size

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

        # k20 修复（2026-09-10 审计）：任何含 ':' 的都按版本化解析——
        # base64 字母表不含 ':'，单钥裸 base64 不可能含冒号；旧判据
        # ("," and ":") 把单键带标签形态 "v1:KEY"（无逗号）误落单钥分支 →
        # base64 解码失败 → 静默 SHA-256 派生错误密钥（本轮测试实证）。
        if ":" in key_str:
            # Versioned keys
            # 逐项解析必须容错——坏项告警并跳过，
            # 不能抛异常（DataEncryptor() 在 main.py lifespan 直建，异常=启动崩）；
            # 全坏时降级 SHA-256 派生并大声告警（与下方单钥兜底同一哲学：不崩、
            # 告警可查）。无 ":" 的裸段同样告警跳过（原来被静默忽略）。
            for entry in key_str.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if ":" not in entry:
                    logger.warning(
                        "ENCRYPTION_KEY 段缺少 ':' 已跳过: %.20s...", entry)
                    continue
                version, key_b64 = entry.split(":", 1)
                try:
                    key_bytes = base64.b64decode(key_b64)
                except Exception:
                    logger.warning(
                        "ENCRYPTION_KEY 段 %r base64 非法已跳过（其余键不受影响）",
                        version.strip())
                    continue
                if len(key_bytes) == self._key_size:
                    keys[version.strip()] = key_bytes
                    current_version = version.strip()
                else:
                    logger.warning(
                        "ENCRYPTION_KEY 段 %r 解码 %d 字节（需 %d）已跳过",
                        version.strip(), len(key_bytes), self._key_size)
            if not keys:
                keys["v1"] = hashlib.sha256(key_str.encode()).digest()
                logger.warning(
                    "ENCRYPTION_KEY 无任何合法版本键，已派生降级密钥——"
                    "已有密文行将无法解密！请检查 ENCRYPTION_KEY 格式"
                    "（v1:base64key,v2:base64key）")
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
        # AESGCM.encrypt(nonce, data, aad) 返回 ct||tag（tag 固定 16 字节）
        ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, aad)

        # 落库格式（k20 审计确认，2026-09-10）：
        #   {version}:{base64(nonce(12) || ciphertext || tag(16))}
        # 无内嵌 aad——解密必须传同一 aad（全仓消费点均为 aad=b""）；
        # 认证由 tag 保证：篡改任意一字节 → decrypt 抛 InvalidTag → 返回 None。
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
            # 空值/异常输入防御（k20 审计，2026-09-10）：与类型标注一致，
            # None 与空串返回 None，不抛异常——调用方守卫外再兜一层。
            if not ciphertext_str:
                return None
            if not isinstance(ciphertext_str, str):
                return None
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
