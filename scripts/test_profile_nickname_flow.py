#!/usr/bin/env python3
"""昵称头像联动测试 — scripts/test_profile_nickname_flow.py（离线：temp DB + 假 JWT）

覆盖（Task4 验收：save → profile 读取闭环，GET /api/user/profile 返回 nickname/avatar_url）：
1. POST /api/user/profile 存昵称 → GET /api/user/profile 返回同一 nickname（保存→读取闭环）
2. 覆盖保存：二次保存新昵称，profile 读回新值
3. 未保存昵称的新用户 → GET profile nickname == ""（不报错、不假默认）
4. 上传头像 → GET profile 返回 avatar_url == "/api/user/avatar/{uid}"
5. 公开 GET 头像 200 + image/jpeg + nosniff 头；无头像用户 profile.avatar_url == ""
6. 未登录（无 token）→ 401；空昵称 → 400；21 字昵称 → 400

运行：cd /mnt/e/fortune-agent-deploy && python3 scripts/test_profile_nickname_flow.py
退出码：0=全部通过；1=有失败
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DEV_OPENID"] = "profile_flow_dev_user"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import user as user_api
from src.security.auth import AuthHandler, set_auth_handler
from src.storage.dao import UserDAO

USER_ID = "wx_profile_flow_user"
OTHER_ID = "wx_profile_flow_other"

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


def main():
    tmp = tempfile.mkdtemp(prefix="profile_flow_test_")
    db = os.path.join(tmp, "test.db")
    avatar_dir = os.path.join(tmp, "avatars")
    os.environ["AVATAR_DIR"] = avatar_dir

    dao = UserDAO(db)
    auth = AuthHandler()
    set_auth_handler(auth)
    user_api.setup(dao, None, auth)

    app = FastAPI(title="profile-nickname-flow-test")
    app.include_router(user_api.router)
    client = TestClient(app)

    TOKEN = auth.create_user_token(USER_ID)
    h = {"Authorization": f"Bearer {TOKEN}"}

    print("=" * 62)
    print("测试 1：save → profile 读取闭环（昵称落库并读回）")
    print("=" * 62)
    r = client.post("/api/user/profile", json={"nickname": "秋水明"}, headers=h)
    check("保存昵称 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    d = r.json()
    check("响应返回 {success, nickname}", d.get("success") is True and d.get("nickname") == "秋水明", str(d))
    r = client.get("/api/user/profile", headers=h)
    d = r.json()
    check("GET profile 200", r.status_code == 200, f"{r.status_code}")
    check("profile.nickname == 保存值", d.get("nickname") == "秋水明", f"nickname={d.get('nickname')!r}")
    check("profile.avatar_url == ''（未传头像）", d.get("avatar_url") == "", f"avatar_url={d.get('avatar_url')!r}")
    check("既有字段不受影响（has_bazi 存在）", "has_bazi" in d and "bazi_info" in d, str(d.keys()))

    print("=" * 62)
    print("测试 2：覆盖保存 → profile 读回新值")
    print("=" * 62)
    r = client.post("/api/user/profile", json={"nickname": "灯下客"}, headers=h)
    check("二次保存 200", r.status_code == 200, str(r.status_code))
    d = client.get("/api/user/profile", headers=h).json()
    check("profile.nickname == 新值", d.get("nickname") == "灯下客", f"nickname={d.get('nickname')!r}")

    print("=" * 62)
    print("测试 3：未保存昵称的新用户 → nickname == ''（不假默认）")
    print("=" * 62)
    OTHER_TOKEN = auth.create_user_token(OTHER_ID)
    d = client.get("/api/user/profile", headers={"Authorization": f"Bearer {OTHER_TOKEN}"}).json()
    check("新用户 nickname == ''", d.get("nickname") == "", f"nickname={d.get('nickname')!r}")
    check("新用户 avatar_url == ''", d.get("avatar_url") == "", f"avatar_url={d.get('avatar_url')!r}")

    print("=" * 62)
    print("测试 4：上传头像 → profile 返回 avatar_url（同上传响应格式）")
    print("=" * 62)
    tiny_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c"
        "140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27"
        "3d3d2e313d2f3d3535ffc0000b080001000101011100ffc4001f000001050101010101010000000000"
        "0000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411"
        "05122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a252627"
        "28292a3435363738393a434445464748494a535455565758595a636465666768696a73747576777879"
        "7a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6"
        "c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f010003"
        "01010101010101010000000000000102030405060708090a0bffc400b511000201020404030407050404"
        "00010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a16"
        "2434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768"
        "696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6"
        "b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"
        "ffda000c03010002110311003f00f6fa28a2803fffd9"
    )
    r = client.post("/api/user/avatar", files={"file": ("a.jpg", tiny_jpeg, "image/jpeg")}, headers=h)
    check("上传头像 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    d = r.json()
    check("上传响应 avatar_url == /api/user/avatar/{uid}", d.get("avatar_url") == f"/api/user/avatar/{USER_ID}", str(d))
    d = client.get("/api/user/profile", headers=h).json()
    check("profile.avatar_url == 上传返回路径", d.get("avatar_url") == f"/api/user/avatar/{USER_ID}", f"avatar_url={d.get('avatar_url')!r}")

    print("=" * 62)
    print("测试 5：公开 GET 头像（无 token）200 + jpeg + nosniff")
    print("=" * 62)
    r = client.get(f"/api/user/avatar/{USER_ID}")
    check("公开头像 200", r.status_code == 200, str(r.status_code))
    check("content-type image/jpeg", r.headers.get("content-type") == "image/jpeg", r.headers.get("content-type"))
    check("nosniff 头", r.headers.get("x-content-type-options") == "nosniff", str(r.headers.get("x-content-type-options")))
    check("头像内容与上传一致", r.content == tiny_jpeg, f"len={len(r.content)} vs {len(tiny_jpeg)}")
    r = client.get(f"/api/user/avatar/{OTHER_ID}")
    check("无头像用户公开 GET → 404", r.status_code == 404, str(r.status_code))

    print("=" * 62)
    print("测试 6：未登录 401 / 空昵称 400 / 超长 400")
    print("=" * 62)
    r = client.get("/api/user/profile")
    check("未登录 GET profile → 401", r.status_code == 401, str(r.status_code))
    r = client.post("/api/user/profile", json={"nickname": "   "}, headers=h)
    check("空昵称 → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = client.post("/api/user/profile", json={"nickname": "一二三四五六七八九十一二三四五六七八九十壹"}, headers=h)
    check("21 字昵称 → 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

    print("=" * 62)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
