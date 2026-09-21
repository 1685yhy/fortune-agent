# -*- coding: utf-8 -*-
"""k72：上传内容校验统一（魔数嗅探单一事实源）。

背景（复审实证，非推测）：本服务两个接图片的端点校验强度不一致——
  - `/api/chat/upload`：content-type 白名单 **+ 魔数嗅探**；
  - `/api/user/avatar`：**只查 content-type 与后缀**，不查内容。
实测（隔离实例、原始请求响应）：把 MP3 改名 `x.png`、声明 `image/png`
打给 `/api/user/avatar` → `HTTP 200 {"success":true}`，落盘文件头 8 字节为
`49 44 33` = `"ID3"`（音频）。

本文件守护四件事：
1. 嗅探实现**单一事实源**——两个端点 import 的是同一个函数对象，且
   `src/main.py` 不再自带一份 `_sniff_image_ext` 副本（该副本正是漂移成因）；
2. `/api/user/avatar` 补上内容校验：**伪装 → 拒**，且拒时不落盘；
3. **正向对照**：真图片仍 200 并正常落盘（证明不是一刀切误伤）；
4. 派生白名单与改动前的字面量**逐项相等**（不许顺手放宽/收窄受理面）。

隔离要求：DB 与头像目录一律 tmp_path（不碰生产库）。
"""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# 必须在导入 src.main（app）之前设置：AuthHandler/JWTHandler 从环境读密钥
os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.api import user as user_api  # noqa: E402
from src.security.auth import AuthHandler, set_auth_handler  # noqa: E402
from src.utils import image_sniff  # noqa: E402

# ── 测试素材 ────────────────────────────────────────────────────────
# 复审实测样本：真 MP3（ID3v2 头）——落盘后头 8 字节即 "ID3"
MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 118
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 120
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 120
GIF_BYTES = b"GIF89a" + b"\x00" * 120


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """隔离实例：tmp_path 建库 + AVATAR_DIR/上传目录全部指向 tmp_path。"""
    db = str(tmp_path / "k72.db")
    udao = UserDAO(db)
    auth = AuthHandler()
    user_api.setup(udao, None, auth)
    set_auth_handler(auth)
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.setenv("FORTUNE_UPLOADS_DIR", str(tmp_path / "uploads"))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.create_user_token('k72-user')}"}
    return client, headers, tmp_path


# ══════════════════════════════════════════════════════════════════
# 一、单一事实源：两端点共用同一实现，且不再各有副本
# ══════════════════════════════════════════════════════════════════

def test_sniff_is_single_source_of_truth():
    """两个上传端点 import 的必须是**同一个函数对象**（不是两份拷贝）。"""
    assert main_module.sniff_image_ext is image_sniff.sniff_image_ext
    assert main_module.sniff_image_format is image_sniff.sniff_image_format


def test_no_local_duplicate_sniff_remains():
    """`src/main.py` 不得再自带一份魔数判断副本——副本正是两处漂移的成因。"""
    assert not hasattr(main_module, "_sniff_image_ext"), (
        "src/main.py 又出现了本地 _sniff_image_ext 副本，请改为 import 单一事实源"
    )
    src = (PROJECT_DIR / "src" / "main.py").read_text(encoding="utf-8")
    assert "def _sniff_image_ext" not in src, "魔数判断逻辑在 main.py 被复制了一份"
    # 头像端点所在文件同样不得内联魔数判断
    user_src = (PROJECT_DIR / "src" / "api" / "user.py").read_text(encoding="utf-8")
    assert "def _sniff_image_ext" not in user_src
    assert b"\xff\xd8\xff".decode("latin-1") not in user_src, "头像端点内联了魔数判断"


def test_chat_whitelist_derived_from_single_source():
    """chat 的 content-type→扩展名表由单一事实源派生，与改动前逐项相等。"""
    assert main_module._CHAT_UPLOAD_EXT == {
        "image/jpeg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif",
    }


def test_avatar_whitelists_derived_and_unchanged():
    """头像三张表由单一事实源派生，且**受理面与改动前完全一致**（未放宽/未收窄）。"""
    assert user_api._ALLOWED_AVATAR_TYPES == {"image/jpeg", "image/png", "image/webp"}
    assert user_api._ALLOWED_AVATAR_EXTS == {".jpg", ".jpeg", ".png", ".webp"}
    assert user_api._ALLOWED_AVATAR_FORMATS == {"jpeg", "png", "webp"}
    # 头像历来不收 gif，改动后依然不收（内容格式表不得混入 gif）
    assert "gif" not in user_api._ALLOWED_AVATAR_FORMATS
    assert ".gif" not in user_api._ALLOWED_AVATAR_EXTS


# ══════════════════════════════════════════════════════════════════
# 二、嗅探本体：认得出什么、认不出什么
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("data,expected", [
    (JPEG_BYTES, "jpeg"),
    (PNG_BYTES, "png"),
    (WEBP_BYTES, "webp"),
    (GIF_BYTES, "gif"),
    # 复审实测样本：音频不得被认成图片
    (MP3_BYTES, ""),
    # 非图片 / 空 / 极短（不足魔数长度，不得越界）
    (b"not really an image at all", ""),
    (b"", ""),
    (b"\xff", ""),
    (b"\x89PNG", ""),
    (b"RIFF\x00\x00\x00\x00XXXX", ""),
])
def test_sniff_image_format(data, expected):
    assert image_sniff.sniff_image_format(data) == expected


def test_sniff_image_ext_maps_to_canonical_ext():
    assert image_sniff.sniff_image_ext(JPEG_BYTES) == ".jpg"
    assert image_sniff.sniff_image_ext(PNG_BYTES) == ".png"
    assert image_sniff.sniff_image_ext(WEBP_BYTES) == ".webp"
    assert image_sniff.sniff_image_ext(GIF_BYTES) == ".gif"
    assert image_sniff.sniff_image_ext(MP3_BYTES) == ""


# ══════════════════════════════════════════════════════════════════
# 三、/api/user/avatar：伪装 → 拒（复审原始复现）+ 拒时不落盘
# ══════════════════════════════════════════════════════════════════

def _post_avatar(client, headers, data, fname="x.png", ctype="image/png"):
    return client.post(
        "/api/user/avatar",
        files={"file": (fname, data, ctype)},
        headers=headers,
    )


def test_avatar_rejects_mp3_renamed_as_png(api_env):
    """复审原始复现：MP3 改名 x.png + 声明 image/png → 必须被拒（原为 200）。"""
    client, headers, tmp_path = api_env
    r = _post_avatar(client, headers, MP3_BYTES, fname="x.png", ctype="image/png")
    assert r.status_code == 400, (
        f"音频伪装成 PNG 未被拒（HTTP {r.status_code}）——内容校验缺失"
    )
    # 拒绝必须**同时**不落盘（不能先写后校验）
    avatars = tmp_path / "avatars"
    assert not avatars.exists() or list(avatars.glob("*")) == [], "伪装文件被写入了磁盘"


def test_avatar_rejects_mp3_named_as_jpg(api_env):
    """同类伪装换后缀/换声明再打一遍：jpg 路径同样必须拒。"""
    client, headers, _ = api_env
    r = _post_avatar(client, headers, MP3_BYTES, fname="x.jpg", ctype="image/jpeg")
    assert r.status_code == 400


def test_avatar_rejects_non_image_claiming_to_be_image(api_env):
    client, headers, _ = api_env
    r = _post_avatar(client, headers, b"just plain text, no magic", "a.webp", "image/webp")
    assert r.status_code == 400


def test_avatar_still_rejects_disallowed_declared_type(api_env):
    """既有断言不许放宽：声明类型不在白名单 → 400（与改动前一致）。"""
    client, headers, _ = api_env
    r = _post_avatar(client, headers, PNG_BYTES, "x.bmp", "image/bmp")
    assert r.status_code == 400


def test_avatar_still_rejects_gif(api_env):
    """既有受理面不许放宽：真 GIF 内容也不收（头像历来只收 jpg/png/webp）。"""
    client, headers, _ = api_env
    r = _post_avatar(client, headers, GIF_BYTES, "x.gif", "image/gif")
    assert r.status_code == 400


def test_avatar_requires_auth(api_env):
    client, _, _ = api_env
    assert _post_avatar(client, None, PNG_BYTES).status_code == 401


@pytest.mark.parametrize("data,fname,ctype,expect_magic", [
    (JPEG_BYTES, "me.jpg", "image/jpeg", b"\xff\xd8\xff"),
    (PNG_BYTES, "me.png", "image/png", b"\x89PNG\r\n\x1a\n"),
    (WEBP_BYTES, "me.webp", "image/webp", b"RIFF"),
])
def test_avatar_accepts_real_images(api_env, data, fname, ctype, expect_magic):
    """**正向对照**：真图片必须 200 且正常落盘（证明内容校验没有误伤）。"""
    client, headers, tmp_path = api_env
    r = _post_avatar(client, headers, data, fname=fname, ctype=ctype)
    assert r.status_code == 200, f"真图片被误拒: {r.status_code} {r.text}"
    assert r.json()["success"] is True
    saved = list((tmp_path / "avatars").glob("*.jpg"))
    assert len(saved) == 1, "真图片未落盘"
    assert saved[0].read_bytes()[:len(expect_magic)] == expect_magic


def test_avatar_jpeg_alias_suffix_accepted(api_env):
    """`.jpeg` 别名后缀（既有白名单内）不得因本次改动被误拒。"""
    client, headers, _ = api_env
    r = _post_avatar(client, headers, JPEG_BYTES, "me.jpeg", "image/jpeg")
    assert r.status_code == 200, r.text


# ══════════════════════════════════════════════════════════════════
# 四、/api/chat/upload：重构后行为零变化（回归守护）
# ══════════════════════════════════════════════════════════════════

def _post_chat(client, headers, data, fname="photo.jpg", ctype="image/jpeg"):
    return client.post(
        "/api/chat/upload", files={"file": (fname, data, ctype)}, headers=headers
    )


def test_chat_upload_still_accepts_real_image(api_env):
    client, headers, _ = api_env
    r = _post_chat(client, headers, PNG_BYTES, "photo.png", "image/png")
    assert r.status_code == 200, r.text


def test_chat_upload_still_rejects_disguised_audio(api_env):
    """chat 端点原本就有嗅探——重构后不得失效。"""
    client, headers, _ = api_env
    r = _post_chat(client, headers, MP3_BYTES, "x.png", "image/png")
    assert r.status_code == 415


def test_both_endpoints_reject_the_same_disguised_bytes(api_env):
    """同一份伪装字节，两个端点必须给出同样"拒"的结论（一致性本身是被守护的契约）。"""
    client, headers, _ = api_env
    chat_r = _post_chat(client, headers, MP3_BYTES, "x.png", "image/png")
    avatar_r = _post_avatar(client, headers, MP3_BYTES, "x.png", "image/png")
    assert chat_r.status_code >= 400, "chat 端点放行了伪装音频"
    assert avatar_r.status_code >= 400, "avatar 端点放行了伪装音频"


# ══════════════════════════════════════════════════════════════════
# 五、面相互/手相端点：原本**完全不校验**上传内容，现补嗅探
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def face_env(api_env, monkeypatch):
    """面相互/手相端点先过 `handler is None` 守卫，须注入非 None 占位。

    用哑对象即可：本组用例只验证"内容校验这一关"，走到 image.read() 时
    校验先于 CV/LLM 发生。
    """
    monkeypatch.setattr(main_module, "handler", object())
    return api_env


@pytest.mark.parametrize("path", ["/api/face-reading", "/api/palm-reading"])
def test_face_palm_reject_non_image(face_env, path):
    client, headers, _ = face_env
    # 这两个端点的表单字段名是 image（非 chat 的 file），与端点签名一致
    r = client.post(
        path, files={"image": ("x.jpg", MP3_BYTES, "image/jpeg")}, headers=headers
    )
    assert r.status_code == 415, f"{path} 未拒绝伪装音频（HTTP {r.status_code}）"


@pytest.mark.parametrize("path", ["/api/face-reading", "/api/palm-reading"])
def test_face_palm_do_not_reject_real_image(face_env, path):
    """正向对照：真图片不得被内容校验拦下（应继续往下走 CV 流程）。"""
    client, headers, _ = face_env
    r = client.post(
        path, files={"image": ("p.png", PNG_BYTES, "image/png")}, headers=headers
    )
    assert r.status_code != 415, f"{path} 误拒了真图片: {r.text[:200]}"


@pytest.mark.parametrize("path", ["/api/face-reading", "/api/palm-reading"])
def test_face_palm_require_auth(face_env, path):
    client, _, _ = face_env
    r = client.post(path, files={"image": ("p.png", PNG_BYTES, "image/png")})
    assert r.status_code == 401
