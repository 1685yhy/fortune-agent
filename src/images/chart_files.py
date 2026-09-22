# -*- coding: utf-8 -*-
"""命盘图产物 —— 路径 / 归属 / URL 的**单一事实源**（k85 必修1）。

## 为什么需要这个模块

改前 `/share-cards` 是一个 `StaticFiles(directory=CHARTS_DIR)` 的**整目录匿名挂载**，
而 `CHARTS_DIR` 里**混装**两类产物：

  - `share_{reading_id}.png` —— 发布者**主动**分享出去的卡片，**本该公开**；
  - `bazi_{YYYYmmdd_HHMMSS}.png`（还有 `ziwei_*` / `fengshui_*`）——
    **私有命盘图**。文件名是**秒级时间戳 ⇒ 可枚举**，且**无 TTL**。
    `main.py` 原注释"uuid 文件名不可猜测"对该类**不成立**（它不是 uuid）。
    k84 已如实登记、上报拍板；k85 按控制方裁定**收窄挂载面**。

## 本模块确立的口径（三条，全部是"单一事实源"）

1. **公开面**（`public_charts_dir()`）：只允许 `share_*.png`。
   挂载/路由一律经 `is_public_share_card()` 过滤 —— 目录里就算将来又混进别的东西，
   **也不会**被匿名服务出去（第二道闸）。
2. **私有面**（`private_charts_dir()`）：`bazi_*` / `ziwei_*` / `fengshui_*` 一律落这里，
   只经**鉴权路由** `GET /api/charts/{filename}`（`require_user`）暴露。
   默认落在 `CHARTS_DIR` 的**兄弟目录**而不是子目录：部署侧可能对
   `CHARTS_DIR` 挂了 nginx alias（本仓无此配置，但无法从代码里排除），
   **子目录会被同一条 alias 一并命中** ⇒ 收窄挂载面在应用层白做。
3. **归属**：私有图文件名带 16 位 hex 的**归属令牌**
   `{kind}_{YYYYmmdd_HHMMSS}_{owner_token}.{ext}`，
   `owner_token = HMAC-SHA256(secret, user_id)[:16]`。
   路由侧用**已鉴权**的 user_id 重算并比对 ⇒ 真正的**归属校验**，
   且**不可枚举**（不知道 secret 就算不出别人的令牌）。无 DB、无状态。

## 与既有口径的关系

与 `/api/chat/uploads/{filename}` 的"能力式不可猜文件名"同源，但**更强**：
那个是匿名 + 128bit 随机名，这里是**鉴权 + 归属校验**（控制方对本批的要求）。
"""
import hmac
import logging
import os
import re
import secrets
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# 形态（正则即契约：路由与本模块**共用**，不各写一份）
# ══════════════════════════════════════════════════════════════════════

#: 私有命盘图的类别前缀（`{kind}_...`）。新增类别只改这一处。
PRIVATE_CHART_KINDS = ("bazi", "ziwei", "fengshui")

#: 归属令牌长度（16 位 hex = 64 bit；在"离线不可枚举"与"文件名不至于太长"之间取）。
_OWNER_TOKEN_HEX = 16

#: 公开面文件名：**只** `share_{id}.png`。`{id}` 复用分享通道 `_READING_ID_RE`
#: 的字符集（8 位 hex / 咨询数字 ID），再加 `_`/`-` 以容纳 `share_YYYYmmdd_HHMMSS.png`
#: 这种旧式（`share_card.py:318` 的 output_path 缺省名）——**不放宽到任意字符**。
_PUBLIC_SHARE_CARD_RE = re.compile(r"^share_[A-Za-z0-9_-]{1,64}\.png$")

#: 私有面文件名：`{kind}_{YYYYmmdd_HHMMSS}_{16hex}.png|.html`
_PRIVATE_CHART_RE = re.compile(
    r"^(?P<kind>%s)_(?P<ts>\d{8}_\d{6})_(?P<token>[0-9a-f]{%d})\.(?P<ext>png|html)$"
    % ("|".join(PRIVATE_CHART_KINDS), _OWNER_TOKEN_HEX)
)


def is_public_share_card(filename: str) -> bool:
    """该文件名是否是**允许匿名公开**的分享卡（挂载/路由的第二道闸）。

    `Path.name` 语义：传进来的若含路径分隔符一律拒绝（防穿越）。
    """
    name = filename or ""
    if name != Path(name).name or "/" in name or "\\" in name:
        return False
    return bool(_PUBLIC_SHARE_CARD_RE.match(name))


def is_private_chart(filename: str) -> bool:
    """该文件名是否是私有命盘图（`{kind}_{ts}_{token}.{png,html}`）。"""
    name = filename or ""
    if name != Path(name).name or "/" in name or "\\" in name:
        return False
    return bool(_PRIVATE_CHART_RE.match(name))


# ══════════════════════════════════════════════════════════════════════
# 目录
# ══════════════════════════════════════════════════════════════════════

def public_charts_dir() -> Path:
    """公开面目录（env `CHARTS_DIR`，与各生成器既有定义逐字节同源）。

    **只允许放 `share_*.png`**。目录本身的存在/权限口径与改前不变。
    """
    return Path(os.environ.get("CHARTS_DIR", "/opt/fortune-data/charts"))


def private_charts_dir() -> Path:
    """私有面目录（env `PRIVATE_CHARTS_DIR`；缺省 = `CHARTS_DIR` 的**兄弟**目录）。

    刻意**不**选 `CHARTS_DIR / "private"`：见模块 docstring 第 2 条
    （nginx alias 会把子目录一并命中）。缺省落在兄弟目录，则
    `CHARTS_DIR=/opt/fortune-data/charts` → `/opt/fortune-data/charts_private`，
    与公开面**完全分离**。
    """
    raw = (os.environ.get("PRIVATE_CHARTS_DIR") or "").strip()
    if raw:
        return Path(raw)
    return public_charts_dir().parent / "charts_private"


# ══════════════════════════════════════════════════════════════════════
# 归属令牌
# ══════════════════════════════════════════════════════════════════════

_PROCESS_SECRET: Optional[bytes] = None


def _secrets() -> bytes:
    """HMAC 密钥：`CHART_FILE_SECRET` → `JWT_SECRET_KEY` → 进程内随机（并报错）。

    与 `security/auth.py::JWTHandler` 同策略：没配就**不静默**——随机密钥下
    旧 URL 在重启后失效（这是**可接受**的降级：宁可链接失效，不要可猜令牌）。
    """
    global _PROCESS_SECRET
    raw = (os.environ.get("CHART_FILE_SECRET")
           or os.environ.get("JWT_SECRET_KEY") or "").strip()
    if raw:
        return raw.encode("utf-8")
    if _PROCESS_SECRET is None:
        _PROCESS_SECRET = secrets.token_bytes(32)
        logger.error(
            "CHART_FILE_SECRET / JWT_SECRET_KEY 均未设置：私有命盘图的归属令牌"
            "改用**进程内随机密钥**（重启后旧图 URL 全部失效）。生产必须设置其一。")
    return _PROCESS_SECRET


def owner_token(user_id: str) -> str:
    """`user_id` → 16 位 hex 归属令牌（不可逆、不可跨用户伪造）。"""
    return hmac.new(_secrets(), (user_id or "").encode("utf-8"),
                    sha256).hexdigest()[:_OWNER_TOKEN_HEX]


def random_token() -> str:
    """无 user_id 时的占位令牌：**任何已鉴权用户都验不过**（等价于不可达）。"""
    return secrets.token_hex(_OWNER_TOKEN_HEX // 2)


def verify_owner(filename: str, user_id: str) -> bool:
    """私有图是否**属于**该 user_id（路由的归属校验，常量时间比较）。

    非私有图形态 / 无 user_id → 一律 False（fail-closed）。
    """
    if not user_id:
        return False
    m = _PRIVATE_CHART_RE.match(filename or "")
    if not m:
        return False
    return hmac.compare_digest(m.group("token"), owner_token(user_id))


# ══════════════════════════════════════════════════════════════════════
# 私有图路径 / URL
# ══════════════════════════════════════════════════════════════════════

def private_chart_path(kind: str, user_id: str = "",
                       now: Optional[datetime] = None) -> Path:
    """生成一张私有命盘图的落盘路径（并确保目录存在）。

    `user_id` 为空 ⇒ 用随机令牌（图仍在私有目录、仍不可匿名取，但**没有**
    任何账号能通过归属校验 —— 与"匿名调用者本就不该拿到这张图"一致）。
    """
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    token = owner_token(user_id) if user_id else random_token()
    d = private_charts_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{kind}_{ts}_{token}.png"


def private_chart_url(filename: str) -> str:
    """私有图的**鉴权路由** URL（对外域名走 `config.public_client_base()`）。

    域名口径与 TTS / 夜间语音一致：`PUBLIC_BASE_URL` 是回环时回落到生产域名
    （否则真机不可达 —— k61 r3 的产品 bug 形态）。
    """
    from src.config import public_client_base
    return f"{public_client_base()}/api/charts/{filename}"


def public_share_card_url(filename: str) -> str:
    """公开分享卡的 URL（`/share-cards/{name}`，匿名可读）。"""
    from src.config import public_client_base
    return f"{public_client_base()}/share-cards/{filename}"


def reply_chart_url(chart_path: str, user_id: str) -> str:
    """已生成的私有命盘图 → 回复文本里可下发的 URL（不可下发时返回**空串**）。

    两个"不发"的判据（都是**死链**，发了只会让用户点到一个 404）：

    · 非 `.png`：`Playwright` 缺失/渲染失败时 `generate()` 会退回 `.html`
      （正常路径下 `.html` 渲染成功即被删，见 `_render_html_to_png` 末行），
      而私有路由**故意只发 .png**（`.html` 是模板产物、可含未转义文本，
      发出去就是"本站域名下的服务端生成 HTML" = XSS 通路，
      见 `main.py::get_private_chart` ②与 `bazi_chart_html.generate` ③）；
    · 无 `user_id`：文件名是**随机令牌**，任何账号都过不了归属校验。

    改前这里发的是 `http://124.221.233.214/charts/{filename}` —— **硬编码 IP +
    本仓从未挂载的 `/charts/` 路径**（k85 实测生产 404，是**死链**）。
    """
    if not user_id or not chart_path:
        return ""
    name = os.path.basename(chart_path)
    if not name.endswith(".png") or not is_private_chart(name):
        return ""
    return private_chart_url(name)


def purge_private_charts(user_id: str, private_dir=None, failures=None) -> int:
    """注销/账号清除时删除该用户的**私有命盘图**，返回删除数。

    归属判据 = 文件名里的归属令牌（`verify_owner`）—— 与路由**同一个**判据，
    不另写一份。令牌算不出来的（别的用户 / 无主随机令牌）一律不动。
    """
    if not user_id:
        return 0
    d = Path(private_dir) if private_dir else private_charts_dir()
    try:
        if not d.is_dir():
            return 0
        names = [p for p in d.iterdir() if p.is_file() and verify_owner(p.name, user_id)]
    except Exception as e:
        logger.warning("私有命盘图目录不可读 %s: %s", d, e)
        if failures is not None:
            failures.append({"kind": "chart_files", "path": str(d),
                             "error": f"{type(e).__name__}: {e}"})
        return 0
    from src.storage.dao import _purge_dir_files
    return _purge_dir_files(names, "私有命盘图", failures=failures)
