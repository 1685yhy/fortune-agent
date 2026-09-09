# -*- coding: utf-8 -*-
"""k20 加密层审计收口测试（2026-09-10）：AES-256-GCM 质量核实。

覆盖：
- GCM 认证篡改检测（改密文/改 tag/改 nonce → 解密失败 None）
- nonce 唯一性（批量加密无重复）
- 密钥轮换解析（v1/v2 格式、末键生效、坏格式告警回退、旧钥可解旧数据）
- 旧明文行兼容（dao._decrypt_or_plain / users.bazi_info 读时懒迁移）
- v1 格式往返 / AAD 认证 / 空值与异常输入
- dev 兜底密钥告警（ENCRYPTION_KEY 未配置）

零网络零 LLM；DataEncryptor 级用例显式传密钥（不依赖进程环境），
dao 级用例 monkeypatch dao._encryptor 为显式密钥实例（不依赖收集顺序）。
"""
import base64
import json
import logging
import os
import sqlite3

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.security.encryption import DataEncryptor  # noqa: E402
import src.storage.dao as dao  # noqa: E402

# 32 字节确定性测试密钥（显式传入，绝不走环境）
KEY_A = base64.b64encode(b"A" * 32).decode()
KEY_B = base64.b64encode(b"B" * 32).decode()


def _tamper(stored: str, offset: int) -> str:
    """翻转密文字符串中 offset 字节后重封包（offset 相对 nonce||ct||tag 字节流）。"""
    version, b64_data = stored.split(":", 1)
    blob = bytearray(base64.b64decode(b64_data))
    blob[offset] ^= 0x01
    return f"{version}:{base64.b64encode(bytes(blob)).decode()}"


# ── GCM 认证：篡改必须解密失败 ───────────────────────────────────────

class TestGCNAuth:
    def setup_method(self):
        self.e = DataEncryptor(encryption_key=KEY_A)

    def test_roundtrip_cjk_emoji(self):
        plain = "1995-03-28 09:00 长春 男 🌙\n换行 乙卯年"
        enc = self.e.encrypt(plain)
        assert enc.startswith("v1:")
        assert self.e.decrypt(enc) == plain

    def test_tamper_ciphertext_byte_fails(self):
        enc = self.e.encrypt("密文区篡改必须失败")
        # nonce 12 字节后即为密文首字节
        assert self.e.decrypt(_tamper(enc, 12)) is None

    def test_tamper_tag_byte_fails(self):
        enc = self.e.encrypt("tag 篡改必须失败")
        blob = base64.b64decode(enc.split(":", 1)[1])
        # 末 16 字节为 GCM tag
        assert self.e.decrypt(_tamper(enc, len(blob) - 1)) is None

    def test_tamper_nonce_byte_fails(self):
        enc = self.e.encrypt("nonce 篡改必须失败")
        assert self.e.decrypt(_tamper(enc, 0)) is None

    def test_aad_mismatch_fails_correct_aad_passes(self):
        plain = "带 AAD 的数据"
        enc = self.e.encrypt(plain, aad=b"ctx:user:42")
        assert self.e.decrypt(enc, aad=b"ctx:user:43") is None
        assert self.e.decrypt(enc, aad=b"ctx:user:42") == plain
        assert self.e.decrypt(enc) is None  # 默认 aad=b"" 与存储时不符

    def test_nonce_uniqueness_bulk(self):
        """批量加密同一文本：全部输出互异且 nonce 无重复。"""
        text = "同文批量加密"
        nonces = set()
        outs = set()
        for _ in range(500):
            enc = self.e.encrypt(text)
            outs.add(enc)
            nonces.add(base64.b64decode(enc.split(":", 1)[1])[:12])
        assert len(outs) == 500
        assert len(nonces) == 500
        assert self.e.decrypt(next(iter(outs))) == text


# ── 密钥轮换解析：v1/v2 格式、末键生效、坏格式告警回退 ────────────────

class TestKeyRotation:
    def test_v1_single_key_format_and_roundtrip(self):
        e = DataEncryptor(encryption_key=f"v1:{KEY_A}")
        enc = e.encrypt("v1 格式往返")
        assert enc.startswith("v1:")
        assert e.decrypt(enc) == "v1 格式往返"
        blob = base64.b64decode(enc.split(":", 1)[1])
        assert len(blob) >= 12 + 16 + 1  # nonce(12)+ct+tag(16)

    def test_single_raw_key_format(self):
        e = DataEncryptor(encryption_key="x" * 32)  # 裸 32 字节串（旧兼容路径）
        enc = e.encrypt("raw key")
        assert e.decrypt(enc) == "raw key"

    def test_rotation_last_key_current_and_old_decrypts(self):
        old = DataEncryptor(encryption_key=f"v1:{KEY_A}")
        enc_old = old.encrypt("轮换前的旧行")
        assert enc_old.startswith("v1:")

        rot = DataEncryptor(encryption_key=f"v1:{KEY_A},v2:{KEY_B}")
        enc_new = rot.encrypt("轮换后的新行")
        assert enc_new.startswith("v2:")  # 末键生效
        # 新实例可解旧 v1 行（旧钥保留供解密）；v1 单钥实例解不了 v2 新行
        assert rot.decrypt(enc_old) == "轮换前的旧行"
        assert old.decrypt(enc_new) is None

    def test_rotation_entries_in_any_order_last_valid_wins(self):
        # v2 在前 v1 在后 → 最后合法段 v1 为当前键
        e = DataEncryptor(encryption_key=f"v2:{KEY_B},v1:{KEY_A}")
        assert e.encrypt("x").startswith("v1:")

    def test_malformed_entry_skipped_others_work(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.security.encryption"):
            e = DataEncryptor(encryption_key=f"v1:{KEY_A},v2:!!!bad-entry!!!")
        assert not e.encrypt("x").startswith("v2:")  # 坏 v2 未生效
        assert e.encrypt("x").startswith("v1:")
        enc = e.encrypt("坏段不影响")
        assert e.decrypt(enc) == "坏段不影响"
        assert any("跳过" in r.message for r in caplog.records)

    def test_all_bad_entries_fall_back_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.security.encryption"):
            e = DataEncryptor(encryption_key="v1:@@@,v2:####")
        enc = e.encrypt("全坏降级仍可往返")
        assert e.decrypt(enc) == "全坏降级仍可往返"  # 不崩、自洽
        assert any("无任何合法版本键" in r.message for r in caplog.records)

    def test_entry_missing_colon_warns_and_skipped(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.security.encryption"):
            e = DataEncryptor(encryption_key=f"v1:{KEY_A},naked-garbage")
        assert e.encrypt("x").startswith("v1:")
        assert any("缺少 ':'" in r.message for r in caplog.records)

    def test_unknown_version_returns_none(self):
        e = DataEncryptor(encryption_key=f"v1:{KEY_A}")
        enc = e.encrypt("旧版本丢失后解密失败")
        v1_body = enc.split(":", 1)[1]
        assert e.decrypt(f"v9:{v1_body}") is None  # 未知版本 → None
        assert e.decrypt(f"v2:{v1_body}") is None  # 未配 v2 键 → None


# ── 空值/异常输入 / dev 兜底 ──────────────────────────────────────────

class TestEdgeCases:
    def test_decrypt_empty_and_none_inputs(self):
        e = DataEncryptor(encryption_key=KEY_A)
        assert e.decrypt(None) is None
        assert e.decrypt("") is None
        assert e.decrypt("no-colon-plain") is None
        assert e.decrypt("v1:@@@garbage@@@") is None  # 坏 base64 → None

    def test_dev_fallback_warns_and_is_deterministic(self, monkeypatch, caplog):
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="src.security.encryption"):
            e1 = DataEncryptor()
            e2 = DataEncryptor()
        assert any("ENCRYPTION_KEY not set" in r.message for r in caplog.records)
        enc = e1.encrypt("dev 兜底往返")
        assert enc.startswith("dev:")
        assert e1.decrypt(enc) == "dev 兜底往返"
        # dev 密钥同主机确定性派生 → 跨实例可解
        assert e2.decrypt(enc) == "dev 兜底往返"

    def test_wrong_key_returns_none(self):
        e1 = DataEncryptor(encryption_key=KEY_A)
        e2 = DataEncryptor(encryption_key=KEY_B)
        assert e2.decrypt(e1.encrypt("跨钥解密")) is None


# ── 旧明文行兼容（dao 层）：_decrypt_or_plain + bazi_info 读时懒迁移 ──

@pytest.fixture
def dao_enc(monkeypatch):
    """把 dao 模块加密器钉到显式密钥实例（不随收集顺序/环境漂移）。"""
    inst = DataEncryptor(encryption_key=KEY_A)
    monkeypatch.setattr(dao, "_encryptor", inst)
    return inst


def test_is_ciphertext_matrix(dao_enc):
    assert not dao._is_ciphertext(None)
    assert not dao._is_ciphertext('{"year":1995}')       # 旧明文 JSON
    assert not dao._is_ciphertext('[{"a":1}]')           # 旧明文 JSON 数组
    assert not dao._is_ciphertext("普通明文无冒号")
    assert dao._is_ciphertext("v1:AAAA")                 # 密文形态


def test_decrypt_or_plain_legacy_and_ciphertext(dao_enc):
    # 旧明文 JSON → 原样返回（不解密不动）
    plain_json = '{"year":1995,"bazi":[]}'
    assert dao._decrypt_or_plain(plain_json) == plain_json
    # 密文 → 正确解密出 JSON 文本
    enc = dao._encrypt_text(plain_json)
    assert dao._decrypt_or_plain(enc) == plain_json
    # 含冒号的非 JSON 旧明文（10:00 之类）→ 解密失败按明文兼容原样返回
    legacy = "2026-09-10 10:00 的运势问题"
    assert dao._decrypt_or_plain(legacy) == legacy
    # 篡改密文 → 解密失败降级原样返回（不抛、不吐解密垃圾）
    tampered = _tamper(enc, 12)
    assert dao._decrypt_or_plain(tampered) == tampered
    # 空值透传
    assert dao._decrypt_or_plain(None) is None
    assert dao._decrypt_or_plain("") == ""


def test_user_bazi_legacy_plaintext_lazy_migration(dao_enc, tmp_path):
    """旧明文行读取即迁移为密文（不改变业务字段）。"""
    from src.storage.models import init_db
    db = str(tmp_path / "t.db")
    init_db(db)
    info = {"year": 1995, "month": 3, "day": 28, "hour": 9,
            "minute": 0, "city": "长春", "gender": "男",
            "calendar": "solar", "bazi": ["乙亥", "己卯", "戊戌", "丁巳"]}
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO users (user_id, bazi_info, created_at, updated_at, "
        "consultation_count) VALUES (?,?,?,?,1)",
        ("u_legacy", json.dumps(info, ensure_ascii=False),
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    dao_obj = dao.UserDAO(db)
    got = dao_obj.get_user_bazi("u_legacy")
    assert got["year"] == 1995 and got["bazi"][0] == "乙亥"

    # 读后原行已迁移为密文（懒迁移写回）
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT bazi_info FROM users WHERE user_id='u_legacy'").fetchone()
    conn.close()
    assert row and row[0].startswith("v1:")
    # 再读一次仍可解析（迁移后路径）
    assert dao_obj.get_user_bazi("u_legacy")["gender"] == "男"


def test_encrypt_text_none_passthrough(dao_enc):
    assert dao._encrypt_text(None) is None
    assert dao._encrypt_text("") == ""
