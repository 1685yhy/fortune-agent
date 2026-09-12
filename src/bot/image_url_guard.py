"""对话图片 URL 白名单（k33/A23）——SSRF 面收口（白名单制，非黑名单）。

背景：`handler._try_face_reading` / `_try_palm_reading` 对客户端传来的
image_url 执行下载 —— 任意 URL 都会被服务端拉取（SSRF：内网服务 /
云元数据 169.254.169.254 / 本机其他端口任意路径）。

修法 = **白名单 + 本机地址默认拒绝 + 规范化比对 + 重定向复检**
（黑名单挡不住 127.0.0.1、0.0.0.0、十进制 IP、DNS 重绑定等变体）：

- **self_hosts（本服务自有域名）**：`PUBLIC_BASE_URL`（config.public_base_url）
  的域名 + 默认生产域名 `yilichat.com`；且要求 URL 路径以
  `/api/chat/uploads/` 开头——本服务唯一的用户图片来源。
- **显式主机名单（`CHAT_IMAGE_ALLOWED_HOSTS`）**：自有 CDN / 多域名部署 /
  开发预览机。公网域名（含 CDN）命中即放行（路径不限，由运营方负责）；
  **回环 / 私有网段 / 链路本地地址必须带端口精确命中**（如 `192.168.0.104:8767`、
  `127.0.0.1:8767`），且仍需上传路径前缀——避免"放开一台本机主机的任意端口"。
- **本机地址默认拒绝（k33 审查 I2）**：loopback（127.0.0.0/8、::1）/ 私有
  （10/8、172.16/12、192.168/16、fc00::/7）/ 链路本地（169.254/16、fe80::/10）/
  未指定（0.0.0.0）/ CGNAT（100.64/10）等一律拒绝，**即使它来自
  `PUBLIC_BASE_URL`**——生产曾把 `PUBLIC_BASE_URL` 设为 `http://127.0.0.1:8768`
  （TTS 内部口径），旧实现据此把 127.0.0.1 放进了白名单，等于开放本机全端口
  探测面。开发预览要走显式名单（带端口）。
- **规范化后比对（k33 审查 I2）**：路径逐级 percent-decode + normpath 归一
  （防 `/api/chat/uploads/../../../admin`、`%2e%2e`、二次编码穿越）；
  host 一律小写、拒绝带 `%` 的编码 host（防 `127%2e0%2e0%2e1`）；
  IP 字面量支持十进制/八进制/十六进制/短写（`127.1`、`2130706433`、`0x7f000001`）
  与 IPv6 及 IPv4-mapped 形态；带用户名口令（`u:p@host`）一律拒绝。
- **重定向复检（k33 审查 I2）**：`safe_urlretrieve` 用自定义 opener 替换裸
  `urlretrieve`——每次 302/301 目标都重新过白名单，跨主机跳转到内网/未授权
  主机一律拒绝（不再"先信任再跳转"）。

拒绝：非 http(s)（file:// / gopher:// / data: 等）、带用户名口令、空 host、
白名单外 host、本机地址、规范化后逃出上传目录的路径。

部署新域名时：把域名加进 `CHAT_IMAGE_ALLOWED_HOSTS`（或把 `PUBLIC_BASE_URL`
指向该域名），无需改代码。
"""
import ipaddress
import logging
import posixpath
import shutil
import socket
import urllib.error
import urllib.request
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# 本服务用户图片的唯一合法路径前缀（main.py 的 /api/chat/upload 落盘 + 静态回读）
CHAT_UPLOADS_PATH_PREFIX = "/api/chat/uploads/"
# config.public_base_url() 的默认生产域名（与 src/config.py 同值，单一事实源
# 仍在该函数；此处仅作默认白名单项，env 覆盖时不冲突——两者并集）
DEFAULT_SELF_HOST = "yilichat.com"
_ALLOWED_SCHEMES = ("http", "https")
# 本机地址判定：显式保留段（除 is_private/is_loopback/is_link_local 外的边角）
_EXTRA_BLOCKED_V4 = (
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT（运营商内网）
    ipaddress.ip_network("192.0.0.0/24"),     # IETF 协议专用
    ipaddress.ip_network("198.18.0.0/15"),    # 基准测试网段
    ipaddress.ip_network("192.0.2.0/24"),     # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
# 本机别名后缀（RFC 6761 / mDNS / 内网约定）：一并拒绝
_LOCAL_NAME_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


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


def _port_of(value: str):
    """从 URL 或裸 host 提取端口；缺省/非法 → None。"""
    v = (value or "").strip()
    if not v:
        return None
    if "//" not in v:
        v = "//" + v
    try:
        return urlparse(v).port
    except ValueError:
        return None


def _allowlisted_host_port(entry: str):
    """显式名单条目 → (host, port)；port None = 未指定（仅对公网域名有效）。"""
    h = _host_of(entry)
    if not h:
        return "", None
    return h, _port_of(entry)


def _ip_literal(host: str):
    """host 若为 IP 字面量 → ip_address 对象（含十进制/十六进制/短写/IPv6）；否则 None。"""
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # 非标准 IPv4 写法（"127.1" / "2130706433" / "0x7f000001" / 八进制）
    try:
        packed = socket.inet_aton(host)
    except (OSError, ValueError):
        return None
    try:
        return ipaddress.ip_address(packed)
    except ValueError:
        return None


def _is_non_public_ip(ip) -> bool:
    """IP 是否属于「绝不能作为图片源」的地址（回环/私网/链路本地/保留/组播）。"""
    if ip.version == 6 and getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 → 127.0.0.1
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified:
        return True
    if ip.is_multicast or ip.is_reserved:
        return True
    if getattr(ip, "is_site_local", False):  # IPv6 ULA fc00::/7
        return True
    if ip.version == 4:
        for net in _EXTRA_BLOCKED_V4:
            if ip in net:
                return True
    return False


def is_local_or_private_host(host: str) -> bool:
    """host 是否「本机/内网」地址（IP 字面量、localhost 别名、无点单标签名）。"""
    h = (host or "").strip().lower().rstrip(".") or ""
    if not h:
        return True  # 空 host 按最保守处理
    ip = _ip_literal(h)
    if ip is not None:
        return _is_non_public_ip(ip)
    if h == "localhost" or h.endswith(_LOCAL_NAME_SUFFIXES):
        return True
    if "." not in h:
        return True  # 单标签名（intranet 之类）不可能是公网域名
    return False


def self_hosts() -> set:
    """本服务自有域名集合（要求上传路径前缀；本机地址由默认拒绝规则兜底）。"""
    hosts = {DEFAULT_SELF_HOST}
    try:
        from src.config import public_base_url
        base_host = _host_of(public_base_url())
        if base_host:
            hosts.add(base_host)
    except Exception as e:  # 配置读取失败不放大：默认域名仍生效
        logger.warning("image_url_guard: 读取 public_base_url 失败: %s", e)
    return hosts


def allowed_host_entries() -> set:
    """显式主机名单（`CHAT_IMAGE_ALLOWED_HOSTS`，逗号分隔，`host` 或 `host:port`）。"""
    import os
    raw = os.environ.get("CHAT_IMAGE_ALLOWED_HOSTS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def cdn_hosts() -> set:
    """显式名单中的 host 集合（供日志/排查与兼容断言；判定见 _allowed_by_explicit）。"""
    return {h for h, _p in (_allowlisted_host_port(x)
                            for x in allowed_host_entries()) if h}


def allowed_image_hosts() -> set:
    """全部白名单域名（self_hosts ∪ 显式名单）——供日志/排查与测试断言。"""
    return self_hosts() | cdn_hosts()


def _normalize_path(path: str) -> str:
    """路径归一：逐级 percent-decode + posix 归一（防 `..` / 二次编码穿越）。

    返回归一化路径；含 NUL / 反斜杠（部分中间件按目录分隔符解释）→ ""（拒绝）。
    """
    raw = path or "/"
    for _ in range(3):  # 多级解码（%252e%252e → %2e%2e → ..）
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    if "\x00" in raw or "\\" in raw:
        return ""
    return posixpath.normpath(raw)


def _effective_port(u, scheme: str):
    """URL 实际端口（未显式写端口 → 按 scheme 默认 80/443）。"""
    return u.port if u.port is not None else (443 if scheme == "https" else 80)


def _allowed_by_explicit(host: str, port) -> bool:
    """显式名单命中判定。

    - 公网域名：条目 `host`（不带端口）或 `host:port` 命中即放行（路径由运营方负责）；
    - 本机/内网地址：条目**必须带端口且与 URL 端口精确相等**——显式名单是
      "明确到端口"的授权，绝不放任本机任意端口（k33 审查 I2）。
    """
    local = is_local_or_private_host(host)
    for entry in allowed_host_entries():
        e_host, e_port = _allowlisted_host_port(entry)
        if e_host != host:
            continue
        if e_port is None:
            if not local:
                return True  # 公网域名条目不带端口 = 该域名全端口
            continue        # 本机地址必须显式到端口
        if e_port == port:
            return True
    return False


def _is_allowed_url(u, host: str) -> bool:
    scheme = (u.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False
    if u.username or u.password:
        return False  # "http://a@b/" 形态歧义：一律拒绝
    if not host:
        return False
    if "%" in host:
        return False  # 编码 host（127%2e0%2e0%2e1）一律不认
    port = _effective_port(u, scheme)
    if is_local_or_private_host(host):
        # 本机/内网地址：仅显式名单（且端口精确）可放行；且仍限上传路径
        if not _allowed_by_explicit(host, port):
            return False
        return _normalize_path(u.path).startswith(CHAT_UPLOADS_PATH_PREFIX)
    if host in self_hosts():
        return _normalize_path(u.path).startswith(CHAT_UPLOADS_PATH_PREFIX)
    if _allowed_by_explicit(host, port):
        return True
    return False


def is_allowed_image_url(url: str) -> bool:
    """白名单判定：可被服务端下载的图片 URL 才返回 True。"""
    if not url or not isinstance(url, str):
        return False
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return _is_allowed_url(u, host)


def reject_reason(url: str) -> str:
    """拒绝原因（日志/排查用，不含完整 URL 以免日志注入）。"""
    try:
        u = urlparse((url or "").strip())
        return (f"scheme={u.scheme!r} host={u.hostname!r} "
                f"port={u.port!r} path_len={len(u.path or '')}")
    except Exception:
        return "unparseable"


class _WhitelistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向复检：每个跳转目标都重新过白名单（防 302 → 内网/未授权主机）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_image_url(newurl):
            raise urllib.error.HTTPError(
                req.full_url, code,
                f"redirect target not allowed ({reject_reason(newurl)})",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_IMAGE_OPENER = None


def _image_opener():
    """带重定向复检的 opener（进程级复用；默认 handler 全集 + 自定义跳转校验）。"""
    global _IMAGE_OPENER
    if _IMAGE_OPENER is None:
        _IMAGE_OPENER = urllib.request.build_opener(_WhitelistRedirectHandler())
    return _IMAGE_OPENER


def safe_urlretrieve(url: str, filename: str, timeout: float = 20.0) -> str:
    """白名单内 URL 下载（替代裸 `urlretrieve`：初始 URL + 每次重定向都校验）。

    非白名单 URL → 直接抛 ValueError（不发起任何连接）；跨主机重定向到
    非白名单目标 → 抛 HTTPError。返回落盘路径。
    """
    if not is_allowed_image_url(url):
        raise ValueError(f"image url not allowed: {reject_reason(url)}")
    resp = _image_opener().open(url, timeout=timeout)
    try:
        with open(filename, "wb") as f:
            shutil.copyfileobj(resp, f)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return filename
