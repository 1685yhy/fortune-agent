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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator

from src.security.auth import require_user
from src.engines.bazi import BaziEngine, BaziResult, TIANGAN, DIZHI, WUXING_TG, WUXING_DZ

logger = logging.getLogger(__name__)

router = APIRouter(tags=["visual_report"])

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

#: 对外域名（与 share.py 的 `_BASE_URL` 同值：同一份报告网页在两个模块里都要生成
#: 绝对链接）。k76 起本模块也用它拼分享通道地址。
_PUBLIC_BASE = "https://fortune.talcloud.com"

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

    # ── k80-必修1 第 1 层（**根因层**）：入参白名单 ─────────────────────────
    # 改前 `gender: str = "男"` **无任何校验**：任意字符串原样进 `profile.gender`
    # 落盘，再被报告页内嵌进 `<script>`（匿名分享页 `/share/{id}` 上执行攻击者 JS
    # —— 存储型 XSS）。这里在**入口**上把它收敛到白名单值（`男`/`女`/`unknown`，
    # 与 `birth_contract.normalize_gender` / `person_dao` / 前端 gender 契约同词表）。
    #
    # 为什么是"归一化"而不是"422 拒收"：本仓既有的性别契约就是
    # `'男' | '女' | 'unknown'` 三值（见 `miniprogram/tests/gender_contract.test.js`
    # 与 `src/storage/person_dao._normalize_gender`），前端历史契约还会送
    # `male`/`female`/1/0 —— 拒收会把正当调用方打挂；"未知"是**受控值**，
    # 不是"把用户给的串转发下去"。白名单之外的一切（含攻击载荷）都变成
    # `unknown`，不可能再带出任何字符。
    @field_validator("gender")
    @classmethod
    def _gender_in_whitelist(cls, v):
        from src.api.birth_contract import normalize_gender
        return normalize_gender(v)

    #: 展示用字段的长度上限（同类根因：`name` 也是**无校验的自由文本**，改前能长到
    #: 任意长度并直接进 `<title>`/og 标签）。这里只做"长度 + 控制字符"的卫生，
    #: 内容一律靠输出编码层（`html_attr_text` / `esc()`）保证不被解释成 HTML/JS。
    @field_validator("name", "city")
    @classmethod
    def _clean_short_text(cls, v, info):
        import re as _re
        text = str(v or "")
        # 控制字符（含 NUL/换行）在标题与地名里没有任何正当用途
        text = _re.sub(r"[\x00-\x1f\x7f]", "", text)
        limit = 32 if info.field_name == "name" else 64
        return text[:limit]


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
    owner_uid: str = "",
) -> dict:
    """Generate complete report data from a BaziResult.

    owner_uid（k76）：报告归属人。非空时以 AES 密文落 `owner_enc` 字段，
    供 `assert_report_owner()` 做归属校验；留空（老调用方）则报告归属未知
    → 本人读路径一律 403（fail-closed，见 `_LEGACY_OWNER_UNKNOWN`）。
    """
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

    # k76 归属：只在有归属人时落密文（老调用方不传 → 不写字段 = 归属未知）
    if owner_uid:
        try:
            from src.security.encryption import DataEncryptor
            report[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
        except Exception as e:
            # 加密不可用 → 宁可不落归属（读路径 fail-closed 会拒），也不落明文 user_id
            logger.warning("报告归属落库失败（报告将按'归属未知'处理）: %s", e)

    # Store to disk
    # k80-M3：「路径怎么拼」**只有一份实现**（`report_path_in`）—— 改前这里有第二处
    # 内联的「目录常量 + 拼接文件名」，k79 的声明"路径唯一实现"与代码对不上
    # （写路径与读路径各拼各的，正是"同一件事两份实现"的老毛病）。现在读写同源。
    report_path = report_path_in(_DATA_DIR, reading_id)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def report_path_in(data_dir, reading_id: str) -> Path:
    """报告文件路径（**唯一**的"路径怎么拼"实现，k79-M2）。

    `visual_report`（本人读）与 `api/share.py`（分享读）共用它，各传自己的目录常量。
    """
    return Path(data_dir) / f"{reading_id}.json"


def load_report_from(data_dir, reading_id: str) -> Optional[dict]:
    """**加载一份报告 JSON 的唯一实现**（k79-M2 收敛点）。

    `data_dir` 由调用方**传入**（而不是本函数去读某个模块的 `_DATA_DIR`）—— 这样
    `api/share.py` 可以带着**自己的**目录常量复用这同一段实现，同时两个模块的目录
    常量仍能被测试各自 monkeypatch（`share._DATA_DIR` / `visual_report._DATA_DIR`），
    k78 担心的"委托会让测试隔离失效"因此**不成立**：隔离取决于"调用点读的是谁的
    模块全局"，而调用点在各自模块内（`share._load_report` 传 `share._DATA_DIR`）。

    为什么必须收敛（k79-M2）：k78 让两处"可展示性**判据**"共用同一函数，却让
    "**加载**"各留一份副本 —— 这正是本批 Critical（必修1）的土壤：同一件事有两份
    实现，改一处漏一处。加载的语义（根必须是对象、解析失败当不存在）与判据一样
    属于"报告 JSON 的契约"，只能有一份。

    k78：**根 JSON 必须是对象**，否则与"不存在"同等对待（返回 None → 调用方 404）。
    改前直接把 `json.loads` 的结果返回：磁盘上若是一份畸形 JSON（`[1,2,3]` /
    `"just a string"` / `12345`），`if not report` 对**真值非空**的畸形值不成立，
    于是继续往渲染/归属里走 → AttributeError/TypeError → **500**。
    "报告不存在"是 404 的语义；"文件在但内容不是一份报告"同样属于**不可展示**，
    不是服务器内部错误。
    """
    try:
        report_path = report_path_in(data_dir, reading_id)
        if not report_path.exists():
            return None
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        # k79：**本函数永不抛**（"取不到 / 读不了 / 不是一份报告"一律 None → 404）。
        # 覆盖的不只是 JSON 解析失败，还包括**路径本身**的异常：超长文件名
        # （`OSError: File name too long`）、含 NUL 字节（`ValueError`）、权限等。
        # 实测改前 `/api/share/{300 个字符的 id}` → **500**（`exists()` 抛 OSError
        # 逃出 try，因为旧代码只 try 了 `json.loads`）—— 与"不存在 → 404"的契约不符。
        return None
    return data if isinstance(data, dict) else None


def load_report(reading_id: str) -> Optional[dict]:
    """Load a stored report by reading_id（本模块目录；实现在 `load_report_from`）。"""
    return load_report_from(_DATA_DIR, reading_id)


def as_mapping(value) -> dict:
    """把报告里的"应该是对象"的字段安全当映射用；不是对象 → 空 dict。

    k79（必修1 的根因之一）：`report.get("profile", {})` 在**键存在且值为 `null`**
    时返回 `None`（默认值不生效），接着 `profile.get(...)` 就 AttributeError → 500
    （复审实测 `{"profile": null}`）。所以取值**不能靠默认值**，只能靠"取值后判类型"。
    全仓读报告 JSON 的消费点统一走本函数，不再各写各的 isinstance。
    """
    return value if isinstance(value, dict) else {}


def as_text(value, default: str = "") -> str:
    """把报告字段安全当**文本**用；不是字符串 → 默认值。

    ⚠️ **不做 `str()` 兜底**：把 `12345` / `{'a':1}` / 嵌套列表 repr 成文案念给用户，
    既不是一句人话、又可能把结构里的字段名/内容漏到页面上；宁可退回中性文案。
    数字/布尔/None/容器 → 一律 `default`。规则只有一条，所有消费点一致。
    """
    return value if isinstance(value, str) else default


#: `json_safe` 的递归深度上限：超过即截断为 None（见函数说明）。
_JSON_SAFE_MAX_DEPTH = 200


def json_safe(value, _depth: int = 0):
    """把报告数据整理成**可 JSON 序列化**的结构（非有限浮点 NaN/Infinity → None）。

    k79-必修1（复审清单里的 `insights=[NaN]`）：`json.loads` **默认接受** NaN /
    Infinity（RFC 8259 之外的扩展），但 Starlette 的 JSONResponse 用
    `allow_nan=False` 序列化 ⇒ 直接把原 dict 返回给客户端时，一份含 `NaN` 的报告
    在 `/api/report/{id}` 上 **500**（复审实测，见本批改前 HTTP 表）；同理
    `_card_from_report` 的 `bazi_data.wuxing` / `shishen` 里带 NaN 也会把
    `/api/share/{id}` 打成 500。

    NaN 是**值层面**的缺陷（不是"这不是一份报告"）⇒ 按本批口径**降级**为 null：
    既不 500、也不 404。HTML 页那条路不需要它（NaN 在 JS 里是合法字面量），
    但两条路都过一遍，规则只有一条。

    深度超过 `_JSON_SAFE_MAX_DEPTH` 的嵌套截断为 None（报告渲染不出 200 层嵌套；
    截断而不是递归到爆栈 —— 本函数必须**永不抛**）。
    """
    if _depth > _JSON_SAFE_MAX_DEPTH:
        return None
    if isinstance(value, float):
        # NaN / ±Infinity → None（math.isfinite 对 NaN 返回 False）
        import math as _math
        return value if _math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v, _depth + 1) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    # 其它类型（bytes / set / 自定义对象 / 超大整数等）→ 字符串化（可序列化）
    return str(value)


#: 内嵌 `<script>` 时必须转义的字符（k80-必修1 的**输出编码层**）：
#:   - `<` `>` `&`：`<`/`>` 让 `</script>` 之类的串**提前闭合脚本标签**（存储型 XSS 的正门），
#:     `&` 是 HTML 实体入口（配合 `innerHTML` 的二次解析）；
#:   - U+2028 / U+2029（下面元组里的两个**行分隔符**字符）：JS 里它们算换行，
#:     出现在字符串字面量中即 SyntaxError（整页脚本死掉 = 白屏）；
#:   - `$`：本页的模板替换用 `string.Template`，而 `report_json` 是**先 `.replace()`
#:     进模板、再交给 `safe_substitute`** 的 —— 用户数据里若含 `$og_title` 之类，
#:     会被二次替换（把别处的占位值注入进来）。转义 `$` 后这一整类都不成立。
#:
#: 用 `\uXXXX` 形式（而不是 `&lt;` 之类实体）：这些串处在 **JS 字面量**里，
#: `<` 经 JS/JSON 解析后**正好等于** `<`，语义零变化；而实体只会变成字面文本。
_SCRIPT_ESCAPES = (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                   (" ", "\\u2028"), (" ", "\\u2029"),
                   ("$", "\\u0024"))


def json_for_script(value) -> str:
    """把对象序列化成**可安全内嵌进 `<script>`** 的字符串（k80-必修1 输出编码层）。

    通用修法：**覆盖所有字段**，不只是 `gender` —— 任何字段（`name`、`insights`、
    LLM 产出、将来的新字段）变脏都不该能逃出脚本标签。

    改前 `_build_report_html` 把 `json.dumps(...)` **原样**塞进
    `var REPORT = …;`：`profile.gender = '</script><script>alert(document.cookie)</script>'`
    即提前闭合脚本、注入攻击者 JS，在**匿名公开页** `/share/{id}` 上执行
    （k80 终验实测：页面里 `<script` 出现 2 次，正常应为 1 次）。

    与 `json_safe` 的分工：`json_safe` 管**值**（NaN/Infinity/超深嵌套 → 可序列化），
    本函数管**编码**（序列化后的字面文本在 HTML 的 `<script>` 里不构成逃逸）。
    """
    text = json.dumps(json_safe(value), ensure_ascii=False)
    for raw, escaped in _SCRIPT_ESCAPES:
        text = text.replace(raw, escaped)
    return text


def js_string_literal(value) -> str:
    """把任意值变成**一段合法的 JS 字符串字面量**（含引号）。

    用于 `var X = <literal>;` 这种"值要进 JS 字符串"的位置。
    改前的做法是 `str(v).replace('"', '&quot;')`（只挡双引号）——反斜杠、换行、
    `</script>` 全都能逃出去：`share_text` 里一个换行就把 `var SHARE_TEXT = "…";`
    变成未闭合字符串 ⇒ **整页 SyntaxError 白屏**（k80 终验实测 `html_len=0`）。
    """
    return json_for_script(value if isinstance(value, str) else str(value))


def html_attr_text(value) -> str:
    """把任意值变成可安全放进 **HTML 文本/属性**的字符串（`& < > " '` 全转义）。"""
    import html as _html
    return _html.escape(value if isinstance(value, str) else str(value), quote=True)


def first_insight_text(report, limit: int = 0) -> str:
    """报告里**第一条可展示的洞察文本**；没有则空串。

    k79（必修1 的根因之一）："取 `insights[0]` 再截断"这段逻辑此前在**三处各写一份**
    —— `get_report_page` 的 share_text、`/share/{reading_id}` 的 share_text、
    `share._card_from_report` 的摘要；其中两处**没有类型兜底**，`insights=[12345]`
    即 TypeError → 500（复审实测）。k78 只改了其中**一处**（`visual_report` 那处），
    另外两处照旧 —— "同一句话的副本没跟着改"。现在只剩这一份：

      - `insights` 缺失/非列表 → 空串；
      - 元素非字符串（数字/布尔/None/嵌套对象/NaN）、或字符串全空白 → **跳过**，
        取第一条**真的是文本**的（不是把 repr 当文案给用户看）；
      - `limit > 0` 时按字符截断。

    消费点：`_build_report_html` 的 og 描述、`get_report_page` 与
    `/share/{reading_id}` 的 share_text、`share._card_from_report` 的摘要。
    """
    insights = report.get("insights") if isinstance(report, dict) else None
    if not isinstance(insights, list):
        return ""
    for item in insights:
        if isinstance(item, str) and item.strip():
            return item[:limit] if limit and limit > 0 else item
    return ""


#: 报告**可渲染性**判据（k78-必修3 引入、k79-必修1 按**全部消费点**重写的单一事实源）。
#:
#: 判据分两段，边界写死（这是本批最该说清的一件事）：
#:   ① **结构**：根必须是对象；`profile` / `bazi_analysis` / `charts` 存在即必须是
#:      对象（消费点当映射用）、`insights` 存在即必须是列表（当列表用）。
#:      —— 只有**结构**不可能，才判"不可展示"。
#:   ② **内容**：连一个可展示的条目都没有（`profile` 为空 **且** 没有任何字符串
#:      洞察）⇒ 空壳报告，不可展示（k78 已有这条，本批把"insights 非空但全不是文本"
#:      一并归入 —— 那种报告渲染出来是空白页，与空壳无实质差别）。
#:
#: ⚠️ **值层面**的异常（元素是数字/布尔/None/NaN、`profile.name` 是数字、超长或
#: 深嵌套、字段缺失）**不**判为不可展示 —— 它们由各消费点**类型兜底降级**
#: （摘要退回中性文案、名字退回中性标题、图表空着），既不 500 也不 404。
#: 依据：线上 4 份真实报告（`data/reports/*.json`）结构均为 profile/bazi_analysis/
#: charts = 对象 + insights = 字符串列表；判据不要求字段齐全（老报告缺可选字段照常渲染）。
def report_shape_problem(report) -> str:
    """报告结构是否**可展示**：返回问题描述；可展示则返回空串。

    调用口径（与"不存在 → 404"同款）：**不可展示 = 404**，绝不 500。

    ⚠️ 本判据是**必要不充分**条件，别把它当"渲染安全"的全部保证（k79-必修1）：
    它是 404 与否的**唯一**决策点，但"不 500"由**消费点全都不抛**保证 —— 见
    `_REPORT_JSON_CONSUMERS` 的清单与 `tests/test_k79_...::test_no_5xx_...`
    的穷举实测。判据漏判一个形态，后果只是"这一页降级显示"，不会是 500。
    """
    if not isinstance(report, dict):
        return "报告内容不是一个对象"
    # ① 结构：容器字段（存在即必须是正确容器；缺失 = 老报告可选字段，不判问题）
    for field, kind, label, want in (
            ("profile", dict, "个人信息段（profile）", "对象"),
            ("bazi_analysis", dict, "八字分析段（bazi_analysis）", "对象"),
            ("charts", dict, "图表段（charts）", "对象"),
            ("insights", list, "结论段（insights）", "列表")):
        value = report.get(field)
        if value is None:
            continue
        if not isinstance(value, kind):
            return f"报告的{label}不是{want}"
    # ② 内容：profile 为空 **且** 没有任何可展示的洞察文本 ⇒ 空壳
    if not report.get("profile") and not first_insight_text(report):
        return "报告没有任何可展示内容"
    return ""


#: 读取本份报告 JSON 的**全部消费点**（k79-必修1 逐处核对，全仓搜索得出）。
#:
#: 为什么要有这份清单：k78 的判据是**按一个消费点**写的（"`_build_report_html` 读
#: `profile.name` 与 `insights[0]`"），却在 4 个路由上声称共用 —— 其余消费点
#: （`_card_from_report` 读 `bazi_analysis`、`/share/{id}` 的 share_text 直接下标
#: `insights[0][:50]`）没被覆盖。本批的处置**不是**"把判据补全到与清单一样长"
#: （那只是把同一份需求写第二遍，下次新增消费点还会漏），而是：
#:   ① 消费点**各自全都不抛**（类型兜底）—— 判据漏了也只是降级，不会 500；
#:   ② 判据只回答"这是不是一份报告"；
#:   ③ 这份清单由 `tests/test_k79_...::test_consumer_inventory_is_closed` **机器穷举**
#:      —— 出现新的读报告 JSON 的文件/符号即红，逼着人来更新清单。
#:
#: 逐处（字段 = 该处真正读到的字段）：
#:   1. `api/visual_report.py::get_report_json`（`GET /api/report/{id}`）
#:      → 整份返回给客户端（鉴权+归属后）。读：全部字段。
#:   2. `api/visual_report.py::get_report_page`（`GET /report/{id}`）
#:      → `profile.name` / `insights[0]`（share_text，经 `first_insight_text`）。
#:   3. `api/visual_report.py::_build_report_html`（上面两处 + 分享页共用）
#:      → `profile.name` / `insights[0]` / `reading_id`（Python 侧）；
#:        页面内嵌 JS 另读 `profile.*` / `bazi_analysis.*` / `charts.*` /
#:        `insights[]` / `recommendations[]` / `generated_date`（JS 侧同样全兜底）。
#:   4. `api/visual_report.py::report_owner_tag` / `assert_report_owner`
#:      → `owner_enc`、`reading_id`（仅日志）。
#:   5. `api/share.py::get_share_metadata`（`GET /api/share/{id}`，非数字 id 分支）
#:      → 经 `_card_from_report`：`reading_id` / `profile.name` / `profile.bazi` /
#:        `profile.day_master` / `bazi_analysis.{geju,yongshen,wuxing,shishen}` /
#:        `insights[0]`。
#:   6. `api/share.py::get_share_page_by_reading`（`GET /share/{id}`，匿名）
#:      → `generated_at`（TTL）、`profile.name|birth_date|birth_info`（剥离）、
#:        `insights[0]`（share_text）、其余整份内嵌。
#:   7. `storage/dao.py::purge_report_files`（`UserDAO.cancel_user` /
#:      `purge_account_data` → 注销接口）
#:      → `owner_enc`、`reading_id`（决定要删的 PNG 名）。
#:
#: ## 这份清单**拦得住什么**、**仍在面外**什么（k80-必修3：如实写清楚）
#:
#: 机器门禁 = `tests/test_k80_xss_privacy_final.py::TestConsumerInventoryGateIsNotBypassable`。
#: 扫描面（k80 扩面）：`src/**/*.py`（含将来的 `src/plugins/`）+ **`scripts/**/*.py`**
#: + **仓根 `*.py`**；排除 `tests/`、`data/`。
#:
#: 拦得住的形态（每一条都有注入证明，见该测试类的 param 列表）：
#:   - **符号引用**：直接 `Name`/`Attribute` 引用本清单任一符号（含 `load_report`、
#:     `report_shape_problem`、`json_for_script` 等）；
#:   - **路径拼接**：`/ "reports"`、`os.path.join(..., "reports", ...)`、
#:     `"data/reports/" + rid + ".json"` 这类**静态可折叠**的路径串；
#:   - **动态取函数名**：`getattr(m, "load_" + "report")`（静态折叠成 `load_report`
#:     → 名字里带 report → 红）、`getattr(m, suffix + "report")`（折叠不出来，但字面
#:     里带 report → 红）、`getattr(m, "load_report_from")`（常量名带 report → 红）；
#:   - **动态导入**：`importlib.import_module("src.api." + "visual_report")`。
#:
#: **仍在面外**（如实列出，别当成已覆盖）：
#:   - **运行期**才拼出来的路径/函数名：从数据库、配置、环境变量读来的目录或符号名，
#:     `.format()`/`%`/`join()` **在运行时**产出的串（扫描器只做静态折叠）；
#:   - **跨函数/跨模块的数据流**：把目录或符号名当参数传来传去，最终在第 3 个文件里
#:     用到（扫描器不做过程间分析）；
#:   - **间接消费**：新代码不碰路径也不碰这些符号，而是调用 `GET /api/report/{id}`
#:     这类**接口**（HTTP 层）拿到数据 —— 扫描器看的是源码符号，不是调用图；
#:   - **非 .py 的消费点**：`.js` / `.sh` / `.md` 里的脚本直接读 `data/reports/*.json`；
#:   - **tests/ 与 data/ 目录**（有意排除：测试本来就要造报告语料）。
#:
#: ⇒ 结论：**"源码里静态可折叠的报告路径/符号引用"这一面不会逃逸；上面几类仍在面外**，
#: 新增消费点时别指望门禁替你把关 —— 它只负责"让你看见"。
_REPORT_JSON_CONSUMERS = (
    "src/api/visual_report.py::get_report_json",
    "src/api/visual_report.py::get_report_page",
    "src/api/visual_report.py::_build_report_html",
    "src/api/visual_report.py::assert_report_owner",
    "src/api/share.py::get_share_metadata",
    "src/api/share.py::get_share_page_by_reading",
    "src/storage/dao.py::purge_report_files",
)


# ─── k76 报告归属（读接口鉴权的单一事实源）─────────────────────────────
# 背景（k72 独立核查 A2）：`data/reports/{id}.json` 明文含姓名+八字+出生日期，
# 而 `GET /api/report/{id}`、`GET /report/{id}` **无鉴权**，拿到短 id 即可读。
# 控制方裁定按「接口安全红线」必修：**读自己的报告要鉴权 + 归属校验**；
# 被分享的内容走分享通道（`/share/{id}`，匿名可读但剥离个人信息），两条路径分开。
#
# 归属怎么存：写盘时把 `owner_uid` 经**既有** `DataEncryptor` AES 加密后落
# `owner_enc` 字段（不新增算法/密钥）。老报告（无该字段）= 归属未知。
#: 报告里记录归属的字段名。归属未知（老报告没这个字段）在"本人读"路径上的
#: 处置是**拒绝**（fail-closed）：依据 `docs/FUNCTION_GAP_AUDIT.md` 记录该 web
#: 报告体系"❌ 未用"（小程序读报告走 `/api/reports/{id}`，是另一套已带归属校验
#: 的接口），挡掉老报告不中断任何在用链路；而把它们对"任意已登录用户"开放，
#: 等于把 k72-A2 的越权面留着。
_REPORT_OWNER_FIELD = "owner_enc"


def report_owner_tag(report: dict) -> str:
    """报告里记录的归属标记（解密后的 user_id）；无记录/解不开 → 空串。"""
    enc = (report or {}).get(_REPORT_OWNER_FIELD) or ""
    if not enc:
        return ""
    try:
        from src.security.encryption import DataEncryptor
        return DataEncryptor().decrypt(enc) or ""
    except Exception:
        return ""


def assert_report_owner(report: dict, uid: str) -> None:
    """归属校验：报告属于 uid 才放行，否则 403（与 `ensure_owner` 同口径）。

    - 归属未知（老报告 / 解密失败）→ **403**，不放行给任何登录用户；
    - 归属存在但不等于 uid → 403。
    分享通道不走本函数（见 `share.py` 的 `/share/{reading_id}`）。
    """
    owner = report_owner_tag(report)
    if not owner or owner != uid:
        logger.warning(
            "鉴权拒绝 403: 越权访问报告 reading_id=%s owner=%s token_user=%s",
            (report or {}).get("reading_id", ""), owner or "(未知)", uid or "(空)")
        raise HTTPException(status_code=403, detail="无权访问该报告")


# ─── API endpoints ─────────────────────────────────────────────

@router.get("/api/report/{reading_id}")
async def get_report_json(reading_id: str, uid: str = Depends(require_user)):
    """Get report data as JSON（k76：必须登录 + 归属校验）。

    被分享的报告不走本接口 —— 分享通道是 `GET /share/{reading_id}`
    （匿名可读、已剥离个人信息）。两条路径分开是控制方拍板的要求。
    """
    report = load_report(reading_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告未找到")
    # k78-必修3：文件在、内容却不是一份可展示的报告 ⇒ 与"不存在"同款（404），不是 500
    problem = report_shape_problem(report)
    if problem:
        logger.warning("报告内容不可展示（404）reading_id=%s: %s", reading_id, problem)
        raise HTTPException(status_code=404, detail="报告未找到")
    assert_report_owner(report, uid)
    # k79：返回前过 `json_safe`（NaN/Infinity → null）—— 否则 Starlette 用
    # allow_nan=False 序列化，一份含 NaN 的报告在这里 **500**（复审实测）。
    return json_safe(report)


@router.get("/report/{reading_id}")
async def get_report_page(reading_id: str, uid: str = Depends(require_user)):
    """Get the rendered HTML report page（k76：必须登录 + 归属校验）。

    403/401 用与 404 同款的极简页返回（浏览器直接访问时人可读）；
    接口状态码语义保持准确（未授权 = 403）。
    """
    report = load_report(reading_id)
    if not report:
        return HTMLResponse(_SIMPLE_NOT_FOUND_HTML, status_code=404)
    # k78-必修3：畸形内容 = 不可展示 ⇒ 与"不存在"同款 404 页（改前会 KeyError/TypeError → 500）
    problem = report_shape_problem(report)
    if problem:
        logger.warning("报告页内容不可展示（404）reading_id=%s: %s", reading_id, problem)
        return HTMLResponse(_SIMPLE_NOT_FOUND_HTML, status_code=404)
    try:
        assert_report_owner(report, uid)
    except HTTPException as e:
        return HTMLResponse(_simple_error_html(e.detail), status_code=e.status_code)

    # Share text for social media（k79：走 `first_insight_text` 单一实现 —— 这段
    # "取 insights[0] 并截断"的逻辑此前三处各一份，只改了一处，副本即漂移）
    share_text = (
        f"我的2026运势报告来了！{first_insight_text(report, 50)}..."
        f" #易理明灯 #AI命理"
    )

    html = _build_report_html(report, share_text)
    return HTMLResponse(html)


_SIMPLE_NOT_FOUND_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>报告未找到</title></head><body>"
    "<h1>报告未找到</h1><p>该命运报告不存在或已被删除。</p></body></html>"
)


def _simple_error_html(detail: str) -> str:
    """报告页的极简错误页（detail 由本模块常量/写死文案产生，仍做转义兜底）。"""
    import html as _html
    msg = _html.escape(str(detail or "无权访问该报告"))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>无法查看</title></head><body>"
        f"<h1>无法查看</h1><p>{msg}。</p>"
        "<p>如果这是别人分享给你的报告，请用分享链接打开。</p></body></html>"
    )


def _build_report_html(report: dict, share_text: str, share_url: str = "") -> str:
    """Build the full HTML report page with embedded data.

    Uses string.Template to avoid f-string conflicts with JavaScript/CSS braces.

    share_url（k76）：该页对外分享时使用的地址。**默认为分享通道**
    `/share/{reading_id}`（匿名可读）——因为本页 `/report/{reading_id}` 自
    k76 起需登录+归属校验，把本页 URL 分享出去对方会打不开。og:url 也用它，
    否则微信抓取会抓到一个 401 页、卡片变空。

    k78-必修3 / k79-必修1：**本函数不抛异常**（Python 侧与页面内嵌 JS 侧都不抛）。
    改前 `report["profile"]["name"]` 直接下标 —— 畸形报告（如
    `{"generated_at":["x"]}`，即没有 profile/insights 的空壳）会 KeyError → 500。
    现在：① 入口先过 `report_shape_problem()`，不可展示则返回错误页（**不 500**，
    与调用方的 404 口径一致）；② 取字段一律走 `.get()` + 类型兜底；③ 内嵌 JS 对
    `profile` / `bazi_analysis` / `charts` / `insights` / `recommendations` **逐个
    兜底**（改前 `r.profile.name`、`r.charts.wuxing_radar.forEach` 无保护 —— 一份
    缺 `charts` 的报告会让页面在浏览器里 TypeError 白屏，虽不是服务端 500，
    但同属"报了可展示却渲染不出来"，k79 一并收掉）。
    """
    import json as _json
    from string import Template

    problem = report_shape_problem(report)
    if problem:
        return _simple_error_html(problem)

    try:
        report_json = json_for_script(report)
    except Exception as e:
        # k79：报告**能解析但序列化不了**时（超深嵌套触到递归上限等）不再 500 ——
        # 退成"只带可展示文本的最小载荷"，页面照常出（判据只保证结构，兜底在这里）。
        logger.warning("报告 JSON 重新序列化失败，降级为最小载荷: %s", e)
        report_json = json_for_script({
            "generated_date": as_text(report.get("generated_date")),
            "insights": [first_insight_text(report)],
        })
    # 分享文案由调用方给定，允许任何类型（f-string/常量都会给 str）；
    # 非 str 一律按空串处理，绝不在本函数里抛。
    share_text_escaped = js_string_literal(share_text or "")

    # Build OG title（k78/k79：全部走 .get() + 类型兜底，缺字段不再 KeyError）
    profile = as_mapping(report.get("profile"))
    pname = as_text(profile.get("name"))
    if pname in ("", "用户", "anonymous"):
        og_title = "我的2026运势报告 | 易理明灯"
    else:
        og_title = f"{pname}的命运报告 | 易理明灯"

    first = first_insight_text(report)
    og_desc = first[:100] or "AI命理分析报告"
    og_desc_short = first[:80] or "AI命理分析报告"
    reading_id = as_text(report.get("reading_id"))

    # k80-必修1（输出编码层 · HTML 侧）：og:* / title / description 落在 **HTML 文本与属性**
    # 上下文里，必须 HTML 转义。改前只对 share_text 做了半套（只换 `"`）——
    # `profile.name` 走 og_title 直接进 `<title>`（RCDATA：`</title><script>…` 即逃逸）
    # 与 `<meta content="…">`（属性逃逸），是同一 Critical 的第二条通路。
    og_title_h = html_attr_text(og_title)
    og_desc_h = html_attr_text(og_desc)
    og_desc_short_h = html_attr_text(og_desc_short)
    # 分享地址两条通道各一份：og:url 进 HTML 属性；JS 里那份进字符串字面量。
    share_url_js = js_string_literal(
        share_url or f"{_PUBLIC_BASE}/share/{reading_id}")
    og_url_h = html_attr_text(share_url or f"{_PUBLIC_BASE}/report/{reading_id}")

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
    <meta property="og:url" content="$og_url">
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
        var SHARE_TEXT = $share_text_escaped;
        // k76：分享出去的链接必须是**分享通道**（/share/{id}，匿名可读）；
        // 本页（/report/{id}）自本批起需登录+归属校验，把它分享给别人等于给死链。
        var SHARE_URL = $share_url;

        /*: ── k80 兜底工具（服务端判据只保证"结构"，**值层面**一律在这里降级）──
           三条原则（与 Python 侧 `as_text` / `as_mapping` / `first_insight_text`
           同一套，别再各写各的）：
             ① **容器层**：不是对象/数组 → 当空（k79 已有）；
             ② **元素层**（k80-必修2 的根因）：数组**元素**不是对象、字段缺失/类型
                错 → 该元素降级或跳过，**绝不让一个坏元素把整页渲染打死**。
                k79 只补了容器层：`charts.wuxing_radar=[1,"x",null]` 在
                `d.value` / `item.axis` 上仍然 TypeError → 白屏（复审实测
                `html_len=0`）；
             ③ **不把 repr 当文案**：对象/数组/NaN/Infinity 一律**不**渲染成
                `[object Object]`／`NaN`（与 `as_text` 的"数字/容器 → 退回中性
                文案"同原则；k80-M4 报的 `rec.action` 出 `[object Object]`、
                `annual_trend` 缺 score 出 `MNaN,NaN` 都属这类）。 */
        function isObj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
        function obj(v) { return isObj(v) ? v : {}; }
        function arr(v) { return Array.isArray(v) ? v : []; }
        function txt(v) {
            if (typeof v === 'string') return v;
            if (typeof v === 'number' && isFinite(v)) return String(v);
            return '';
        }
        function num(v) {
            if (typeof v === 'number') return isFinite(v) ? v : null;
            if (typeof v === 'string' && v.trim() !== '') {
                var n = Number(v);
                return isFinite(n) ? n : null;
            }
            return null;
        }
        /*: **渲染汇点转义**（k80-必修1 的第 4 层，这条不修的话前 3 层都白修）：
           值最终进 `innerHTML`，必须转义。只做"脚本标签逃逸"是不够的 —— payload
           被 `<` 编码后仍是**运行时的 HTML 字符串**（如 img+onerror），拼进
           innerHTML 照样执行（innerHTML 插入的 script 元素不执行，但事件属性会
           执行）。所以每个插入点都过 esc()。
           （本注释刻意不写尖括号形态的"script 标签"字样：它是**页面文本**的一部分，
           会让"页面里有几个脚本标签"这种计数口径失真。） */
        function esc(v) {
            return txt(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function render() {
            var r = obj(REPORT);
            // k79：字段逐个兜底（改前 p = r.profile 无保护，报告缺 profile/
            // bazi_analysis/charts 时下面 p.name / ba.geju / r.charts.* 会
            // TypeError → 页面白屏）。服务端判据漏判的形态在这里降级显示，
            // 绝不把整页打死。
            var p = obj(r.profile);
            var ba = obj(r.bazi_analysis);
            var ch = obj(r.charts);
            var insights = arr(r.insights);
            var recs = arr(r.recommendations);
            // 元素层降级：只有"画得出来"的条目留下（数值必须是有限数）
            var radar = arr(ch.wuxing_radar).filter(isObj).map(function(d) {
                return { axis: txt(d.axis), value: num(d.value) };
            }).filter(function(d) { return d.value !== null; });
            var monthly = arr(ch.monthly_fortune).filter(isObj).map(function(m) {
                return { label: txt(m.label), score: num(m.score) };
            }).filter(function(m) { return m.score !== null; });
            var annual = arr(ch.annual_trend).filter(isObj).map(function(y) {
                return { year: txt(y.year), score: num(y.score) };
            }).filter(function(y) { return y.score !== null; });
            var recItems = recs.map(function(rec) {
                var o = obj(rec);
                return { time_window: txt(o.time_window), action: txt(o.action),
                         reason: txt(o.reason) };
            }).filter(function(x) {
                return x.time_window || x.action || x.reason;
            });
            var shensha = arr(ba.shensha).map(txt).filter(function(s) {
                return s.trim() !== '';
            });
            var html = '';

            html += '<div class="report-header">';
            html += '<div class="badge">命运报告 v5.0</div>';
            html += '<h1>' + (esc(p.name) || '我') + '的命运报告</h1>';
            html += '<div class="subtitle">' + esc(r.generated_date) + ' · AI智能生成</div>';
            html += '</div>';

            // Profile Card
            var genderText = txt(p.gender);
            html += '<div class="profile-card">';
            html += '<div class="bazi-display">' + esc(p.bazi) + '</div>';
            html += '<div class="day-master">日主 ' + esc(p.day_master) +
                    (genderText ? ' · ' + esc(genderText) : '') + '</div>';
            html += '<div style="margin-top:12px">';
            html += '<div class="row"><span class="label">出生</span><span class="value">' + esc(p.birth_info) + '</span></div>';
            html += '<div class="row"><span class="label">格局</span><span class="value">' + esc(ba.geju) + '</span></div>';
            html += '<div class="row"><span class="label">用神</span><span class="value">' + esc(ba.yongshen) + '</span></div>';
            html += '<div class="row"><span class="label">神煞</span><span class="value">' + (shensha.length ? esc(shensha.join('、')) : '无') + '</span></div>';
            html += '</div></div>';

            // Wuxing Analysis
            html += '<div class="section-title">五行能量分布</div>';
            html += '<div class="chart-card">';
            html += '<div class="radar-container">' + drawRadar(radar) + '</div>';

            // Legend
            html += '<div class="radar-legend">';
            var wxClr = {"金":"#f0d060","木":"#60c060","水":"#5090e0","火":"#e06060","土":"#c09050"};
            radar.forEach(function(item) {
                html += '<div class="radar-legend-item"><span class="dot" style="background:' + (wxClr[item.axis] || '#667eea') + '"></span>' + esc(item.axis) + ' ' + item.value + '</div>';
            });
            html += '</div>';

            // Wuxing bars
            html += '<div class="wuxing-bars">';
            var maxVal = Math.max(1, Math.max.apply(null, radar.map(function(x) { return x.value; })));
            radar.forEach(function(item) {
                var pct = Math.round((item.value / maxVal) * 100);
                html += '<div class="wuxing-bar-item wx-' + esc(item.axis) + '">';
                html += '<div class="bar-track"><div class="bar-fill" style="height:' + pct + '%"></div></div>';
                html += '<div class="bar-label">' + esc(item.axis) + '</div>';
                html += '<div class="bar-value">' + item.value + '</div></div>';
            });
            html += '</div></div>';

            // K-Line Chart
            html += '<div class="section-title">年度运势走势</div>';
            html += '<div class="chart-card">';
            if (monthly.length > 0) {
                html += '<h3>未来12个月运势趋势</h3>';
            }
            html += drawKLine(monthly);
            html += '</div>';

            // Annual Trend
            html += '<div class="chart-card">';
            html += '<h3>年运势大趋势</h3>';
            html += drawAnnualTrend(annual);
            html += '</div>';

            // Key Insights
            html += '<div class="section-title">核心洞察</div>';
            html += '<ul class="insight-list">';
            insights.forEach(function(insight) {
                // k79：只展示**文本**条目（与服务端 first_insight_text 同一原则：
                // 不把数字/对象的 repr 当文案）
                if (typeof insight === 'string' && insight.trim()) html += '<li>' + esc(insight) + '</li>';
            });
            html += '</ul>';

            // Recommendations
            html += '<div class="section-title">行动建议</div>';
            html += '<ol class="rec-list">';
            recItems.forEach(function(rec, idx) {
                html += '<li>';
                html += '<div class="rec-number">' + (idx + 1) + '</div>';
                if (rec.time_window) html += '<div class="rec-time">' + esc(rec.time_window) + '</div>';
                if (rec.action) html += '<div class="rec-action">' + esc(rec.action) + '</div>';
                if (rec.reason) html += '<div class="rec-reason">' + esc(rec.reason) + '</div>';
                html += '</li>';
            });
            html += '</ol>';

            // Footer
            html += '<div class="report-footer">';
            html += '<div class="disclaimer">由易理明灯生成 · 仅供娱乐参考</div>';
            html += '<div class="qr-placeholder">QR Code placeholder</div>';
            html += '<button class="share-btn" onclick="shareReport()">分 享</button>';
            html += '</div>';

            var app = document.getElementById('app');
            if (app) app.innerHTML = html;
        }

        function drawKLine(raw) {
            // k80-必修2 元素层兜底 + M4：本函数可能被任意形态调用，先归一再画。
            var months = arr(raw).filter(isObj).map(function(m) {
                return { label: txt(m.label), score: num(m.score) };
            }).filter(function(m) { return m.score !== null; });
            /* 少于两个点画不出线：`xStep = cw/(n-1)` 在 n=1 时是 Infinity →
               坐标 NaN → SVG 里出 `MNaN,NaN`（k80-M4 实测）。降级为"不画"，
               而不是画一条 NaN 折线。 */
            if (months.length < 2) return '';
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
                    labels += '<span>' + esc(m.label) + '</span>';
                } else { labels += '<span></span>'; }
            });
            labels += '</div>';
            return svg + labels;
        }

        function drawAnnualTrend(raw) {
            // k80：同 drawKLine —— 先归一（缺 score / score 非有限数的年份直接剔除）。
            var years = arr(raw).filter(isObj).map(function(y) {
                return { year: txt(y.year), score: num(y.score) };
            }).filter(function(y) { return y.score !== null; });
            if (years.length < 2) return '';
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
                svg += '<text x="' + x + '" y="' + (ph - 1) + '" font-size="3" fill="#888" text-anchor="middle">' + esc(y.year) + '</text>';
            });
            svg += '</svg>';
            return svg;
        }

        function drawRadar(raw) {
            var data = arr(raw).filter(isObj).map(function(d) {
                return { axis: txt(d.axis), value: num(d.value) };
            }).filter(function(d) { return d.value !== null; });
            if (data.length === 0) return '';
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
                svg += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) + '" font-size="9" fill="#b0b0c0" text-anchor="middle" dominant-baseline="middle">' + esc(d.axis) + '</text>';
            });
            svg += '</svg>';
            return svg;
        }

        function shareReport() {
            if (navigator.share) {
                navigator.share({ title: SHARE_TEXT.split(" #")[0], text: SHARE_TEXT, url: SHARE_URL }).catch(function(){});
            } else {
                var ta = document.createElement("textarea");
                ta.value = SHARE_TEXT + "\\n" + SHARE_URL;
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
        og_title=og_title_h,
        og_desc=og_desc_h,
        og_desc_short=og_desc_short_h,
        reading_id=reading_id,
        share_url=share_url_js,
        og_url=og_url_h,
    )

@router.post("/api/report/generate")
async def generate_report(req: GenerateReportRequest, uid: str = Depends(require_user)):
    """Generate a new fortune report from user profile.

    安全修复：必须登录（生辰信息为敏感数据）。
    """
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
            owner_uid=uid,     # k76：落归属密文，供读接口做归属校验
        )

        return report_data

    except Exception as e:
        logger.exception("生成报告失败")
        raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")
