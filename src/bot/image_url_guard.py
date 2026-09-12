"""对话图片 URL 白名单（k33/A23）——SSRF 面收口（白名单制，非黑名单）。

背景：`handler._try_face_reading` / `_try_palm_reading` 对客户端传来的
image_url 执行 `urllib.request.urlretrieve` —— 任意 URL 都会被服务端拉取
（SSRF：内网服务 / 云元数据 169.254.169.254 / 本机其他端口任意路径）。

修法 = **白名单**（黑名单挡不住 127.0.0.1、0.0.0.0、十进制 IP、DNS 重绑定等变体）：

- **self_hosts（本服务自有域名）**：`PUBLIC_BASE_URL`（config.public_base_url）
  的域名 + 默认生产域名 `config.public_base_url()` 的默认值（yilichat.com）；
  且要求 URL 路径以 `/api/chat/uploads/` 开头——本服务唯一的用户图片来源，
  顺带把「本机其他端口的任意路径」挡在门外（自有域名 + 任意路径 = 内网跳板面）。
- **cdn_hosts（自有 CDN / 多域名部署）**：环境变量 `CHAT_IMAGE_ALLOWED_HOSTS`
  （逗号分隔，可写成 `host` 或 `https://host`），命中即放行（路径不限，由运营
  方对 CDN 内容负责）。

拒绝：非 http(s)（file:// / gopher:// / data: 等）、带用户名口令（`u:p@host`
钓鱼式歧义）、空 host、白名单外的 host、self host 但路径不是上传路径。
域名比对大小写不敏感、忽略端口（端口由 self_hosts 的路径约束与 CDN 配置兜底）。

部署新域名时：把域名加进 `CHAT_IMAGE_ALLOWED_HOSTS`（或把 `PUBLIC_BASE_URL`
指向该域名），无需改代码。
"""
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 本服务用户图片的唯一合法路径前缀（main.py 的 /api/chat/upload 落盘 + 静态回读）
CHAT_UPLOADS_PATH_PREFIX = "/api/chat/uploads/"
# config.public_base_url() 的默认生产域名（与 src/config.py 同值，单一事实源
# 仍在该函数；此处仅作默认白名单项，env 覆盖时不冲突——两者并集）
DEFAULT_SELF_HOST = "yilichat.com"
_ALLOWED_SCHEMES = ("http", "https")


def _host_of(value: str) -> str:
    """从 URL 或裸 host（可带端口）提取小写 hostname；空/非法 → ""。"""
    v = (value or "").strip()
    if not v:
        return ""
    if "//" not in v:
        v = "//" + v
    try:
        return (urlparse(v).hostname or "").lower()
    except ValueError:
        return ""


def self_hosts() -> set:
    """本服务自有域名集合（要求上传路径前缀）。"""
    hosts = {DEFAULT_SELF_HOST}
    try:
        from src.config import public_base_url
        base_host = _host_of(public_base_url())
        if base_host:
            hosts.add(base_host)
    except Exception as e:  # 配置读取失败不放大：默认域名仍生效
        logger.warning("image_url_guard: 读取 public_base_url 失败: %s", e)
    return hosts


def cdn_hosts() -> set:
    """自有 CDN / 多域名部署白名单（`CHAT_IMAGE_ALLOWED_HOSTS`，逗号分隔）。"""
    import os
    raw = os.environ.get("CHAT_IMAGE_ALLOWED_HOSTS", "")
    return {h for h in (_host_of(x) for x in raw.split(",")) if h}


def allowed_image_hosts() -> set:
    """全部白名单域名（self_hosts ∪ cdn_hosts）——供日志/排查与测试断言。"""
    return self_hosts() | cdn_hosts()


def is_allowed_image_url(url: str) -> bool:
    """白名单判定：可被服务端下载的图片 URL 才返回 True。"""
    if not url or not isinstance(url, str):
        return False
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    if u.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if u.username or u.password:
        return False  # "http://a@b/" 形态歧义：一律拒绝
    host = (u.hostname or "").lower()
    if not host:
        return False
    if host in cdn_hosts():
        return True  # 自有 CDN：路径由运营方配置
    if host in self_hosts():
        return (u.path or "").startswith(CHAT_UPLOADS_PATH_PREFIX)
    return False


def reject_reason(url: str) -> str:
    """拒绝原因（日志/排查用，不含完整 URL 以免日志注入）。"""
    try:
        u = urlparse((url or "").strip())
        return f"scheme={u.scheme!r} host={u.hostname!r} path_len={len(u.path or '')}"
    except Exception:
        return "unparseable"
