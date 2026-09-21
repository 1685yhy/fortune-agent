"""图片上传内容校验 — 魔数嗅探的**单一事实源**（k72）。

背景（k72 复审实证）：本服务原有两个接图片的端点，校验强度不一致——
  - `/api/chat/upload`：content-type 白名单 **+ 魔数嗅探**（内容不是真图片则 415）；
  - `/api/user/avatar`：**只查 content-type 与文件后缀**，不查内容。
后果（实测）：把 MP3 改名成 x.png、声明 `image/png` 打给 `/api/user/avatar`
→ HTTP 200 并落盘，落盘文件头 8 字节是 `ID3`（音频）。即"宣称的类型"与
"真实内容"可以完全脱节。

修法：把嗅探与「声明类型/后缀」的对应关系下沉到本模块，`src/main.py`
（chat/face/palm 上传）与 `src/api/user.py`（头像上传）**共同 import**，
不再各留一份判断逻辑——避免两个端点再次漂移（同类问题只修一处是本次
复审点名的失败模式）。

本模块零依赖（纯 bytes 运算），可被任意层安全导入。
"""

# ── 单一事实源：声明类型 → 允许的嗅探格式 ──────────────────────────
# content-type 是客户端**自述**，只能当"允许集合"的入口条件，
# 绝不可当作内容为真的证据（必须再过 sniff）。
IMAGE_CONTENT_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

# 嗅探格式 → 落盘规范扩展名（服务端自己生成文件名时用）
IMAGE_FORMAT_EXT = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "gif": ".gif",
}

# 客户端文件名后缀 → 嗅探格式（区别于 content-type，两者都要过关）
IMAGE_EXT_FORMAT = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
    ".gif": "gif",
}


def sniff_image_format(data: bytes) -> str:
    """魔数嗅探真实图片格式。

    返回 `"jpeg"` / `"png"` / `"webp"` / `"gif"`；非图片或数据不足返回 `""`。

    只认文件头魔数，与客户端自述的 content-type / 后缀无关（这正是要点：
    伪装者改得了声明，改不了自己文件开头的字节）。
    """
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def sniff_image_ext(data: bytes) -> str:
    """魔数嗅探 → 规范扩展名（`.jpg`/`.png`/`.webp`/`.gif`）；非图片返回 `""`。"""
    return IMAGE_FORMAT_EXT.get(sniff_image_format(data), "")
