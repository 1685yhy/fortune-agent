"""B4-1: 对话图片上传端点（/api/chat/upload + /api/chat/uploads/{file}）。

覆盖：鉴权 401 / 类型白名单 / 魔数嗅探 / 大小超限 / 成功落盘与回读 /
路径安全（客户端文件名忽略、GET 穿越拒绝）。
"""
import pytest

from starlette.testclient import TestClient

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128
MAX = 5 * 1024 * 1024


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """注入独立临时上传目录 + 假 token 的 TestClient 环境。

    上传目录经 FORTUNE_UPLOADS_DIR 注入（main 惰性解析，无需重导模块）。
    """
    monkeypatch.setenv("FORTUNE_UPLOADS_DIR", str(tmp_path / "uploads"))
    from src.main import app
    from src.security.auth import AuthHandler, set_auth_handler

    _auth = AuthHandler()
    set_auth_handler(_auth)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {_auth.create_user_token('upload-test-user')}"}
    return client, h, tmp_path


def _post(client, headers, fname="photo.jpg", data=JPEG_BYTES, ctype="image/jpeg"):
    return client.post(
        "/api/chat/upload",
        files={"image": (fname, data, ctype)},
        headers=headers,
    )


def test_upload_requires_auth(api_env):
    client, _, _ = api_env
    r = _post(client, None)
    assert r.status_code == 401


def test_upload_type_whitelist(api_env):
    client, h, _ = api_env
    r = _post(client, h, fname="note.txt", data=b"hello", ctype="text/plain")
    assert r.status_code == 415


def test_upload_sniff_rejects_fake_image(api_env):
    client, h, _ = api_env
    # content-type 是 jpeg 但内容不是图片 → 魔数嗅探拒绝
    r = _post(client, h, data=b"not really an image at all")
    assert r.status_code == 415


def test_upload_size_limit(api_env):
    client, h, _ = api_env
    r = _post(client, h, fname="big.jpg", data=JPEG_BYTES + b"\x00" * MAX)
    assert r.status_code == 413


def test_upload_success_returns_url_and_get_back(api_env):
    client, h, tmp_path = api_env
    # 客户端文件名带路径穿越成分 → 必须被忽略（url 是服务端 uuid 名）
    r = _post(client, h, fname="../../etc/passwd.jpg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["url"].startswith("http://")
    assert "/api/chat/uploads/" in body["url"]
    fname = body["url"].rsplit("/", 1)[1]
    assert ".." not in fname
    # 文件确实落在上传目录（而非穿越路径）
    assert (tmp_path / "uploads" / fname).is_file()
    # 静态回读
    g = client.get("/api/chat/uploads/" + fname)
    assert g.status_code == 200
    assert g.content[:3] == JPEG_BYTES[:3]


def test_upload_get_path_traversal_rejected(api_env):
    client, _, _ = api_env
    for bad in [
        "/api/chat/uploads/..%2F..%2Fetc%2Fpasswd",
        "/api/chat/uploads/..",
        "/api/chat/uploads/a%2Fb.jpg",
    ]:
        r = client.get(bad)
        assert r.status_code != 200, f"路径穿越未拦截: {bad}"


def test_upload_get_missing_file(api_env):
    client, _, _ = api_env
    assert client.get("/api/chat/uploads/no-such-file.jpg").status_code == 404
