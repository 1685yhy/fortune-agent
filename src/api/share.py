"""分享卡 API — 报告分享卡片（命书摘要 + 用户昵称 + 二维码占位）。

Provides:
  GET /api/share/{report_id} → {imageUrl, card}
      - report_id 为咨询 ID（数字）→ 从 consultations 加载，owner 校验：
          不存在 404 / 非本人 403（IDOR 防护）
      - report_id 为 reading_id（8 位）→ 兼容旧版 data/reports JSON 报告：
          不存在 404（k76 起**同样做归属校验**，非本人/归属未知 403）
      优先尝试生成真实分享图（ShareCardGenerator，依赖 playwright + CHARTS_DIR）；
      环境未就绪时降级返回结构化 card 数据（标题/摘要/样式/昵称/二维码占位），
      由前端自行渲染分享。
  GET /share/{reading_id} → **分享通道**（匿名可读的报告分享页）
      k76：公开页剥离个人信息（姓名/出生日期），就地渲染报告，不再跳转到
      需鉴权的 /report/{reading_id}（本人路径）。
  POST /api/share → 匿名对话分享落库（有有效期，见下）
  GET  /share?id=... → 匿名对话落地页（过期 → 410）
"""
import html
import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from src.security.auth import require_user, ensure_owner, optional_user
from src.images.chart_files import public_share_card_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"

#: 对外域名 —— **单一事实源**：`config.public_client_base()`（env `PUBLIC_BASE_URL`，
#: 回环值自动回落到生产域名；与 TTS / 夜间语音同一口径）。
#:
#: k85：改前这里是**写死的** `"https://fortune.talcloud.com"`，而该域名
#: **根本不解析**（k85 实测 `Could not resolve host`）⇒ 分享通道下发给客户端的
#: 三个 URL 全是死链：`report_url`、`share-cards/{name}`、以及 `_get_base_url()`
#: 的返回值。同型第二处见 `visual_report.py::_PUBLIC_BASE`（本批一并改为同源）。
def _base_url() -> str:
    from src.config import public_client_base
    return public_client_base()

# 全局引用，由 main.py 在 lifespan 中设置（setup_share）
_dao = None


def setup(dao):
    """在应用启动时设置 DAO 引用（owner 校验用）。"""
    global _dao
    _dao = dao


def _load_report(reading_id: str) -> dict:
    """Load a stored report by reading_id（**委托** `visual_report.load_report_from`）。

    k79-M2：加载实现**只有一份**（`visual_report.load_report_from(data_dir, id)`），
    本函数把自己的目录常量**传进去**，因此：

      - 语义（根必须是对象、解析失败当"不存在"）不再有第二份副本可漂移；
      - 测试隔离**不破** —— k78 担心的"委托会让测试的隔离失效"不成立：隔离取决于
        "调用点读的是谁的模块全局"，而这里读的是 **本模块的** `_DATA_DIR`
        （调用时求值 → `monkeypatch.setattr(share_api, "_DATA_DIR", tmp)` 照常生效；
        `tests/test_k76_security_batch.py` 对 `vr._DATA_DIR` 的 monkeypatch 也照常生效）。
    """
    from src.api.visual_report import load_report_from
    return load_report_from(_DATA_DIR, reading_id)


# ── k77-F：**报告分享页**的有效期（控制方拍板：与对话分享同为 30 天）──────
# 背景：`/share/{reading_id}` 是**匿名可读的公开页**（k76 已剥离姓名/出生信息），
# 但此前**没有有效期** —— 一旦分享出去就永久公开，与 k76 给对话分享设的
# 30 天口径不一致：同一个"分享出去的链接"在两个入口有两条规则。
#
# 单一事实源（**选择：与对话分享同一个配置项**，不另立第二旋钮）：
# 有效期仍只由 `src/config.py::share_ttl_days()`（环境变量 `FORTUNE_SHARE_TTL_DAYS`，
# 默认 30）定义，本页经 `share_ttl_seconds()` 取值。理由：
#   ① 控制方拍板的是**一条口径**——"分享链接不做无限期公开"，30 天是这条口径的取值；
#      两个入口都是"公开可访问的分享链接"，语义同类，没有分设取值的依据；
#   ② 加第二个环境变量等于允许"对话 30 天 / 报告永久"这种半失效状态复活
#      （正是本批要消灭的状态），而且必然出现"只改一处"的漂移
#      —— k72/k74/k75 反复点名的失败模式；
#   ③ 若将来确实需要区别对待，须先有产品拍板，届时再加旋钮，并由守卫
#      （k77 的分享有效期断言）同时钉住两个入口，避免只改一边。

#: reading_id 的合法形态（`visual_report.py` 生成口径：uuid4 前 8 位十六进制）。
#  本页是**匿名**入口，校验形态只为①拒绝路径穿越式怪串②不做无意义磁盘探测；
#  与 `/report/{reading_id}` 的"鉴权 + 归属校验"是两条不同的路径，不冲突。
_READING_ID_RE = re.compile(r"^[0-9a-f]{8}$")

#: 咨询 ID 的合法形态（k84-必修7）。**必须是 ASCII 数字**，不用 `str.isdigit()` ——
#  `isdigit()` 对 `²`（上标）、`１２３`（全角）也为真，而 `int('²')` 抛 ValueError
#  ⇒ 用它当判据会让 `GET /api/share/²` 变成 **500**（实测）。`^[0-9]+$` 与
#  `/api/share/{report_id}` 的**形态白名单**同一口径（单一事实源）。
_CONSULT_ID_RE = re.compile(r"^[0-9]+$")


def _report_path(reading_id: str) -> Path:
    """报告文件路径（k79-M2：与加载器同一个"路径怎么拼"的实现，不再各写一份）。"""
    from src.api.visual_report import report_path_in
    return report_path_in(_DATA_DIR, reading_id)


def _report_share_expires_at(report: dict, path: Path) -> float:
    """报告分享页的**失效时刻**（epoch 秒）—— 存量回算与对话分享同款。

    取值顺序（回算口径）：
      ① 报告 JSON 的 `generated_at`（生成报告时写入的 ISO 字符串）→ + TTL；
      ② 缺失/不可解析（k77 之前的老报告，或字段被清）→ 退回**文件 mtime** + TTL
         （mtime 是"这个文件什么时候出现在磁盘上"，是存量行最接近生成时刻的可用
         信号；与 share_dao 对老行 `COALESCE(created_at, now)` 的回算同思路）；
      ③ 两条路都拿不到（文件已消失等）→ 返回 0.0 = **视为已过期**
         （fail-closed，与 `ShareDAO.get_state` 对 `expires_at IS NULL` 的处理同款：
         绝不因为"算不出来"就退回"永久有效"）。
    """
    from src.config import share_ttl_seconds

    base = None
    # k78-必修3：非 dict 根（`[1,2,3]`）不再 AttributeError —— 取值前先判类型。
    # 调用方已用 `_load_report`/`report_shape_problem` 拦过，这里是同一函数被复用时
    # 的兜底（helper 自己也要站得住）。
    raw = report.get("generated_at") if isinstance(report, dict) else None
    if isinstance(raw, str) and raw.strip():
        try:
            base = datetime.fromisoformat(raw.strip()).timestamp()
        except Exception:
            base = None
    if base is None:
        try:
            base = path.stat().st_mtime
        except Exception:
            base = None
    if base is None:
        return 0.0
    return base + float(share_ttl_seconds())


def _report_share_expired_html() -> str:
    """报告分享页过期页（HTTP 410）——与对话分享的过期页同款口径与样式。

    k78-必修4：**标题必须准确**。本页此前直接复用 `_SHARE_PAGE_HEAD`（其 `<title>`
    恒为"易理明灯 · 一段对话"）—— 一份**报告**的过期页顶着"一段对话"的标题，
    与被打开的链接不符。现在标题由 `_fill_share_page(title=...)` 逐页给定。
    """
    from src.config import share_ttl_days

    days_text = f"{share_ttl_days():g}"
    return _fill_share_page(
        f"""
<div class="wrap empty">
  <div class="seal">明灯</div>
  <h2>这份报告分享已过期</h2>
  <p>分享链接的有效期为 {days_text} 天，到期后自动失效</p>
</div>""",
        title="报告分享已过期 · 易理明灯",
    )


def _get_base_url(request=None) -> str:
    """Get the base URL for share links（同源：`_base_url()`）。"""
    return _base_url()


# ── 分享卡片数据构造 ─────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _wuxing_summary(wuxing: dict) -> str:
    """五行概要文本（如 金旺(4) 木(2) 水缺(0) …）。"""
    parts = []
    for wx in ["金", "木", "水", "火", "土"]:
        count = _safe_int(wuxing.get(wx))
        if count >= 2:
            parts.append(f"{wx}旺({count})")
        elif count == 0:
            parts.append(f"{wx}缺")
        else:
            parts.append(f"{wx}({count})")
    return "五行：" + " ".join(parts)


def _card_from_consultation(c: dict, report_id: str) -> dict:
    """咨询记录 → 分享卡片数据（question/analysis/chart_data 已由 DAO 解密）。"""
    question = (c.get("question") or "").strip() or "命理咨询"
    analysis = (c.get("analysis") or "").strip()
    chart_data = {}
    try:
        chart_data = json.loads(c.get("chart_data") or "{}")
    except (ValueError, TypeError):
        chart_data = {}

    ba = chart_data.get("bazi_analysis") or chart_data
    bazi = ba.get("bazi", []) or chart_data.get("bazi", [])
    bazi_str = " ".join(bazi) if isinstance(bazi, list) else str(bazi or "")
    day_master = ba.get("day_master", "") or ""
    geju = ba.get("geju", "") or ""
    yongshen_raw = ba.get("yongshen", "") or ""
    yongshen = yongshen_raw.split("（")[0] if yongshen_raw else ""
    wuxing = ba.get("wuxing", {}) if isinstance(ba.get("wuxing", {}), dict) else {}

    # 命书摘要：取分析正文首段前 60 字；无正文时用问题
    summary = ""
    if analysis:
        first_line = analysis.split("\n")[0].strip()
        summary = first_line[:60]
    if not summary:
        summary = question[:60]

    return {
        "reading_id": report_id,
        "source": "consultation",
        "title": "我的命书 | 易理明灯",
        "summary": summary,
        "user_name": "我的命书",
        "bazi": bazi_str,
        "day_master": day_master,
        "geju": geju,
        "yongshen": yongshen,
        "wuxing_summary": _wuxing_summary(wuxing),
        "style": {"bg": "#faf8f5", "accent": "#c9a96e", "text": "#1a1a1a"},
        "qr_placeholder": "扫码查看完整命书",
        "report_url": f"{_base_url()}/report/{report_id}",
        "bazi_data": {
            "bazi": bazi,
            "day_master": day_master,
            "wuxing": wuxing,
            "shishen": ba.get("shishen", []),
            "geju": geju or "普通格",
            "yongshen": yongshen,
        },
    }


def _card_from_report(report: dict) -> dict:
    """旧版 data/reports JSON 报告 → 分享卡片数据。

    k79-必修1（Critical）：本函数此前是**唯一没有类型兜底**的报告消费点，复审实测
    四类畸形全部 500：

        {'profile': None, ...}                → AttributeError（`report.get("profile", {})`
                                                 在**键存在且值为 null** 时返回 None，
                                                 **默认值不生效** → `profile.get(...)`）
        {'insights': [12345]}                 → TypeError（`12345[:60]`）
        {'insights': [{'a':1}]}               → KeyError（`{'a':1}[:60]`）
        {'bazi_analysis': 'notadict'}         → AttributeError（`'str'.get(...)`）

    现在取值一律"先判类型再当映射/文本用"（`as_mapping` / `as_text` /
    `first_insight_text`，都是 `visual_report.py` 里的**同一份**实现），
    **任何输入**都返回一张可用的卡片，不再抛。
    """
    from src.api.visual_report import (as_mapping, as_text, first_insight_text,
                                       json_safe)

    profile = as_mapping(report.get("profile"))
    ba = as_mapping(report.get("bazi_analysis"))
    reading_id = as_text(report.get("reading_id"))

    user_name = as_text(profile.get("name"), "用户")
    if user_name in ("", "用户", "anonymous"):
        user_name = "我的命书"
    bazi = as_text(profile.get("bazi"))
    day_master = as_text(profile.get("day_master"))
    geju = as_text(ba.get("geju"))
    yongshen_raw = as_text(ba.get("yongshen"))
    yongshen = yongshen_raw.split("（")[0] if yongshen_raw else ""
    wuxing = as_mapping(ba.get("wuxing"))
    shishen = ba.get("shishen")
    shishen = shishen if isinstance(shishen, list) else []

    # 摘要：**第一条真的是文本**的洞察（元素非字符串一律跳过，不把 repr 念给用户）
    summary = first_insight_text(report, 60) or "AI命理分析报告"

    # 八字柱列表（profile.bazi 为空格分隔字符串；已是列表的老报告则只取其中的文本项）
    if isinstance(profile.get("bazi"), list):
        bazi_list = [x for x in profile["bazi"] if isinstance(x, str) and x.strip()]
    else:
        bazi_list = [p for p in bazi.split() if p]

    # k79：`bazi_data.wuxing` / `shishen` 是**原样透传**的报告数据，可能含
    # NaN/Infinity（`json.loads` 接受，Starlette 的 JSONResponse 不接受 ⇒ 500）。
    # 卡片整体过 `json_safe`：非有限浮点 → null，其它字段本来就是 str/list/dict。
    return json_safe({
        "reading_id": reading_id,
        "source": "legacy_report",
        "title": f"{user_name}的命运报告 | 易理明灯",
        "summary": summary,
        "user_name": user_name,
        "bazi": str(bazi),
        "day_master": day_master,
        "geju": geju,
        "yongshen": yongshen,
        "wuxing_summary": _wuxing_summary(wuxing),
        "style": {"bg": "#faf8f5", "accent": "#c9a96e", "text": "#1a1a1a"},
        "qr_placeholder": "扫码查看完整报告",
        # k76：指向**分享通道**而不是 /report/{reading_id}——后者自本批起需要
        # 登录+归属校验，把"分享给别人看"的链接指到那里等于给对方一个打不开的页。
        # 分享通道（/share/{reading_id}）匿名可读且已剥离个人信息，是收件人该走的路径。
        "report_url": f"{_base_url()}/share/{reading_id}",
        "bazi_data": {
            "bazi": bazi_list,
            "day_master": day_master,
            "wuxing": wuxing,
            "shishen": shishen,
            "geju": geju or "普通格",
            "yongshen": yongshen,
        },
    })


def _try_generate_image(card: dict) -> Optional[str]:
    """尝试生成真实分享图 PNG（ShareCardGenerator，依赖 playwright + CHARTS_DIR）。

    环境未装 playwright / CHARTS_DIR 不存在 / 渲染失败 → 返回 None，
    由调用方降级为结构化 card 数据（前端自行渲染）。
    """
    try:
        from src.images.share_card import ShareCardGenerator, CHARTS_DIR

        bd = card.get("bazi_data") or {}
        if not bd.get("bazi"):
            return None

        result = _BaziLite(
            bazi=bd.get("bazi", []),
            day_master=bd.get("day_master", ""),
            wuxing=bd.get("wuxing", {}),
            shishen=bd.get("shishen", []),
            geju=bd.get("geju", "普通格"),
            yongshen=bd.get("yongshen", ""),
        )
        out = ShareCardGenerator().generate(
            result,
            user_name=card.get("user_name", ""),
            output_path=str(CHARTS_DIR / f"share_{card['reading_id']}.png"),
        )
        # k85 必修1：分享卡 URL 也走同源域名（改前指向不解析的 talcloud）。
        return public_share_card_url(Path(out).name)
    except Exception as e:
        logger.warning("分享图生成失败，降级返回结构化卡片数据: %s", e)
        return None


class _BaziLite:
    """ShareCardGenerator 需要的 BaziResult 最小属性集。"""

    def __init__(self, bazi, day_master, wuxing, shishen, geju, yongshen):
        self.bazi = bazi or []
        self.day_master = day_master or ""
        self.wuxing = wuxing or {}
        self.shishen = shishen or []
        self.geju = geju or "普通格"
        self.yongshen = yongshen or ""


# ── 匿名对话分享(扫码落地页体系)─────────────────────────────────────
# 契约(与前端约定):
#   POST /api/share      body {pairs:[{u,tag,content}], dateText} → {"id": "8位base62"}
#   GET  /api/share/qr?url=... → 二维码 PNG(仅允许 https://yilichat.com/share?id= 前缀)
#   GET  /share?id=...    → 墨韵风格 HTML 落地页(纯静态,无外部依赖,内容 HTML 转义)
# 红线:匿名分享 —— 不落任何**明文**用户标识;页面无鉴权,内容即公开。
#
# k76(控制方 2026-09-21 拍板)两项收紧:
#   ① **有效期**:链接默认 30 天(`FORTUNE_SHARE_TTL_DAYS` 可配),到期失效
#      —— 过期返回 **410 Gone** + 「已过期」文案(≠ 不存在的 200 空页),
#      存量行按 created_at 回算(见 ShareDAO._migrate);
#   ② **归属标记**:登录用户创建时记 owner_tag(HMAC 伪名,非明文 user_id),
#      注销时据此删除本人分享;未登录创建 = 空标记(无归属可删,TTL 照旧约束)。
# 注意:本段路由必须定义在 /api/share/{report_id} 之前(FastAPI 按注册顺序匹配)。

_SHARE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHARE_ID_LEN = 8
_SHARE_QR_PREFIX = "https://yilichat.com/share?id="
_WEAPP_APPID = "wxe5391a48fe36b278"
_WEAPP_PATH = "pages/chat/chat"
_MAX_PAIRS = 50
_MAX_U_LEN = 200
_MAX_TAG_LEN = 50
_MAX_CONTENT_LEN = 3000
_MAX_DATETEXT_LEN = 50

_share_dao = None


def _sdao():
    """惰性获取 ShareDAO(首次调用时建表,复用 sqlite 轻量连接)。"""
    global _share_dao
    if _share_dao is None:
        from src.storage.dao import get_conn
        from src.storage.share_dao import ShareDAO
        _share_dao = ShareDAO(get_conn())
    return _share_dao


def _gen_share_id() -> str:
    """8 位 base62 短随机串 —— 不可逆推用户身份。"""
    return "".join(secrets.choice(_SHARE_ID_ALPHABET) for _ in range(_SHARE_ID_LEN))


class SharePairIn(BaseModel):
    u: str = ""          # 用户问句(可为空)
    tag: str = ""        # 明灯标签
    content: str = ""    # AI 回复


class ShareCreateIn(BaseModel):
    pairs: list[SharePairIn] = Field(..., max_length=_MAX_PAIRS)
    dateText: str = ""


def _clean_share_body(body: ShareCreateIn) -> dict:
    """清洗分享内容:截断超长字段、剔除空回复,防 DB 滥用。"""
    pairs = []
    for p in body.pairs:
        content = (p.content or "").strip()
        if not content:
            continue
        pairs.append({
            "u": (p.u or "").strip()[:_MAX_U_LEN],
            "tag": (p.tag or "").strip()[:_MAX_TAG_LEN],
            "content": content[:_MAX_CONTENT_LEN],
        })
    if not pairs:
        raise HTTPException(status_code=400, detail="pairs 不能为空")
    return {
        "pairs": pairs,
        "dateText": (body.dateText or "").strip()[:_MAX_DATETEXT_LEN],
    }


@router.post("/api/share")
def create_share(body: ShareCreateIn, _uid: str = Depends(optional_user)):
    """匿名落库一条分享对话,返回 8 位 base62 短 id(不含任何明文用户标识)。

    k76:按 `optional_user` 记**归属标记**(HMAC 伪名)供注销删除;未登录 → 空标记。
    本端点**仍然匿名可用**(登录不是前提,只是"记不记得住归属"的差别)。
    """
    from src.storage.share_dao import owner_tag_for

    content = _clean_share_body(body)
    owner_tag = owner_tag_for(_uid)
    for _ in range(5):  # 主键冲突(极小概率)换 id 重试
        share_id = _gen_share_id()
        if _sdao().insert(share_id, content, owner_tag=owner_tag):
            return {"id": share_id}
    raise HTTPException(status_code=500, detail="生成分享 id 失败,请重试")


@router.get("/api/share/qr")
def get_share_qr(url: str = Query(..., min_length=1, max_length=500)):
    """生成分享链接二维码 PNG。

    安全:url 必须带 https://yilichat.com/share?id= 前缀并拒绝控制字符,
    防止本接口被当作开放二维码生成工具(QR 编码任意钓鱼链接)。
    """
    if not url.startswith(_SHARE_QR_PREFIX):
        raise HTTPException(status_code=400, detail="仅支持本站分享链接")
    if any(ch in url for ch in ("\r", "\n", "\t", "\x00", " ")):
        raise HTTPException(status_code=400, detail="非法链接")
    try:
        import io as _io
        import qrcode

        img = qrcode.make(url)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("二维码生成失败: %s", e)
        raise HTTPException(status_code=500, detail="二维码生成失败")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/share")
def get_share_page(id: str = Query(..., min_length=4, max_length=32)):
    """墨韵风格 HTML 落地页:扫码打开看对话内容(纯静态,防 XSS)。

    k76 三种返回(到期行为**明确**、与"不存在"分开):
      - 有效          → 200 + 对话页;
      - **已过期**    → **410 Gone** + 「这段分享已过期」页(带有效期天数);
      - 不存在/损坏   → 200 + 「这段对话已随风而去」页(既有行为,未改)。
    过期一律**不返回内容**(fail-closed,由 ShareDAO.get_state 判定)。
    """
    state, entry = _sdao().get_state(id)
    if state == "expired":
        return HTMLResponse(_share_expired_html(), status_code=410)
    if state != "ok" or not entry:
        return HTMLResponse(_SHARE_EMPTY_HTML)
    return HTMLResponse(_render_share_page(id, entry))


# ── 墨韵风格落地页模板 ─────────────────────────────────────────────
# 宣纸底 #F5EFE1 / 墨 #3A2C1E / 朱砂 #A93A2C;宋体/楷体;页面内联 CSS,
# 无外部依赖、不引用任何 JS 库;对话内容一律 HTML 转义后替换 @@ 占位符。

_SHARE_PAGE_CSS = """
:root{--paper:#F5EFE1;--ink:#3A2C1E;--cinnabar:#A93A2C;--paper-deep:#EFE8D5;--line:#E3D9C0;}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html,body{background:var(--paper);}
body{color:var(--ink);font-family:"Songti SC","STSong","SimSun","Noto Serif SC","Kaiti SC","STKaiti","KaiTi",serif;min-height:100vh;line-height:1.7;}
.wrap{max-width:600px;margin:0 auto;padding:44px 22px 56px;}
.head{display:flex;align-items:center;gap:14px;}
.seal{width:46px;height:46px;flex:0 0 46px;background:var(--cinnabar);color:#F7F1E3;display:flex;align-items:center;justify-content:center;font-size:17px;letter-spacing:1px;border-radius:6px;box-shadow:0 3px 10px rgba(169,58,44,.3);font-family:"Kaiti SC","STKaiti","KaiTi",serif;}
.brand{font-size:26px;font-weight:700;letter-spacing:6px;}
.date{margin:10px 0 30px 60px;font-size:12.5px;color:rgba(58,44,30,.55);letter-spacing:1px;}
.pair{margin-bottom:26px;}
.q{display:inline-block;max-width:88%;background:var(--paper-deep);padding:12px 16px;border-radius:14px 14px 14px 5px;font-size:15px;line-height:1.8;}
.a-tag{display:flex;align-items:center;gap:8px;margin:18px 0 8px;color:var(--cinnabar);font-size:11.5px;letter-spacing:3px;font-family:"Kaiti SC","STKaiti","KaiTi",serif;}
.a-tag::before{content:"";width:14px;height:1px;background:rgba(169,58,44,.6);}
.a{background:#FBF6E9;border:1px solid var(--line);border-radius:5px 14px 14px 14px;padding:16px 18px;font-size:15px;line-height:2;white-space:pre-wrap;word-break:break-word;}
.foot{margin-top:40px;text-align:center;}
.btn-cta{display:block;width:100%;max-width:380px;margin:0 auto;padding:14px 0;background:var(--ink);color:var(--paper);border:none;border-radius:40px;font-size:16px;letter-spacing:4px;font-family:inherit;text-align:center;}
.btn-cta:active{opacity:.85;}
.web-hint{font-size:13px;color:rgba(58,44,30,.6);letter-spacing:1px;padding:10px 0 0;}
.like{display:inline-flex;align-items:center;gap:6px;margin-top:22px;padding:8px 22px;background:transparent;border:1px solid rgba(169,58,44,.45);border-radius:30px;color:var(--cinnabar);font-size:14px;font-family:inherit;letter-spacing:2px;cursor:pointer;}
.like.on{background:rgba(169,58,44,.08);border-color:var(--cinnabar);}
.like .heart{font-size:15px;}
.empty{text-align:center;padding-top:96px;}
.empty .seal{margin:0 auto 22px;}
.empty h2{font-size:19px;font-weight:600;letter-spacing:3px;margin-bottom:10px;}
.empty p{font-size:14px;color:rgba(58,44,30,.6);letter-spacing:1px;}
"""

#: 对话分享落地页的默认标题（正文是"一段对话"）。
#: k78-必修4：`<title>` 改成占位符 —— 改前它硬编码在 HEAD 里，所有复用 HEAD 的页面
#: （含**报告**过期页）都顶着"一段对话"这个与实际内容不符的标题。
_SHARE_PAGE_DEFAULT_TITLE = "易理明灯 · 一段对话"

_SHARE_PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
__BODY__
</body>
</html>"""


def _fill_share_page(body: str, title: str = _SHARE_PAGE_DEFAULT_TITLE) -> str:
    """把正文与标题填进分享页外壳（唯一入口，避免有人只改 `__CSS__`/`__BODY__`
    而把 `__TITLE__` 留成字面量）。标题经 `html.escape` 兜底（当前全部由本模块常量
    给定，不做外部输入）。"""
    return (
        _SHARE_PAGE_HEAD
        .replace("__TITLE__", html.escape(title))
        .replace("__CSS__", _SHARE_PAGE_CSS)
        .replace("__BODY__", body)
    )


_SHARE_PAGE_BODY = """
<div class="wrap">
  <div class="head">
    <div class="seal">明灯</div>
    <div class="brand">易理明灯</div>
  </div>
  <div class="date">__DATE__</div>
  <div class="dialogue">__PAIRS__</div>
  <div class="foot">
    <wx-open-launch-weapp id="wxLaunch" appid="__APPID__" path="__PATH__" style="display:none">
      <script type="text/wxtag-template">
        <style>
          .wxbtn{display:block;width:100%;max-width:380px;margin:0 auto;padding:14px 0;background:#3A2C1E;color:#F5EFE1;border-radius:40px;font-size:16px;letter-spacing:4px;font-family:"Songti SC","STSong","SimSun",serif;text-align:center;}
        </style>
        <div class="wxbtn">和明灯继续聊 ＞</div>
      </script>
    </wx-open-launch-weapp>
    <button class="btn-cta" id="webCta" style="display:none">和明灯继续聊 ＞</button>
    <div class="web-hint" id="webHint" style="display:none">请在微信中打开，和明灯继续聊</div>
    <button class="like" id="likeBtn" type="button"><span class="heart" id="likeHeart">♡</span><span class="cnt" id="likeCnt">0</span></button>
  </div>
</div>
<script>
(function(){
  var inWx = /MicroMessenger/i.test(navigator.userAgent || "");
  var wxBtn = document.getElementById("wxLaunch");
  var webBtn = document.getElementById("webCta");
  var hint = document.getElementById("webHint");
  if (inWx) { wxBtn.style.display = "block"; }
  else { webBtn.style.display = "block"; hint.style.display = "block"; }
  var KEY = __ID_JS__;
  var n = 0;
  try { n = parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (e) {}
  var cnt = document.getElementById("likeCnt");
  var heart = document.getElementById("likeHeart");
  var btn = document.getElementById("likeBtn");
  cnt.textContent = n;
  if (n > 0) { btn.className = "like on"; heart.textContent = "♥"; }
  btn.addEventListener("click", function () {
    n += 1;
    cnt.textContent = n;
    btn.className = "like on";
    heart.textContent = "♥";
    try { localStorage.setItem(KEY, String(n)); } catch (e) {}
  });
})();
</script>
"""

_SHARE_EMPTY_HTML = _fill_share_page(
    """
<div class="wrap empty">
  <div class="seal">明灯</div>
  <h2>这段对话已随风而去</h2>
  <p>来和明灯聊聊吧</p>
</div>
""",
)


def _share_expired_html() -> str:
    """过期分享落地页(HTTP 410)文案:明说"已过期"与有效期,不谎称"不存在"。

    k78-必修4：标题同为过期语义（改前复用 HEAD 的"易理明灯 · 一段对话"，
    浏览器标签页上看不出这条链接已经失效）。
    """
    from src.config import share_ttl_days

    days = share_ttl_days()
    days_text = f"{days:g}"
    return _fill_share_page(
        f"""
<div class="wrap empty">
  <div class="seal">明灯</div>
  <h2>这段分享已过期</h2>
  <p>分享链接的有效期为 {days_text} 天，到期后自动失效</p>
  <p>来和明灯聊聊吧</p>
</div>
""",
        title="分享已过期 · 易理明灯",
    )


def _js_string_literal(value) -> str:
    """JS 字符串字面量（**实现只有一份**：`visual_report.js_string_literal`）。

    k80 同类排查：本页把 `share_id` 填进 `var KEY = …;` —— **JS 字符串上下文**。
    改前用 `html.escape` 兜底：`"` 变 `&quot;` 看着挡住了，但**反斜杠/换行**没挡，
    一个换行就让 `var KEY = "…";` 变成未闭合字符串 ⇒ 整页 SyntaxError（白屏）。
    现在按"值进哪个上下文就用哪个上下文的编码"来办（HTML 用 html.escape，
    JS 用 json.dumps + 脚本内嵌转义）。
    """
    from src.api.visual_report import js_string_literal
    return js_string_literal(value)


def _render_share_page(share_id: str, entry: dict) -> str:
    """对话数据 → 落地页 HTML。所有对话内容经 html.escape,防 XSS。"""
    pairs_html = []
    for p in entry.get("pairs") or []:
        q = (p.get("u") or "").strip()
        tag = (p.get("tag") or "").strip()
        content = p.get("content") or ""
        tag_text = f"明灯 · {tag}" if tag else "明灯 · 夜话"
        block = []
        if q:  # 问句可为空:为空则只展示答句
            block.append(f'<div class="q">{html.escape(q)}</div>')
        block.append(f'<div class="a-tag">{html.escape(tag_text)}</div>')
        block.append(f'<div class="a">{html.escape(content)}</div>')
        pairs_html.append('<div class="pair">' + "".join(block) + "</div>")

    body = (
        _SHARE_PAGE_BODY
        .replace("__DATE__", html.escape(entry.get("dateText") or ""))
        .replace("__PAIRS__", "".join(pairs_html))
        .replace("__APPID__", _WEAPP_APPID)
        .replace("__PATH__", _WEAPP_PATH)
        .replace("__ID_JS__", _js_string_literal("yilm_like_" + str(share_id)))
    )
    return _fill_share_page(body)


# ── API 端点 ─────────────────────────────────────────────────────

@router.get("/api/share/{report_id}")
async def get_share_metadata(report_id: str, uid: str = Depends(require_user)):
    """获取/生成报告分享卡片：{imageUrl, card}。

    - report_id 为咨询 ID（数字）：必须登录 + 归属校验（403 非本人 / 404 不存在）
    - report_id 为 reading_id（8 位 hex）：兼容旧版 data/reports 报告（公开分享页）

    k80-M5：**分派判据从"是不是全数字"改成"哪一边真的有东西"**。
    改前一律 `report_id.isdigit()` 分派 ⇒ **全数字的 8 位 reading_id** 被误当咨询 ID。
    `reading_id = uuid4().hex[:8]` 全是数字的概率是 `(10/16)^8 ≈ 2.33%` ——
    这些报告（本人路径能正常打开）的**分享卡片会 404/503**（终验实测：约 2.3% 的
    分享链接打不开）。现在先看**报告文件在不在**（存在即按报告处理），再看数字 →
    咨询，两边都落空才是 404。两个命名空间（8 位 hex 文件名 vs 整数咨询 ID）
    各自独立，同名时"文件在"是唯一能同时解释两边的判据（此情形已在本仓不存在：
    咨询 ID 是自增整数，8 位纯数字且恰好有同名报告文件才会撞上，撞上时按报告处理）。

    k84-必修7：**形态白名单前置**（k79 报出、当时以"穿越实测不可达、属新增限制"为由
    未加，本批按控制方定规「只要是报的，都要修」补上）。
    只接受两种合法形态，其余一律 **404 且不做任何磁盘探测 / DB 查询**：
      ① `_READING_ID_RE`（8 位小写 hex）—— 与 `/share/{reading_id}`、
         `/report/{reading_id}`、`/api/report/{reading_id}` **同一口径、同一个正则**；
      ② `_CONSULT_ID_RE`（纯 ASCII 数字）—— 咨询 ID。
    **为什么改前"结论相同"仍要加**：改前怪串落到末尾 `else` 也是 404，但判据是
    **隐式**的 —— 它依赖"`_READING_ID_RE` 不中 ⇒ report=None"与"`.isdigit()` 不中
    ⇒ 走 else"两处**恰好都不读盘**；任何一处提前读盘/查库，这些怪串立刻变成探测面
    （`/share/{reading_id}` 早已前置白名单，本路由是同一匿名面对称位置上的缺口）。
    **顺带修掉一个既有 500**：`str.isdigit()` 对 `²`／`１２３` 等**非 ASCII 数字**为真，
    而 `int('²')` 抛 `ValueError`（实测）⇒ 改前 `GET /api/share/²` → **500**；
    白名单用 ASCII 的 `^[0-9]+$` 后归入 404。**这不是新增限制**：任何客户端都不产出
    全角/上标数字 ID，而它们此前也**不可能成功**（`²` 是 500，`１２３` 只会在
    咨询 ID 恰好等于 123 时"歪打正着"，属怪串碰撞而非支持形态）。
    """
    # 0. 形态白名单（见 docstring）：不读盘、不查库，直接 404
    if not (_READING_ID_RE.match(report_id or "")
            or _CONSULT_ID_RE.match(report_id or "")):
        raise HTTPException(status_code=404, detail="报告未找到")

    # 1. 定位报告来源
    report = (_load_report(report_id)
              if _READING_ID_RE.match(report_id or "") else None)
    if report is not None:
        # 旧版 JSON 报告：k76 起同样做**归属校验**（改前只要求登录，任意登录用户
        # 拿到 8 位 reading_id 即可读出他人姓名+八字——与 /report/{id} 同一个越权面）。
        # 归属未知的老报告 → 403（fail-closed，与读接口同一判据）。
        from src.api.visual_report import assert_report_owner, report_shape_problem
        assert_report_owner(report, uid)
        # k78-必修3：归属正确但内容畸形时，改前在 _card_from_report 里
        # AttributeError（profile=[1,2]）/TypeError → 500；同样归入"不可展示" → 404
        problem = report_shape_problem(report)
        if problem:
            logger.warning("分享卡片报告内容不可展示（404）reading_id=%s: %s", report_id, problem)
            raise HTTPException(status_code=404, detail="报告未找到")
        card = _card_from_report(report)
    elif _CONSULT_ID_RE.match(report_id):
        # 咨询记录派生报告：owner 校验
        # （k84-必修7：判据与上方形态白名单**共用** `_CONSULT_ID_RE`，不再各写一份
        #   `.isdigit()` —— 两处判据不同会让"白名单放过的"与"下游认的"漂移）
        if _dao is None:
            raise HTTPException(status_code=503, detail="Service not ready")
        c = _dao.get_consultation(int(report_id))
        if c is None:
            raise HTTPException(status_code=404, detail="报告未找到")
        ensure_owner(c.get("user_id", ""), uid)
        card = _card_from_consultation(c, report_id)
    else:
        raise HTTPException(status_code=404, detail="报告未找到")

    # 2. 尝试生成真实分享图；失败/环境未就绪 → 结构化降级
    image_url = _try_generate_image(card)

    # 3. 返回（imageUrl 为空时前端用 card 自行渲染）
    return {
        "reading_id": report_id,
        "imageUrl": image_url,
        "card": card,
        # 兼容旧字段（原 get_share_metadata 契约）
        "title": card["title"],
        "description": card["summary"],
        "share_text": f"我的命书来了！{card['summary']}… #易理明灯 #AI命理",
        "wechat_text": f"{card['user_name']}：{card['summary']}",
        "user_name": card["user_name"],
        "day_master": card["day_master"],
        "bazi": card["bazi"],
        "geju": card["geju"],
        "yongshen": card["yongshen"],
        "report_url": card["report_url"],
        "image_url": image_url,
        "shareable": True,
    }


#: 分享通道在公开页上**必须剥离**的个人信息字段（`profile` 下）。
#:
#: 依据（k72-A2 / 控制方红线）：姓名、出生日期属个人信息，不得出现在无需鉴权的
#: 公开页面上；八字与运势结论是**被分享的内容本体**（控制方已授权分享通道承载），
#: 故保留。这份清单同时被 `/share/{reading_id}` 使用。
#:
#: k80-必修1 第 3 层：**逐个字段重新问过"这是用户的个人信息吗"** ——
#:   - `name`       个人信息（可直接识别）        → 剥离；
#:   - `birth_date` 个人信息（可直接识别）        → 剥离；
#:   - `birth_info` 与 `birth_date` 同一事实的另一种写法 → 剥离；
#:   - `gender`     **个人信息** —— 本仓自己的事实源就把它列为收集项：
#:                  `miniprogram/privacy.md` 第 2 条「**性别**：用于区分阴阳年干的
#:                  排盘规则…」（同一条也写进 `privacy.wxml`）⇒ 与"姓名/出生日期不得
#:                  出现在匿名公开页"同一口径，**本批一并剥离**。
#:                  （改前不剥：登录用户提交恶意/任意 `gender` 会原样落在
#:                  匿名公开页上；这是本批 Critical 的传递通道。）
#:   - `bazi` / `day_master` → 被分享的**内容本体**（八字结论），保留。
#:
#: 顶层字段（不在 `profile` 下，k80 一并处理）见 `_SHARE_REDACT_TOP_FIELDS`。
_SHARE_REDACT_PROFILE_FIELDS = ("name", "birth_date", "birth_info", "gender")

#: `profile` 下**明确判定为"内容本体"从而保留**的字段（与剥离清单一起构成**闭集**：
#: 报告新出现一个 `profile.*` 字段而不表态 → `tests/test_k80_...::
#: TestGenderPayloadCannotReachAnyPage::test_every_profile_field_is_classified` 即红）。
#:
#:   - `bazi`       四柱 = 被分享的内容本体（控制方已授权分享通道承载）；
#:   - `day_master` 日主 = 八字结论的一部分。
#:
#: 注意**日期与年龄类**的自洽性（如实记录边界）：`bazi_analysis.dayun[].age` 与
#: `generated_at` 组合可以**反推**出生年份（起运岁数 + 报告生成年 ≈ 出生年）。
#: 这是分享通道**内容本体**的固有属性（没有大运就没有这份报告），本批不削；
#: 若要彻底消除，只能整段不下发大运 —— 属产品口径变更，留待控制方拍板。
_SHARE_KEEP_PROFILE_FIELDS = ("bazi", "day_master")

#: 公开页上必须剥离的**顶层**字段。`owner_enc` = AES(user_id) 的**归属标识密文**：
#: 它不是个人信息明文，但它是**账号标识**（能把同一个人生成的报告关联起来，
#: 也不想把它交给匿名访客）；`GET /report/{id}` 的归属校验只在服务端用得到，
#: 公开页完全不需要。它由 k76 引入（写盘时落 `owner_enc`），k76 的剥离清单没跟上
#: —— 于是新报告（带 `owner_enc`）的分享页把归属密文一起嵌进了匿名页面。
_SHARE_REDACT_TOP_FIELDS = ("owner_enc",)

#: `profile` **之外**的顶层字段里，明确判定为"内容本体从而保留"的那些
#: （与 `_SHARE_REDACT_TOP_FIELDS` 一起构成**闭集**）。
#:
#: k81-M2（终验点名）：`profile.*` 早有闭集测试
#: （`test_every_profile_field_is_classified`），**顶层字段却是逐个枚举**的
#: ——`_SHARE_REDACT_TOP_FIELDS = ("owner_enc",)` 是"人记着补"，而 `owner_enc`
#: 自己就是这么漏的（k76 新增了字段、剥离清单没跟上）。今天是没实际泄漏，
#: 但"**再漏一个 owner_enc 类字段**"恰好没有绊线。本批给顶层也做闭集：
#: `tests/test_k81_gender_privacy_final.py::TestTopLevelFieldsAreClassified`
#: 用**实际生成的报告**跑 `顶层键 ⊆ 剥离集 ∪ 保留集`，新增顶层键不表态即红。
#:
#: 逐字段理由（全部 = 被分享的内容本体；控制方已授权分享通道承载八字结论）：
#:   - `reading_id`      报告的**身份**（分享链接与页面对应的就是它；页面 JS 与
#:                       og 卡片都要用），非个人信息；
#:   - `generated_at`    生成时刻（TTL 判据 `_report_share_expires_at` 要用）；
#:   - `generated_date`  生成日期的展示文案（页面直接渲染）；
#:   - `version`         报告 payload 版本（渲染兼容用）；
#:   - `profile`         命主信息**容器**（其内部逐字段分类见
#:                       `_SHARE_REDACT_PROFILE_FIELDS` / `_SHARE_KEEP_PROFILE_FIELDS`）；
#:   - `bazi_analysis`   八字分析正文（格局/用神/十神/大运/流年）＝内容本体；
#:   - `charts`          图表数据（五行雷达/月运/年运）＝内容本体；
#:   - `insights`        洞察文案＝内容本体；
#:   - `recommendations` 建议卡片＝内容本体。
#:
#: 注意：**不保留**的顶层键不止 `owner_enc` 一类 —— 将来任何"账号/归属/鉴权"
#: 性质的顶层键都进 `_SHARE_REDACT_TOP_FIELDS`，不进这里。
_SHARE_KEEP_TOP_FIELDS = (
    "reading_id", "generated_at", "generated_date", "version",
    "profile", "bazi_analysis", "charts", "insights", "recommendations",
)


def _redact_report_for_share(report: dict) -> dict:
    """报告 → 分享通道可公开的副本（剥离个人信息与归属标识，**不改原 dict**）。

    - `profile.name` / `birth_date` / `birth_info` / `gender` 一律清空（名字置
      "用户"占位，`_build_report_html` 对内会退回中性标题）；
    - 顶层 `owner_enc` 一并剥离（**账号标识密文**：公开页不需要，见常量注释）。
      k81-M2**语义收紧**：改前是"置空串"，于是匿名页的内嵌 JSON 里**仍留着
      `"owner_enc": ""` 这个键名**（值没了、键还在）。现在**整键移除** ——
      "不下发"就是连字段名都不出现，与本人路径的 `without_report_owner`
      同口径（两处都是 `pop`）。公开页 JS 从不读该键，行为无变化。
    - 深拷贝到 JSON 兼容结构，避免调用方拿到被改动的原报告。
      k79：深拷贝失败时（超深嵌套触到递归上限）退成**浅拷贝 + 单独复制 profile**
      —— 否则下面的剥离会改到**调用方手里那份报告的** `profile`（"不改原 dict"
      这条契约会在兜底路径上破掉）。
    """
    try:
        safe = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    except Exception:
        safe = dict(report)
        _prof = safe.get("profile")
        safe["profile"] = dict(_prof) if isinstance(_prof, dict) else _prof
    if not isinstance(safe, dict):
        return safe
    profile = safe.get("profile")
    if isinstance(profile, dict):
        for field in _SHARE_REDACT_PROFILE_FIELDS:
            if field in profile:
                profile[field] = "用户" if field == "name" else ""
    # k81-M2：顶层剥离 = **整键移除**（改前置空串 → 匿名页里仍留 `"owner_enc": ""`
    # 这个键名）。判据（`.get(field)` 为假）对两种写法都成立，所以 k80 的既有断言
    # 一条都不用改、判定力不变。
    for field in _SHARE_REDACT_TOP_FIELDS:
        safe.pop(field, None)
    return safe


@router.get("/share/{reading_id}")
async def get_share_page_by_reading(reading_id: str):
    """分享通道：匿名可读的**报告分享页**（微信/浏览器卡片 + 完整报告）。

    k76 的两点与改前不同（控制方拍板，两条路径分开）：
      ① **公开页不出现个人信息** —— 改前标题写 `{姓名}的命运报告`，且整份报告
         （含 `profile.name` / 出生日期）嵌进页面 JSON；现在标题恒为中性标题，
         个人信息字段经 `_redact_report_for_share` 剥离后才入页。
      ② **不再 JS 跳转到 `/report/{reading_id}`** —— 那个页面 k76 起要鉴权+归属
         校验（本人路径），分享接收者没有令牌。故本页**就地渲染**报告，
         分享通道自身闭环（接收者的体验与改前一致：还是看到完整报告）。

    k77-F（控制方拍板）：本页**与对话分享同一有效期口径**（默认 30 天，见
    `_report_share_expires_at`）。到期 → **410 Gone** + 过期页（与 `/share?id=`
    的过期返回同款），**不再返回任何报告内容**（fail-closed）。
    为什么要做：本页是**匿名可读的公开面**，改前永久有效 —— 同一句"分享出去的
    链接"在对话分享（30 天）与报告分享（永久）之间口径分裂，本批一并收口。
    """
    # k77：形态校验（uuid4 前 8 位十六进制）→ 非法形态直接 404，
    # 不做磁盘探测、不接受怪串（匿名入口的纵深防御；合法 id 逐个不受影响）
    if not _READING_ID_RE.match(reading_id or ""):
        raise HTTPException(status_code=404, detail="报告未找到")
    report = _load_report(reading_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告未找到")
    # k78-必修3：文件在、内容却不是一份可展示的报告 ⇒ 与"不存在"同款 404（改前 500）。
    # 判据与 /report/{id}、/api/report/{id} 共用 report_shape_problem（单一事实源）。
    from src.api.visual_report import report_shape_problem
    problem = report_shape_problem(report)
    if problem:
        logger.warning("分享页报告内容不可展示（404）reading_id=%s: %s", reading_id, problem)
        raise HTTPException(status_code=404, detail="报告未找到")
    if time.time() >= _report_share_expires_at(report, _report_path(reading_id)):
        return HTMLResponse(_report_share_expired_html(), status_code=410)

    # k79-必修1：这里原来是 `insights[0][:50]` —— 与 `visual_report.py` 里
    # **同一句话的副本**；k78 在那里加了类型兜底，这一份没跟着改（复审点名：
    # "同一句话的副本没跟着改"）。现在两处都调 `first_insight_text`（同一实现），
    # 副本本身被消灭：元素非字符串 → 退回中性文案，不再 TypeError → 500。
    from src.api.visual_report import _build_report_html, first_insight_text

    safe = _redact_report_for_share(report)
    key_insight = first_insight_text(safe, 50) or "AI命理分析报告"
    share_text = f"我的2026运势报告来了！{key_insight}... #易理明灯 #AI命理"
    return HTMLResponse(_build_report_html(safe, share_text))
