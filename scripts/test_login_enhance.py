#!/usr/bin/env python3
"""登录增强测试 — scripts/test_login_enhance.py（离线：temp DB + mock 微信 API + 假 JWT）

覆盖（对应 Task1 验收清单 + T1 评审修复）：
1. 手机号绑定成功：AES 密文落库（断言 != 明文且可解回原文）+ 响应脱敏 138****1234；
   日志红线：绑定过程完整手机号不出现在任何日志（只允许脱敏号）
2. watermark.appid 不匹配 → 400
2b. watermark 缺失（缺 appid / 完全缺失）→ 400
3. 微信 API 返回错误 → 400
4. 未登录（无 token）→ 401
5. 重复绑定 → 覆盖成功（换绑新号）
6. profile：保存成功 / 空昵称 400 / 超长 400 / 20 字边界通过
7. avatar：伪造小图上传 → 200 + 文件存在 + GET 200 内容一致 + nosniff 头；
   超 2MB → 400；非法类型 → 400；不存在用户 → 404（公开 GET 无 token 可读）
7b. 头像大小防护双路径：file.size 已知超限 → 读前预拒（未读 body）；
   size 未知超大 → 分块读取中途中止（未读完整文件，内存有界）
8. 迁移幂等：ensure_profile_columns 调两次不报错
9. DAO 未 setup → phone-bind 503（fail-closed 守卫，非假 200）

运行：cd /mnt/e/fortune-agent && .venv/bin/python scripts/test_login_enhance.py
退出码：0=全部通过；1=有失败
"""
import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
import unittest.mock as mock
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from starlette.datastructures import UploadFile

# ── 环境（必须先于任何 src 导入设置；.env 加载不会覆盖已存在的变量）──
os.environ["WECHAT_APP_ID"] = "wx_test_appid_login_enhance"
os.environ["WECHAT_APP_SECRET"] = "test_secret_xxx"
os.environ["DEV_OPENID"] = "login_enhance_dev_user"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import user as user_api
from src.security.auth import AuthHandler, set_auth_handler
from src.security.encryption import DataEncryptor
from src.storage.dao import UserDAO

APPID = os.environ["WECHAT_APP_ID"]
PHONE = "13812341234"
MASKED = "138****1234"
USER_ID = "wx_login_enhance_test_user"

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


class CaptureHandler(logging.Handler):
    """捕获日志记录（红线断言：完整手机号不得出现在任何日志，hlog 模式同 test_stream_pacing）。"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


async def _avatar_direct(payload: bytes, size: Optional[int] = None) -> tuple:
    """直接调 user_upload_avatar（绕过框架表单解析），构造可控 file.size 的 UploadFile。

    返回 (status_code | None, 文件已读位置, 文件总长)：
    - 成功：status_code=None；异常：HTTPException 的 status_code。
    """
    uf = UploadFile(file=BytesIO(payload), size=size, filename="big.jpg",
                    headers={"content-type": "image/jpeg"})
    try:
        await user_api.user_upload_avatar(file=uf, uid=USER_ID)
        return None, uf.file.tell(), len(payload)
    except HTTPException as e:
        return e.status_code, uf.file.tell(), len(payload)


# ── 微信 API mock（httpx.AsyncClient：cgi-bin/token + getuserphonenumber）──
PHONE_API_RESPONSE = {"errcode": 0, "errmsg": "ok", "data": {"phone_info": {
    "phoneNumber": PHONE, "purePhoneNumber": PHONE, "countryCode": "86",
    "watermark": {"appid": APPID, "timestamp": 1755000000}}}}


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeAsyncClient:
    """mock httpx.AsyncClient：两个端点，响应由 PHONE_API_RESPONSE 全局控制。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, **kwargs):
        if "cgi-bin/token" in url:
            return FakeResponse({"access_token": "test_access_token_123", "expires_in": 7200})
        raise AssertionError(f"未预期的 GET {url}")

    async def post(self, url, params=None, json=None, **kwargs):
        if "getuserphonenumber" in url:
            return FakeResponse(PHONE_API_RESPONSE)
        raise AssertionError(f"未预期的 POST {url}")


def db_phone_enc(db: str) -> str:
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT phone_enc FROM users WHERE user_id=?", (USER_ID,)).fetchone()
    conn.close()
    return row[0] if row else None


def db_nickname(db: str) -> str:
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT nickname FROM users WHERE user_id=?", (USER_ID,)).fetchone()
    conn.close()
    return (row[0] if row else "") or ""


def main():
    global PASS, FAIL, PHONE_API_RESPONSE  # 微信 mock 响应在用例间切换（全局可被 FakeAsyncClient 读到）
    tmp = tempfile.mkdtemp(prefix="login_enhance_test_")
    db = os.path.join(tmp, "test.db")
    avatar_dir = os.path.join(tmp, "avatars")
    os.environ["AVATAR_DIR"] = avatar_dir

    dao = UserDAO(db)
    user_api.setup(dao, None, None)
    auth = AuthHandler()
    set_auth_handler(auth)
    user_api.setup(dao, None, auth)

    app = FastAPI(title="login-enhance-test")
    app.include_router(user_api.router)
    client = TestClient(app)

    TOKEN = auth.create_user_token(USER_ID)
    h = {"Authorization": f"Bearer {TOKEN}"}

    print("=" * 62)
    print("测试 0：迁移幂等（PRAGMA 检查缺列才 ALTER）")
    print("=" * 62)
    dao.ensure_profile_columns()
    dao.ensure_profile_columns()  # 第二次不报错
    check("ensure_profile_columns 调两次不报错", True)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    conn.close()
    check("users 表含 phone_enc/nickname 列", "phone_enc" in cols and "nickname" in cols,
          f"cols={sorted(cols)}")

    print("=" * 62)
    print("测试 1：未登录 → 401")
    print("=" * 62)
    r = client.post("/api/user/phone-bind", json={"code": "no_token_code"})
    check("未登录 phone-bind → 401", r.status_code == 401, str(r.status_code))
    r = client.get("/api/user/phone")
    check("未登录 GET phone → 401", r.status_code == 401, str(r.status_code))
    r = client.post("/api/user/profile", json={"nickname": "测试"})
    check("未登录 profile → 401", r.status_code == 401, str(r.status_code))
    r = client.post("/api/user/avatar",
                    files={"file": ("a.jpg", b"xx", "image/jpeg")})
    check("未登录 avatar 上传 → 401", r.status_code == 401, str(r.status_code))

    print("=" * 62)
    print("测试 2：手机号绑定成功（密文落库 + 脱敏响应 + 日志不打全号）")
    print("=" * 62)
    cap = CaptureHandler()
    hlog = logging.getLogger()
    prev_level = hlog.level
    hlog.setLevel(logging.INFO)
    hlog.addHandler(cap)
    try:
        with mock.patch("httpx.AsyncClient", FakeAsyncClient):
            r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_1"}, headers=h)
    finally:
        hlog.removeHandler(cap)
        hlog.setLevel(prev_level)
    check("绑定成功 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    d = r.json()
    check("响应 phone_masked == 138****1234", d.get("phone_masked") == MASKED, str(d))
    check("响应含提示文案", bool(d.get("message")), str(d))
    enc = db_phone_enc(db)
    check("phone_enc 已落库", bool(enc), str(enc))
    check("落库为密文（非明文、含版本前缀）", enc != PHONE and ":" in enc, str(enc))
    check("密文可解密回原文", DataEncryptor().decrypt(enc) == PHONE, str(DataEncryptor().decrypt(enc)))
    # 日志红线：绑定过程日志只允许脱敏号，完整手机号不得出现（捕获生效用脱敏号正断言兜底）
    log_text = "\n".join(cap.lines)
    check("绑定日志含脱敏号（捕获生效）", MASKED in log_text, log_text[:300])
    check("完整手机号不出现在任何日志", PHONE not in log_text, log_text[:300])

    print("=" * 62)
    print("测试 3：GET /api/user/phone 绑定状态")
    print("=" * 62)
    r = client.get("/api/user/phone", headers=h)
    d = r.json()
    check("GET phone → bound=true + 脱敏号", r.status_code == 200 and d.get("bound") is True
          and d.get("phone_masked") == MASKED, f"{r.status_code} {d}")

    print("=" * 62)
    print("测试 4：watermark.appid 不匹配 → 400")
    print("=" * 62)
    PHONE_API_RESPONSE["data"]["phone_info"]["watermark"]["appid"] = "wx_evil_appid"
    with mock.patch("httpx.AsyncClient", FakeAsyncClient):
        r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_bad"}, headers=h)
    check("watermark 不符 → 400", r.status_code == 400, f"{r.status_code} {r.text[:150]}")
    check("400 文案明确", "校验失败" in r.text or "重新授权" in r.text, r.text[:150])
    PHONE_API_RESPONSE["data"]["phone_info"]["watermark"]["appid"] = APPID

    print("=" * 62)
    print("测试 4b：watermark 缺失（只传部分字段）→ 400")
    print("=" * 62)
    PHONE_API_RESPONSE = {"errcode": 0, "errmsg": "ok", "data": {"phone_info": {
        "phoneNumber": PHONE, "purePhoneNumber": PHONE, "countryCode": "86",
        "watermark": {"timestamp": 1755000000}}}}  # 缺 appid
    with mock.patch("httpx.AsyncClient", FakeAsyncClient):
        r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_nw1"}, headers=h)
    check("watermark 缺 appid → 400", r.status_code == 400, f"{r.status_code} {r.text[:150]}")
    PHONE_API_RESPONSE = {"errcode": 0, "errmsg": "ok", "data": {"phone_info": {
        "phoneNumber": PHONE, "purePhoneNumber": PHONE, "countryCode": "86"}}}  # 完全无 watermark
    with mock.patch("httpx.AsyncClient", FakeAsyncClient):
        r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_nw2"}, headers=h)
    check("watermark 完全缺失 → 400", r.status_code == 400, f"{r.status_code} {r.text[:150]}")
    PHONE_API_RESPONSE = {"errcode": 0, "errmsg": "ok", "data": {"phone_info": {
        "phoneNumber": PHONE, "purePhoneNumber": PHONE, "countryCode": "86",
        "watermark": {"appid": APPID, "timestamp": 1755000000}}}}

    print("=" * 62)
    print("测试 5：微信 API 返回错误 → 400")
    print("=" * 62)
    PHONE_API_RESPONSE = {"errcode": 40029, "errmsg": "invalid code", "data": {}}
    with mock.patch("httpx.AsyncClient", FakeAsyncClient):
        r = client.post("/api/user/phone-bind", json={"code": "bad_code"}, headers=h)
    check("微信返回错误 → 400", r.status_code == 400, f"{r.status_code} {r.text[:150]}")
    PHONE_API_RESPONSE = {"errcode": 0, "errmsg": "ok", "data": {"phone_info": {
        "phoneNumber": PHONE, "purePhoneNumber": PHONE, "countryCode": "86",
        "watermark": {"appid": APPID, "timestamp": 1755000000}}}}

    print("=" * 62)
    print("测试 6：重复绑定 → 覆盖（换绑新号）")
    print("=" * 62)
    new_phone = "13999998888"
    PHONE_API_RESPONSE["data"]["phone_info"]["phoneNumber"] = new_phone
    PHONE_API_RESPONSE["data"]["phone_info"]["purePhoneNumber"] = new_phone
    with mock.patch("httpx.AsyncClient", FakeAsyncClient):
        r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_2"}, headers=h)
    check("换绑成功 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    check("换绑返回新号脱敏 139****8888", r.json().get("phone_masked") == "139****8888",
          str(r.json()))
    check("换绑提示文案（已更新）", "更新" in r.json().get("message", ""), str(r.json()))
    enc = db_phone_enc(db)
    check("库中密文已覆盖为新号", DataEncryptor().decrypt(enc) == new_phone,
          str(DataEncryptor().decrypt(enc)))

    print("=" * 62)
    print("测试 7：昵称保存")
    print("=" * 62)
    r = client.post("/api/user/profile", json={"nickname": "  明灯用户  "}, headers=h)
    check("昵称保存成功（自动 strip）", r.status_code == 200
          and r.json().get("nickname") == "明灯用户", f"{r.status_code} {r.text[:150]}")
    check("昵称落库", db_nickname(db) == "明灯用户", db_nickname(db))
    r = client.post("/api/user/profile", json={"nickname": "   "}, headers=h)
    check("空昵称 → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = client.post("/api/user/profile", json={"nickname": "明" * 21}, headers=h)
    check("超长昵称（21 字）→ 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = client.post("/api/user/profile", json={"nickname": "明" * 20}, headers=h)
    check("20 字昵称（边界）→ 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    print("=" * 62)
    print("测试 8：头像上传 / 公开读取")
    print("=" * 62)
    fake_img = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"  # 伪 JPEG
    r = client.post("/api/user/avatar",
                    files={"file": ("avatar.jpg", fake_img, "image/jpeg")}, headers=h)
    check("头像上传 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    avatar_url = r.json().get("avatar_url", "")
    check("avatar_url == /api/user/avatar/{user_id}",
          avatar_url == f"/api/user/avatar/{USER_ID}", avatar_url)
    path = Path(avatar_dir) / f"{USER_ID}.jpg"
    check("头像文件存在", path.is_file(), str(path))
    r = client.get(avatar_url)  # 公开 GET：无 token
    check("GET 头像 200 且内容一致", r.status_code == 200 and r.content == fake_img,
          f"{r.status_code} len={len(r.content)}")
    check("GET 头像带 X-Content-Type-Options: nosniff",
          r.headers.get("x-content-type-options") == "nosniff",
          str(r.headers.get("x-content-type-options")))
    # 覆盖上传
    fake_img2 = b"\xff\xd8\xff\xe0" + b"\x01" * 200 + b"\xff\xd9"
    r = client.post("/api/user/avatar",
                    files={"file": ("avatar.png", fake_img2, "image/png")}, headers=h)
    check("png 类型上传覆盖 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = client.get(avatar_url)
    check("覆盖后 GET 内容为新图", r.status_code == 200 and r.content == fake_img2,
          f"{r.status_code} len={len(r.content)}")

    big = b"\xff\xd8" * (2 * 1024 * 1024 + 10)
    r = client.post("/api/user/avatar",
                    files={"file": ("big.jpg", big, "image/jpeg")}, headers=h)
    check(">2MB → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = client.post("/api/user/avatar",
                    files={"file": ("evil.gif", b"GIF89a", "image/gif")}, headers=h)
    check("非法类型 gif → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = client.post("/api/user/avatar",
                    files={"file": ("evil.exe", b"MZ", "application/octet-stream")}, headers=h)
    check("非法类型 exe → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

    r = client.get("/api/user/avatar/no_such_user")
    check("不存在用户头像 → 404", r.status_code == 404, f"{r.status_code}")

    # 大小防护双路径（评审修复）：
    # A. file.size（Content-Length 派生）已知超限 → 读前预拒，body 一个字都不读
    status, pos, total = asyncio.run(_avatar_direct(b"\xff\xd8" * (3 * 1024 * 1024),
                                                    size=3 * 1024 * 1024 + 1))
    check("size 已知超限 → 读前预拒 400", status == 400, str(status))
    check("预拒路径未读 body（位置=0）", pos == 0, f"pos={pos}")
    # B. size 未知（无 Content-Length）超大文件 → 分块读取累计超限中途中止
    status, pos, total = asyncio.run(_avatar_direct(b"\xff\xd8" * (3 * 1024 * 1024)))
    check("size 未知超大 → 分块中止 400", status == 400, str(status))
    check("分块路径中途中止（未读完整文件）", pos < total, f"pos={pos} < total={total}")
    # C. starlette 表单解析下 file.size==0：小图不得被误拒
    status, pos, total = asyncio.run(_avatar_direct(
        b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9", size=0))
    check("size=0（starlette 默认）小图不误拒", status is None, str(status))

    print("=" * 62)
    print("测试 9：DAO 未 setup → phone-bind 503（fail-closed 守卫，非假 200）")
    print("=" * 62)
    saved_dao = user_api._dao
    user_api._dao = None
    try:
        with mock.patch("httpx.AsyncClient", FakeAsyncClient):
            r = client.post("/api/user/phone-bind", json={"code": "wx_phone_code_503"}, headers=h)
    finally:
        user_api._dao = saved_dao
    check("DAO 未 setup → 503", r.status_code == 503, f"{r.status_code} {r.text[:150]}")

    print("=" * 62)
    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
