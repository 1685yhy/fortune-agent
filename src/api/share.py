"""分享卡生成 API — Phase 4 Social Virality.

Provides:
  GET /api/share/{reading_id} → share card metadata for social media

Generates OpenGraph meta tags dynamically for each report,
so sharing on WeChat/Weibo shows a nice card preview.
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_BASE_URL = "https://fortune.talcloud.com"


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


@router.get("/api/share/{reading_id}")
async def get_share_metadata(reading_id: str):
    """Get share card metadata for a given report.

    Returns JSON with OpenGraph fields, share text templates,
    and social media preview data.
    """
    report = _load_report(reading_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告未找到")

    profile = report.get("profile", {})
    insights = report.get("insights", [])
    charts = report.get("charts", {})
    ba = report.get("bazi_analysis", {})

    # Determine user name
    user_name = profile.get("name", "用户")
    if user_name in ("", "用户", "anonymous"):
        title = "我的2026运势报告"
    else:
        title = f"{user_name}的命运报告"

    # Build share text
    key_insight = insights[0][:60] if insights else "AI命理分析，探索命运轨迹"
    share_text = (
        f"我的2026运势报告来了！{key_insight}… "
        f"#易理明灯 #AI算命"
    )
    wechat_text = (
        f"{user_name}的2026命运报告：{key_insight}"
    )

    # Generate summary text (for OpenGraph description)
    summary_parts = []
    if ba.get("geju"):
        summary_parts.append(f"格局：{ba['geju']}")
    if ba.get("yongshen"):
        summary_parts.append(f"用神：{ba['yongshen']}")
    if insights:
        summary_parts.append(insights[0][:40])

    description = " · ".join(summary_parts) if summary_parts else "AI命理分析报告"

    # Build monthly fortune highlights
    monthly = charts.get("monthly_fortune", [])
    fortune_highlights = []
    for m in monthly:
        if m.get("score", 50) >= 70:
            fortune_highlights.append(f"{m['label']}运势佳({m['score']}分)")
        elif m.get("score", 50) <= 30:
            fortune_highlights.append(f"{m['label']}需谨慎({m['score']}分)")

    # Build wuxing summary
    wuxing = ba.get("wuxing", {})
    wuxing_summary = "五行："
    for wx in ["金", "木", "水", "火", "土"]:
        count = wuxing.get(wx, 0)
        if count >= 2:
            wuxing_summary += f"{wx}旺({count}) "
        elif count == 0:
            wuxing_summary += f"{wx}缺({count}) "
        else:
            wuxing_summary += f"{wx}({count}) "

    return {
        "reading_id": reading_id,
        "title": title,
        "description": description,
        "share_text": share_text,
        "wechat_text": wechat_text,
        "weibo_text": share_text,
        "user_name": user_name,
        "day_master": profile.get("day_master", ""),
        "bazi": profile.get("bazi", ""),
        "geju": ba.get("geju", ""),
        "yongshen": ba.get("yongshen", ""),
        "fortune_highlights": fortune_highlights[:3],
        "wuxing_summary": wuxing_summary,
        "report_url": f"{_BASE_URL}/report/{reading_id}",
        "image_url": f"{_BASE_URL}/static/og-report.png",
        "generated_at": report.get("generated_at", ""),
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
