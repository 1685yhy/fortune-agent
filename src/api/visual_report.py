"""视觉化命运报告 API — Phase 4 Social Virality.

Provides:
  GET  /api/report/{reading_id}  → report data as JSON
  GET  /report/{reading_id}      → rendered HTML report page
  POST /api/report/generate      → generate report from user profile

Auto-stores reports in data/reports/{reading_id}.json
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.engines.bazi import BaziEngine, BaziResult, TIANGAN, DIZHI, WUXING_TG, WUXING_DZ

logger = logging.getLogger(__name__)

router = APIRouter(tags=["visual_report"])

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_engine = BaziEngine()

# ─── Pydantic models ───────────────────────────────────────────

class BirthInfo(BaseModel):
    year: int
    month: int
    day: int
    hour: int = 12
    minute: int = 0
    gender: str = "男"
    city: str = "北京"
    name: str = ""


class GenerateReportRequest(BaseModel):
    user_id: str = "anonymous"
    birth: BirthInfo
    scenario: str = "overall"  # career, love, wealth, health, property, overall


# ─── Report generation helpers ─────────────────────────────────

def _compute_monthly_fortune(bazi_result: BaziResult) -> List[dict]:
    """Compute 12-month fortune trend based on month-over-month wuxing interactions.

    Each month's score (0-100) reflects how the monthly pillar interacts
    with the user's day master.
    """
    # Current year from liunian
    year_keys = sorted(bazi_result.liunian.keys())
    current_year = int(year_keys[2]) if len(year_keys) >= 3 else 2026  # middle year

    day_master_gan = bazi_result.bazi[2][0]  # day heavenly stem
    day_wx = WUXING_TG.get(day_master_gan, "土")

    # Sheng/Ke cycles
    sheng_cycle = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    ke_cycle = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    months = []
    for m in range(1, 13):
        # Build a simulated monthly stem-branch based on the year pillar
        # Year stem index drives the month sequence
        year_gan_idx = TIANGAN.index(bazi_result.bazi[0][0])
        # Month gan follows wuxing birth cycle from year
        month_gan_idx = (year_gan_idx * 2 + m - 1) % 10
        month_zhi_idx = (m + 1) % 12  #寅=2→正月

        month_gan = TIANGAN[month_gan_idx]
        month_zhi = DIZHI[month_zhi_idx]
        month_wx = WUXING_TG.get(month_gan, "土")
        month_zhi_wx = WUXING_DZ.get(month_zhi, "土")

        # Score computation (0-100)
        score = 50  # baseline

        # Same wuxing → boost
        if month_wx == day_wx:
            score += 15
        # Month generates day master → good
        if sheng_cycle.get(month_wx) == day_wx:
            score += 10
        # Month controls day master → bad
        if ke_cycle.get(month_wx) == day_wx:
            score -= 10
        # Day master generates month → slight drain
        if sheng_cycle.get(day_wx) == month_wx:
            score -= 5
        # Day master controls month → slight control
        if ke_cycle.get(day_wx) == month_wx:
            score += 5

        # Branch influence
        if sheng_cycle.get(month_zhi_wx) == day_wx:
            score += 5
        if ke_cycle.get(month_zhi_wx) == day_wx:
            score -= 5

        # Clamp
        score = max(5, min(95, score))

        # Chinese month names
        month_names = [
            "正月", "二月", "三月", "四月", "五月", "六月",
            "七月", "八月", "九月", "十月", "冬月", "腊月",
        ]

        months.append({
            "month": m,
            "month_name": month_names[m - 1],
            "gan": month_gan,
            "zhi": month_zhi,
            "wuxing": month_wx,
            "score": score,
            "label": f"{m}月",
        })

    return months


def _compute_annual_trend(bazi_result: BaziResult, years: int = 5) -> List[dict]:
    """Compute year-by-year fortune trend over multiple years."""
    year_keys = sorted(bazi_result.liunian.keys())
    current_year = int(year_keys[2]) if len(year_keys) >= 3 else 2026

    day_master_gan = bazi_result.bazi[2][0]
    day_wx = WUXING_TG.get(day_master_gan, "土")

    sheng_cycle = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    ke_cycle = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    trends = []
    for offset in range(-2, years):
        y = current_year + offset
        diff = y - 2024
        gan_idx = diff % 10
        zhi_idx = (4 + diff) % 12
        year_gan = TIANGAN[gan_idx]
        year_zhi = DIZHI[zhi_idx]
        year_wx = WUXING_TG.get(year_gan, "土")

        score = 50
        if year_wx == day_wx:
            score += 15
        if sheng_cycle.get(year_wx) == day_wx:
            score += 10
        if ke_cycle.get(year_wx) == day_wx:
            score -= 10
        if sheng_cycle.get(day_wx) == year_wx:
            score -= 5
        if ke_cycle.get(day_wx) == year_wx:
            score += 5

        score = max(5, min(95, score))

        # Determine phase label
        if score >= 70:
            phase = "旺"
        elif score >= 55:
            phase = "平上"
        elif score >= 40:
            phase = "平"
        else:
            phase = "弱"

        trends.append({
            "year": y,
            "gan": year_gan,
            "zhi": year_zhi,
            "ganzhi": year_gan + year_zhi,
            "wuxing": year_wx,
            "score": score,
            "phase": phase,
        })

    return trends


def _wuxing_to_radar(wuxing: dict) -> List[dict]:
    """Convert wuxing dict to radar chart data."""
    return [
        {"axis": "金", "value": wuxing.get("金", 0)},
        {"axis": "木", "value": wuxing.get("木", 0)},
        {"axis": "水", "value": wuxing.get("水", 0)},
        {"axis": "火", "value": wuxing.get("火", 0)},
        {"axis": "土", "value": wuxing.get("土", 0)},
    ]


def _generate_insights(bazi_result: BaziResult) -> List[str]:
    """Generate 3 key insights from bazi data."""
    wuxing = bazi_result.wuxing
    day_master = bazi_result.day_master
    dm_wx = day_master[-1]  # 金木水火土

    # Sort wuxing by count
    sorted_wx = sorted(wuxing.items(), key=lambda x: -x[1])
    strongest = sorted_wx[0]
    weakest = sorted_wx[-1]

    insights = []

    # Insight 1: Strongest/weakest wuxing
    if strongest[1] >= 3:
        insights.append(
            f"命局{strongest[0]}元素偏旺（{strongest[1]}处），"
            f"需注意{strongest[0]}过旺带来的平衡问题。"
        )
    else:
        insights.append(
            f"日主{dm_wx}为{dm_wx}命，"
            f"命局五行分布相对均衡，整体运势较为平稳。"
        )

    # Insight 2: Geju insight
    geju = bazi_result.geju
    dayun_insight = ""
    if bazi_result.dayun:
        next_dayun = bazi_result.dayun[0]
        dayun_insight = f"当前大运为{next_dayun[1]}（{next_dayun[0]}岁起），"

    if "官" in geju:
        insights.append(
            f"{geju}，{dayun_insight}"
            f"事业和贵人运较好，适合在职场中发挥领导才能。"
        )
    elif "财" in geju:
        insights.append(
            f"{geju}，{dayun_insight}"
            f"财运方面有天然优势，适合在金融、商贸等领域发展。"
        )
    elif "印" in geju:
        insights.append(
            f"{geju}，{dayun_insight}"
            f"学习能力和吸收力强，适合深耕专业技能。"
        )
    elif "食" in geju or "伤" in geju:
        insights.append(
            f"{geju}，{dayun_insight}"
            f"才华和表达力突出，适合创意、艺术、教育等领域。"
        )
    else:
        insights.append(
            f"{geju}，{dayun_insight}"
            f"命局较为中和，运势起伏不大，稳中求进为上策。"
        )

    # Insight 3: Yongshen advice
    yongshen = bazi_result.yongshen
    insights.append(
        f"用神建议：{yongshen}。"
        f"在生活、工作中多运用和接触对应五行元素，"
        f"有助于提升整体运势。"
    )

    return insights


def _generate_recommendations(bazi_result: BaziResult) -> List[dict]:
    """Generate 3 actionable recommendations with time windows."""
    wuxing = bazi_result.wuxing
    day_master = bazi_result.day_master
    dm_wx = day_master[-1]

    sheng_cycle = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    ke_cycle = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    sorted_wx = sorted(wuxing.items(), key=lambda x: -x[1])
    strongest = sorted_wx[0]
    weakest = sorted_wx[-1]

    # Determine weakest element for recommendation
    recs = []

    # Rec 1: Career/development
    if "金" in bazi_result.yongshen:
        career = "从事金属、金融、科技、法律相关行业"
        time_w = "当前至下个大运交接"
    elif "水" in bazi_result.yongshen:
        career = "从事贸易、物流、文化传媒、互联网相关行业"
        time_w = "未来3年"
    elif "木" in bazi_result.yongshen:
        career = "从事教育、医疗、环保、文化艺术相关行业"
        time_w = "未来1-2年"
    elif "火" in bazi_result.yongshen:
        career = "从事能源、餐饮、娱乐、IT技术相关行业"
        time_w = "未来2年"
    elif "土" in bazi_result.yongshen:
        career = "从事房地产、建筑、农业、咨询相关行业"
        time_w = "当前运势窗口"
    else:
        career = "发展与日主五行相生的行业方向"
        time_w = "近期"

    recs.append({
        "time_window": time_w,
        "action": f"职业发展建议：{career}",
        "reason": f"基于命局用神{bazi_result.yongshen}的五行导向",
    })

    # Rec 2: Relationship/social
    shensha = bazi_result.shensha
    if "天乙贵人" in shensha:
        recs.append({
            "time_window": "全年",
            "action": "多与属马、属虎的朋友交往，易遇贵人相助",
            "reason": "命带天乙贵人，善用贵人运可事半功倍",
        })
    else:
        recs.append({
            "time_window": "重点月份",
            "action": f"加强{weakest[0]}元素的社交活动，弥补五行短板",
            "reason": f"命局{weakest[0]}元素偏弱（{weakest[1]}处），需主动补足",
        })

    # Rec 3: Health/lifestyle
    if weakest[0] == "木":
        recs.append({
            "time_window": "日常",
            "action": "规律作息，多进行户外运动和伸展，多吃绿色蔬菜",
            "reason": "木主肝胆，木弱易疲劳，需加强春季养生",
        })
    elif weakest[0] == "火":
        recs.append({
            "time_window": "午时（11-13点）",
            "action": "午间小憩，补充红色食物，保持心态积极",
            "reason": "火主心脏，火弱需注意心血管健康",
        })
    elif weakest[0] == "土":
        recs.append({
            "time_window": "每季末",
            "action": "注意饮食规律，少吃生冷，多吃黄色五谷杂粮",
            "reason": "土主脾胃，土弱需注意消化系统",
        })
    elif weakest[0] == "金":
        recs.append({
            "time_window": "秋冬季节",
            "action": "注意呼吸道保养，多吃白色食物，保持空气湿润",
            "reason": "金主肺，金弱需注意呼吸系统健康",
        })
    elif weakest[0] == "水":
        recs.append({
            "time_window": "冬季",
            "action": "注意保暖，多喝温水，适当补充黑色食物",
            "reason": "水主肾，水弱需注意泌尿和内分泌系统",
        })
    else:
        recs.append({
            "time_window": "全年",
            "action": "保持规律作息和适度运动，定期体检",
            "reason": "五行平衡，重在维持",
        })

    return recs


def generate_report_data(
    bazi_result: BaziResult,
    birth: Optional[dict] = None,
    name: str = "",
) -> dict:
    """Generate complete report data from a BaziResult."""
    monthly = _compute_monthly_fortune(bazi_result)
    annual = _compute_annual_trend(bazi_result)
    radar = _wuxing_to_radar(bazi_result.wuxing)
    insights = _generate_insights(bazi_result)
    recommendations = _generate_recommendations(bazi_result)

    reading_id = str(uuid.uuid4())[:8]

    now = datetime.now(timezone(timedelta(hours=8)))

    report = {
        "reading_id": reading_id,
        "generated_at": now.isoformat(),
        "generated_date": now.strftime("%Y年%m月%d日"),
        "version": "v5.0",
        "profile": {
            "name": name or "用户",
            "bazi": " ".join(bazi_result.bazi),
            "day_master": bazi_result.day_master,
            "gender": birth.get("gender", "") if birth else "",
            "birth_date": f"{birth.get('year','')}年{birth.get('month','')}月{birth.get('day','')}日" if birth else "",
            "birth_info": f"{birth.get('year','')}年{birth.get('month','')}月{birth.get('day','')}日" if birth else "未知",
        },
        "bazi_analysis": {
            "wuxing": bazi_result.wuxing,
            "shishen": bazi_result.shishen,
            "geju": bazi_result.geju,
            "yongshen": bazi_result.yongshen,
            "shensha": bazi_result.shensha,
            "nayin": bazi_result.nayin,
            "dayun": [
                {"age": age, "ganzhi": gz, "label": f"{age}-{age+9}岁"}
                for age, gz in bazi_result.dayun[:8]
            ],
            "liunian": bazi_result.liunian,
        },
        "charts": {
            "monthly_fortune": monthly,
            "annual_trend": annual,
            "wuxing_radar": radar,
        },
        "insights": insights,
        "recommendations": recommendations,
    }

    # Store to disk
    report_path = _DATA_DIR / f"{reading_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def load_report(reading_id: str) -> Optional[dict]:
    """Load a stored report by reading_id."""
    report_path = _DATA_DIR / f"{reading_id}.json"
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ─── API endpoints ─────────────────────────────────────────────

@router.get("/api/report/{reading_id}")
async def get_report_json(reading_id: str):
    """Get report data as JSON."""
    report = load_report(reading_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告未找到")
    return report


@router.get("/report/{reading_id}")
async def get_report_page(reading_id: str):
    """Get the rendered HTML report page."""
    report = load_report(reading_id)
    if not report:
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>报告未找到</title></head><body>"
            "<h1>报告未找到</h1><p>该命运报告不存在或已被删除。</p></body></html>",
            status_code=404,
        )

    # Share text for social media
    share_text = (
        f"我的2026运势报告来了！{report['insights'][0][:50]}..."
        f" #易理明灯 #AI算命"
    )

    html = _build_report_html(report, share_text)
    return HTMLResponse(html)


def _build_report_html(report: dict, share_text: str) -> str:
    """Build the full HTML report page with embedded data.

    Uses string.Template to avoid f-string conflicts with JavaScript/CSS braces.
    """
    import json as _json
    from string import Template

    report_json = _json.dumps(report, ensure_ascii=False)
    share_text_escaped = share_text.replace('"', '&quot;')

    # Build OG title
    pname = report["profile"]["name"]
    if pname in ("", "用户", "anonymous"):
        og_title = "我的2026运势报告 | 易理明灯"
    else:
        og_title = f"{pname}的命运报告 | 易理明灯"

    og_desc = report["insights"][0][:100] if report["insights"] else "AI命理分析报告"
    og_desc_short = report["insights"][0][:80] if report["insights"] else "AI命理分析报告"
    reading_id = report["reading_id"]

    # The static HTML/CSS/JS template — contains NO Python f-string interpolation
    # All Python values are substituted via $PLACEHOLDER markers using string.Template
    template_str = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>$og_title</title>

    <!-- OpenGraph Meta Tags -->
    <meta property="og:title" content="$og_title">
    <meta property="og:description" content="$og_desc">
    <meta property="og:type" content="website">
    <meta property="og:image" content="https://fortune.talcloud.com/static/og-report.png">
    <meta property="og:url" content="https://fortune.talcloud.com/report/$reading_id">
    <meta name="description" content="$og_desc">

    <!-- WeChat Share Meta -->
    <meta name="wechat:title" content="$og_title">
    <meta name="wechat:description" content="$og_desc_short">
    <meta name="wechat:image" content="https://fortune.talcloud.com/static/og-report.png">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700;900&display=swap" rel="stylesheet">

    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0a0a0f;
            color: #e8e8ed;
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            overflow-x: hidden;
        }
        .bg-gradient {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background:
                radial-gradient(ellipse at 20% 0%, rgba(120, 80, 200, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 100%, rgba(200, 100, 50, 0.06) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 50%, rgba(50, 100, 200, 0.04) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            padding: 24px 16px 48px;
            position: relative;
            z-index: 1;
        }
        .report-header { text-align: center; padding: 40px 0 32px; }
        .badge {
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #fff; font-size: 11px; font-weight: 600;
            padding: 4px 14px; border-radius: 20px;
            letter-spacing: 1px; margin-bottom: 16px;
        }
        .report-header h1 {
            font-size: 28px; font-weight: 700;
            background: linear-gradient(135deg, #e8e8ed 0%, #a0a0b8 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            background-clip: text; margin-bottom: 8px;
        }
        .report-header .subtitle { font-size: 14px; color: #8888a0; font-weight: 300; }
        .profile-card {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 20px; padding: 24px; margin-bottom: 20px;
            backdrop-filter: blur(10px);
        }
        .profile-card .row {
            display: flex; justify-content: space-between;
            align-items: center; padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .profile-card .row:last-child { border-bottom: none; }
        .profile-card .label { color: #8888a0; font-size: 13px; }
        .profile-card .value { color: #e8e8ed; font-size: 14px; font-weight: 500; }
        .profile-card .bazi-display {
            font-size: 28px; font-weight: 700; letter-spacing: 6px;
            text-align: center; padding: 12px 0 8px;
            background: linear-gradient(135deg, #f5af19 0%, #f12711 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .profile-card .day-master { text-align: center; font-size: 14px; color: #8888a0; }
        .section-title {
            font-size: 18px; font-weight: 600;
            margin: 32px 0 16px; padding-left: 12px;
            border-left: 3px solid #667eea;
        }
        .chart-card {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 20px; padding: 24px 16px;
            margin-bottom: 20px; backdrop-filter: blur(10px);
            overflow: hidden;
        }
        .chart-card h3 {
            font-size: 14px; color: #8888a0;
            font-weight: 400; margin-bottom: 16px; text-align: center;
        }
        .kline-chart { position: relative; height: 200px; width: 100%; margin: 8px 0; }
        .kline-chart svg { width: 100%; height: 100%; }
        .kline-labels {
            display: flex; justify-content: space-between;
            margin-top: 4px; padding: 0 2px;
        }
        .kline-labels span { font-size: 9px; color: #666; white-space: nowrap; }
        .radar-container { display: flex; justify-content: center; align-items: center; padding: 8px 0; }
        .radar-container svg { width: 240px; height: 240px; }
        .radar-legend { display: flex; justify-content: center; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
        .radar-legend-item { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #b0b0c0; }
        .radar-legend-item .dot { width: 10px; height: 10px; border-radius: 50%; }
        .insight-list { list-style: none; padding: 0; }
        .insight-list li {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px; padding: 16px 18px 16px 36px;
            margin-bottom: 12px; font-size: 14px; line-height: 1.7;
        }
        .rec-list { list-style: none; padding: 0; }
        .rec-list li {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px; padding: 18px; margin-bottom: 12px;
        }
        .rec-list .rec-number {
            display: inline-flex; align-items: center; justify-content: center;
            width: 24px; height: 24px; border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: #fff; font-size: 12px; font-weight: 700; margin-bottom: 8px;
        }
        .rec-list .rec-time { font-size: 12px; color: #667eea; font-weight: 500; margin-bottom: 4px; }
        .rec-list .rec-action { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
        .rec-list .rec-reason { font-size: 12px; color: #8888a0; }
        .report-footer { text-align: center; padding: 32px 0 16px; border-top: 1px solid rgba(255,255,255,0.06); margin-top: 32px; }
        .report-footer .disclaimer { font-size: 12px; color: #666; margin-bottom: 16px; }
        .report-footer .qr-placeholder {
            width: 100px; height: 100px; margin: 0 auto 16px;
            background: rgba(255,255,255,0.06);
            border: 1px dashed rgba(255,255,255,0.15);
            border-radius: 12px; display: flex; align-items: center;
            justify-content: center; font-size: 10px; color: #555;
        }
        .share-btn {
            display: block; width: 100%; max-width: 280px;
            margin: 0 auto 32px; padding: 14px 24px; border: none;
            border-radius: 30px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #fff; font-size: 16px; font-weight: 600; cursor: pointer;
            transition: all 0.3s ease; font-family: inherit;
        }
        .share-btn:hover { transform: translateY(-1px); box-shadow: 0 8px 24px rgba(102, 126, 234, 0.3); }
        .share-btn:active { transform: scale(0.98); }
        .loading { text-align: center; padding: 80px 0; color: #666; }
        .spinner {
            width: 32px; height: 32px;
            border: 3px solid rgba(255,255,255,0.08);
            border-top: 3px solid #667eea;
            border-radius: 50%; animation: spin 0.8s linear infinite;
            margin: 0 auto 16px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .wuxing-bars { display: flex; gap: 10px; margin-top: 16px; }
        .wuxing-bar-item { flex: 1; text-align: center; }
        .wuxing-bar-item .bar-track {
            height: 80px; background: rgba(255,255,255,0.04);
            border-radius: 6px; position: relative; margin-bottom: 6px; overflow: hidden;
        }
        .wuxing-bar-item .bar-fill {
            position: absolute; bottom: 0; left: 0; right: 0;
            border-radius: 6px; transition: height 0.6s ease;
        }
        .wuxing-bar-item .bar-label { font-size: 12px; font-weight: 600; }
        .wuxing-bar-item .bar-value { font-size: 11px; color: #8888a0; }
        .wx-金 .bar-fill { background: linear-gradient(to top, #f0d060, #f5e090); }
        .wx-木 .bar-fill { background: linear-gradient(to top, #60c060, #90d090); }
        .wx-水 .bar-fill { background: linear-gradient(to top, #5090e0, #80b0e8); }
        .wx-火 .bar-fill { background: linear-gradient(to top, #e06060, #e89090); }
        .wx-土 .bar-fill { background: linear-gradient(to top, #c09050, #d8b080); }
        @media (max-width: 480px) {
            .container { padding: 16px 12px 32px; }
            .report-header h1 { font-size: 22px; }
            .profile-card .bazi-display { font-size: 24px; letter-spacing: 4px; }
            .kline-chart { height: 160px; }
            .radar-container svg { width: 200px; height: 200px; }
        }
    </style>
</head>
<body>
    <div class="bg-gradient"></div>
    <div class="container" id="app">
        <div class="loading" id="loading">
            <div class="spinner"></div>
            <div>加载命运报告中...</div>
        </div>
    </div>

    <script>
    (function() {
        var REPORT = $report_json;
        var SHARE_TEXT = "$share_text_escaped";

        function render() {
            var r = REPORT;
            var p = r.profile;
            var ba = r.bazi_analysis;
            var html = '';

            html += '<div class="report-header">';
            html += '<div class="badge">命运报告 v5.0</div>';
            html += '<h1>' + p.name + '的命运报告</h1>';
            html += '<div class="subtitle">' + r.generated_date + ' · AI智能生成</div>';
            html += '</div>';

            // Profile Card
            html += '<div class="profile-card">';
            html += '<div class="bazi-display">' + p.bazi + '</div>';
            html += '<div class="day-master">日主 ' + p.day_master + ' · ' + p.gender + '</div>';
            html += '<div style="margin-top:12px">';
            html += '<div class="row"><span class="label">出生</span><span class="value">' + p.birth_info + '</span></div>';
            html += '<div class="row"><span class="label">格局</span><span class="value">' + ba.geju + '</span></div>';
            html += '<div class="row"><span class="label">用神</span><span class="value">' + ba.yongshen + '</span></div>';
            html += '<div class="row"><span class="label">神煞</span><span class="value">' + (ba.shensha && ba.shensha.length ? ba.shensha.join('、') : '无') + '</span></div>';
            html += '</div></div>';

            // Wuxing Analysis
            html += '<div class="section-title">五行能量分布</div>';
            html += '<div class="chart-card">';
            html += '<div class="radar-container">' + drawRadar(r.charts.wuxing_radar) + '</div>';

            // Legend
            html += '<div class="radar-legend">';
            var wxClr = {"金":"#f0d060","木":"#60c060","水":"#5090e0","火":"#e06060","土":"#c09050"};
            r.charts.wuxing_radar.forEach(function(item) {
                html += '<div class="radar-legend-item"><span class="dot" style="background:' + wxClr[item.axis] + '"></span>' + item.axis + ' ' + item.value + '</div>';
            });
            html += '</div>';

            // Wuxing bars
            html += '<div class="wuxing-bars">';
            r.charts.wuxing_radar.forEach(function(item) {
                var vals = r.charts.wuxing_radar.map(function(x) { return x.value; });
                var maxVal = Math.max(1, Math.max.apply(null, vals));
                var pct = Math.round((item.value / maxVal) * 100);
                html += '<div class="wuxing-bar-item wx-' + item.axis + '">';
                html += '<div class="bar-track"><div class="bar-fill" style="height:' + pct + '%"></div></div>';
                html += '<div class="bar-label">' + item.axis + '</div>';
                html += '<div class="bar-value">' + item.value + '</div></div>';
            });
            html += '</div></div>';

            // K-Line Chart
            html += '<div class="section-title">年度运势走势</div>';
            html += '<div class="chart-card">';
            var mf = r.charts.monthly_fortune;
            if (mf && mf.length > 0) {
                html += '<h3>未来12个月运势趋势</h3>';
            }
            html += drawKLine(mf);
            html += '</div>';

            // Annual Trend
            html += '<div class="chart-card">';
            html += '<h3>年运势大趋势</h3>';
            html += drawAnnualTrend(r.charts.annual_trend);
            html += '</div>';

            // Key Insights
            html += '<div class="section-title">核心洞察</div>';
            html += '<ul class="insight-list">';
            r.insights.forEach(function(insight) {
                html += '<li>' + insight + '</li>';
            });
            html += '</ul>';

            // Recommendations
            html += '<div class="section-title">行动建议</div>';
            html += '<ol class="rec-list">';
            r.recommendations.forEach(function(rec, idx) {
                html += '<li>';
                html += '<div class="rec-number">' + (idx + 1) + '</div>';
                html += '<div class="rec-time">' + rec.time_window + '</div>';
                html += '<div class="rec-action">' + rec.action + '</div>';
                html += '<div class="rec-reason">' + rec.reason + '</div>';
                html += '</li>';
            });
            html += '</ol>';

            // Footer
            html += '<div class="report-footer">';
            html += '<div class="disclaimer">由易理明灯生成 · 仅供娱乐参考</div>';
            html += '<div class="qr-placeholder">QR Code placeholder</div>';
            html += '<button class="share-btn" onclick="shareReport()">分 享</button>';
            html += '</div>';

            document.getElementById('app').innerHTML = html;
        }

        function drawKLine(months) {
            if (!months || months.length === 0) return '';
            var pw = 100, ph = 100, pd = 5;
            var cw = pw - pd * 2, ch = ph - pd * 2;
            var scores = months.map(function(m) { return m.score; });
            var minS = Math.min.apply(null, scores);
            var maxS = Math.max.apply(null, scores);
            var range = Math.max(maxS - minS, 10);
            var xStep = cw / (months.length - 1);

            function yPos(s) { return pd + ch - ((s - minS) / range) * ch; }

            var pathD = '';
            months.forEach(function(m, i) {
                var x = pd + i * xStep;
                var y = yPos(m.score);
                if (i === 0) pathD += 'M' + x + ',' + y;
                else pathD += ' L' + x + ',' + y;
            });

            var areaD = pathD + ' L' + (pd + (months.length - 1) * xStep) + ',' + (pd + ch) +
                        ' L' + pd + ',' + (pd + ch) + ' Z';
            var gid = 'kg-' + Math.random().toString(36).substr(2, 5);

            var svg = '<svg viewBox="0 0 ' + pw + ' ' + ph + '" xmlns="http://www.w3.org/2000/svg">';
            svg += '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">';
            svg += '<stop offset="0%" stop-color="#667eea" stop-opacity="0.3"/>';
            svg += '<stop offset="100%" stop-color="#667eea" stop-opacity="0.02"/></linearGradient></defs>';
            svg += '<line x1="' + pd + '" y1="' + pd + '" x2="' + pd + '" y2="' + (pd + ch) + '" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>';
            svg += '<line x1="' + pd + '" y1="' + (pd + ch/2) + '" x2="' + (pd + cw) + '" y2="' + (pd + ch/2) + '" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>';
            svg += '<line x1="' + pd + '" y1="' + (pd + ch) + '" x2="' + (pd + cw) + '" y2="' + (pd + ch) + '" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>';
            svg += '<path d="' + areaD + '" fill="url(#' + gid + ')" stroke="none"/>';
            svg += '<path d="' + pathD + '" fill="none" stroke="#667eea" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>';

            months.forEach(function(m, i) {
                var x = pd + i * xStep;
                var y = yPos(m.score);
                var cr = m.score >= 60 ? "#60c060" : (m.score >= 40 ? "#f0d060" : "#e06060");
                svg += '<circle cx="' + x + '" cy="' + y + '" r="1.5" fill="' + cr + '" stroke="rgba(255,255,255,0.2)" stroke-width="0.3"/>';
            });
            svg += '</svg>';

            var labels = '<div class="kline-labels">';
            months.forEach(function(m, i) {
                if (i % 2 === 0 || i === months.length - 1) {
                    labels += '<span>' + m.label + '</span>';
                } else { labels += '<span></span>'; }
            });
            labels += '</div>';
            return svg + labels;
        }

        function drawAnnualTrend(years) {
            if (!years || years.length === 0) return '';
            var pw = 100, ph = 60, pd = 5;
            var cw = pw - pd * 2, ch = ph - pd * 2;
            var scores = years.map(function(y) { return y.score; });
            var minS = Math.min.apply(null, scores);
            var maxS = Math.max.apply(null, scores);
            var range = Math.max(maxS - minS, 10);
            var xStep = cw / (years.length - 1);
            function yPos(s) { return pd + ch - ((s - minS) / range) * ch; }

            var pathD = '';
            years.forEach(function(y, i) {
                var x = pd + i * xStep, y2 = yPos(y.score);
                if (i === 0) pathD += 'M' + x + ',' + y2;
                else pathD += ' L' + x + ',' + y2;
            });

            var svg = '<svg viewBox="0 0 ' + pw + ' ' + ph + '" xmlns="http://www.w3.org/2000/svg">';
            svg += '<path d="' + pathD + '" fill="none" stroke="#764ba2" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>';
            years.forEach(function(y, i) {
                var x = pd + i * xStep, y2 = yPos(y.score);
                var cr = y.score >= 70 ? "#60c060" : (y.score >= 45 ? "#f0d060" : "#e06060");
                svg += '<circle cx="' + x + '" cy="' + y2 + '" r="1.5" fill="' + cr + '"/>';
                svg += '<text x="' + x + '" y="' + (ph - 1) + '" font-size="3" fill="#888" text-anchor="middle">' + y.year + '</text>';
            });
            svg += '</svg>';
            return svg;
        }

        function drawRadar(data) {
            if (!data || data.length === 0) return '';
            var cx = 120, cy = 120, rr = 80;
            var svg = '<svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg">';

            for (var lvl = 1; lvl <= 5; lvl++) {
                var lr = (rr / 5) * lvl;
                var pts = '';
                for (var i = 0; i < 5; i++) {
                    var a = (Math.PI/2)*3 + (Math.PI*2/5)*i;
                    pts += (cx + lr * Math.cos(a)).toFixed(1) + ',' + (cy + lr * Math.sin(a)).toFixed(1) + ' ';
                }
                svg += '<polygon points="' + pts + '" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="0.5"/>';
            }

            for (var i = 0; i < 5; i++) {
                var a = (Math.PI/2)*3 + (Math.PI*2/5)*i;
                svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + rr * Math.cos(a)).toFixed(1) + '" y2="' + (cy + rr * Math.sin(a)).toFixed(1) + '" stroke="rgba(255,255,255,0.08)" stroke-width="0.5"/>';
            }

            var wxClr = {"金":"#f0d060","木":"#60c060","水":"#5090e0","火":"#e06060","土":"#c09050"};
            var maxV = Math.max(1, Math.max.apply(null, data.map(function(d) { return d.value; })));
            var pts = '';
            data.forEach(function(d, i) {
                var a = (Math.PI/2)*3 + (Math.PI*2/5)*i;
                var v = d.value / maxV;
                pts += (cx + rr * v * Math.cos(a)).toFixed(1) + ',' + (cy + rr * v * Math.sin(a)).toFixed(1) + ' ';
            });
            svg += '<polygon points="' + pts + '" fill="rgba(102, 126, 234, 0.2)" stroke="#667eea" stroke-width="1.2"/>';

            data.forEach(function(d, i) {
                var a = (Math.PI/2)*3 + (Math.PI*2/5)*i;
                var v = d.value / maxV;
                var px = cx + rr * v * Math.cos(a);
                var py = cy + rr * v * Math.sin(a);
                svg += '<circle cx="' + px.toFixed(1) + '" cy="' + py.toFixed(1) + '" r="3" fill="' + (wxClr[d.axis] || "#667eea") + '"/>';
                var lx = cx + (rr + 18) * Math.cos(a);
                var ly = cy + (rr + 18) * Math.sin(a);
                svg += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) + '" font-size="9" fill="#b0b0c0" text-anchor="middle" dominant-baseline="middle">' + d.axis + '</text>';
            });
            svg += '</svg>';
            return svg;
        }

        function shareReport() {
            if (navigator.share) {
                navigator.share({ title: SHARE_TEXT.split(" #")[0], text: SHARE_TEXT, url: window.location.href }).catch(function(){});
            } else {
                var ta = document.createElement("textarea");
                ta.value = SHARE_TEXT + "\\n" + window.location.href;
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand("copy"); var btn = document.querySelector(".share-btn"); var orig = btn.textContent; btn.textContent = "已复制！"; setTimeout(function() { btn.textContent = orig; }, 2000); } catch(e) {}
                document.body.removeChild(ta);
            }
        }
        render();
    })();
    </script>
</body>
</html>""".replace("$report_json", report_json).replace("$share_text_escaped", share_text_escaped)

    t = Template(template_str)
    return t.safe_substitute(
        og_title=og_title,
        og_desc=og_desc,
        og_desc_short=og_desc_short,
        reading_id=reading_id,
    )

@router.post("/api/report/generate")
async def generate_report(req: GenerateReportRequest):
    """Generate a new fortune report from user profile."""
    try:
        bazi_result = _engine.calculate(
            year=req.birth.year,
            month=req.birth.month,
            day=req.birth.day,
            hour=req.birth.hour,
            minute=req.birth.minute,
            city=req.birth.city,
            gender=req.birth.gender,
        )

        report_data = generate_report_data(
            bazi_result,
            birth=req.birth.model_dump(),
            name=req.birth.name,
        )

        return report_data

    except Exception as e:
        logger.exception("生成报告失败")
        raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")
