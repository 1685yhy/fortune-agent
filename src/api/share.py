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
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

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
        "share_text": f"我的命书来了！{card['summary']}… #易理明灯 #AI算命",
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
    <meta name="keywords" content="命运报告,AI算命,八字,运势,易理明灯">

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
