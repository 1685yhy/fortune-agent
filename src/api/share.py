"""分享卡 API — 报告分享卡片（命书摘要 + 用户昵称 + 二维码占位）。

Provides:
  GET /api/share/{report_id} → {imageUrl, card}
      - report_id 为咨询 ID（数字）→ 从 consultations 加载，owner 校验：
          不存在 404 / 非本人 403（IDOR 防护）
      - report_id 为 reading_id（8 位 hex）→ 兼容旧版 data/reports JSON 报告
          不存在 404（公开分享页，无归属校验）
      优先尝试生成真实分享图（ShareCardGenerator，依赖 playwright + CHARTS_DIR）；
      环境未就绪时降级返回结构化 card 数据（标题/摘要/样式/昵称/二维码占位），
      由前端自行渲染分享。
  GET /share/{reading_id} → OpenGraph HTML 分享页（微信爬虫卡片）
"""
import html
import json
import logging
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from src.security.auth import require_user, ensure_owner

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_BASE_URL = "https://fortune.talcloud.com"

# 全局引用，由 main.py 在 lifespan 中设置（setup_share）
_dao = None


def setup(dao):
    """在应用启动时设置 DAO 引用（owner 校验用）。"""
    global _dao
    _dao = dao


def _load_report(reading_id: str) -> dict:
    """Load a stored report by reading_id."""
    report_path = _DATA_DIR / f"{reading_id}.json"
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _get_base_url(request=None) -> str:
    """Get the base URL for share links."""
    return _BASE_URL


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
        "report_url": f"{_BASE_URL}/report/{report_id}",
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
    """旧版 data/reports JSON 报告 → 分享卡片数据。"""
    reading_id = report.get("reading_id", "")
    profile = report.get("profile", {})
    ba = report.get("bazi_analysis", {})
    insights = report.get("insights", [])

    user_name = profile.get("name", "用户")
    if user_name in ("", "用户", "anonymous"):
        user_name = "我的命书"
    bazi = profile.get("bazi", "")
    day_master = profile.get("day_master", "")
    geju = ba.get("geju", "") or ""
    yongshen_raw = ba.get("yongshen", "") or ""
    yongshen = yongshen_raw.split("（")[0] if yongshen_raw else ""
    wuxing = ba.get("wuxing", {}) if isinstance(ba.get("wuxing", {}), dict) else {}

    key_insight = insights[0] if insights else ""
    summary = key_insight[:60] if key_insight else "AI命理分析报告"

    # 八字柱列表（profile.bazi 为空格分隔字符串）
    bazi_list = [p for p in str(bazi).split() if p]

    return {
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
        "report_url": f"{_BASE_URL}/report/{reading_id}",
        "bazi_data": {
            "bazi": bazi_list,
            "day_master": day_master,
            "wuxing": wuxing,
            "shishen": ba.get("shishen", []),
            "geju": geju or "普通格",
            "yongshen": yongshen,
        },
    }


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
        return f"{_BASE_URL}/share-cards/{Path(out).name}"
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
# 红线:匿名分享 —— 不落任何用户标识;页面无鉴权,内容即公开。
# 注意:本段路由必须定义在 /api/share/{report_id} 之前(FastAPI 按注册顺序匹配)。

_SHARE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHARE_ID_LEN = 8
_SHARE_QR_PREFIX = "https://yilichat.com/share?id="
_WEAPP_APPID = "wxa100b72566782fd0"
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
def create_share(body: ShareCreateIn):
    """匿名落库一条分享对话,返回 8 位 base62 短 id(不含任何用户标识)。"""
    content = _clean_share_body(body)
    for _ in range(5):  # 主键冲突(极小概率)换 id 重试
        share_id = _gen_share_id()
        if _sdao().insert(share_id, content):
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
    """墨韵风格 HTML 落地页:扫码打开看对话内容(纯静态,防 XSS)。"""
    entry = _sdao().get(id)
    if not entry:
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

_SHARE_PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>易理明灯 · 一段对话</title>
<style>__CSS__</style>
</head>
<body>
__BODY__
</body>
</html>"""

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
  var KEY = "yilm_like___ID__";
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

_SHARE_EMPTY_HTML = _SHARE_PAGE_HEAD.replace("__CSS__", _SHARE_PAGE_CSS).replace(
    "__BODY__",
    """
<div class="wrap empty">
  <div class="seal">明灯</div>
  <h2>这段对话已随风而去</h2>
  <p>来和明灯聊聊吧</p>
</div>
""",
)


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
        .replace("__ID__", html.escape(share_id))
    )
    return _SHARE_PAGE_HEAD.replace("__CSS__", _SHARE_PAGE_CSS).replace("__BODY__", body)


# ── API 端点 ─────────────────────────────────────────────────────

@router.get("/api/share/{report_id}")
async def get_share_metadata(report_id: str, uid: str = Depends(require_user)):
    """获取/生成报告分享卡片：{imageUrl, card}。

    - report_id 为咨询 ID（数字）：必须登录 + 归属校验（403 非本人 / 404 不存在）
    - report_id 为 reading_id（8 位 hex）：兼容旧版 data/reports 报告（公开分享页）
    """
    # 1. 定位报告来源
    if report_id.isdigit():
        # 咨询记录派生报告：owner 校验
        if _dao is None:
            raise HTTPException(status_code=503, detail="Service not ready")
        c = _dao.get_consultation(int(report_id))
        if c is None:
            raise HTTPException(status_code=404, detail="报告未找到")
        ensure_owner(c.get("user_id", ""), uid)
        card = _card_from_consultation(c, report_id)
    else:
        # 旧版 JSON 报告（公开分享页）
        report = _load_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="报告未找到")
        card = _card_from_report(report)

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


@router.get("/share/{reading_id}")
async def get_share_redirect(reading_id: str):
    """Redirect to the report page with OpenGraph meta tags.

    This endpoint returns a minimal HTML page with full OpenGraph
    meta tags for social media crawlers, then redirects to the
    full report page via JavaScript.
    """
    report = _load_report(reading_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告未找到")

    profile = report.get("profile", {})
    insights = report.get("insights", [])
    ba = report.get("bazi_analysis", {})

    user_name = profile.get("name", "用户")
    if user_name in ("", "用户", "anonymous"):
        title = "我的2026运势报告 | 易理明灯"
    else:
        title = f"{user_name}的命运报告 | 易理明灯"

    key_insight = insights[0][:100] if insights else "AI命理分析报告"
    description = key_insight

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>

    <!-- OpenGraph -->
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{description}">
    <meta property="og:type" content="website">
    <meta property="og:image" content="{_BASE_URL}/static/og-report.png">
    <meta property="og:url" content="{_BASE_URL}/share/{reading_id}">
    <meta property="og:site_name" content="易理明灯">
    <meta property="og:locale" content="zh_CN">

    <!-- WeChat specific -->
    <meta name="wechat:title" content="{title}">
    <meta name="wechat:description" content="{description[:80]}">
    <meta name="wechat:image" content="{_BASE_URL}/static/og-report.png">

    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{description[:120]}">
    <meta name="twitter:image" content="{_BASE_URL}/static/og-report.png">

    <!-- Standard meta -->
    <meta name="description" content="{description}">
    <meta name="keywords" content="命运报告,AI命理,八字,运势,易理明灯">

    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0a0a0f; color: #e8e8ed;
               display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; text-align: center; }}
        .card {{ padding: 48px 24px; }}
        .card h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 12px; }}
        .card p {{ color: #8888a0; font-size: 14px; margin-bottom: 24px; }}
        .card .btn {{ display: inline-block; padding: 12px 32px; border-radius: 30px;
                     background: linear-gradient(135deg, #667eea, #764ba2); color: #fff;
                     text-decoration: none; font-weight: 500; font-size: 15px; }}
        .spinner {{ width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.1);
                    border-top: 2px solid #667eea; border-radius: 50%;
                    animation: spin 0.8s linear infinite; margin: 0 auto 16px; }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div class="card">
        <div class="spinner"></div>
        <h1>正在加载命运报告...</h1>
        <p>即将跳转至完整的可视化报告页面</p>
        <a class="btn" href="{_BASE_URL}/report/{reading_id}">查看完整报告</a>
    </div>

    <script>
        // Redirect to the full report page after a brief delay
        setTimeout(function() {{
            window.location.href = "{_BASE_URL}/report/{reading_id}";
        }}, 1500);
    </script>
</body>
</html>"""
    return HTMLResponse(html)
