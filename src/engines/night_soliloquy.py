"""枕边灯语管线(方案·灯下漫谈):当天 L2 摘要 → 分类级温柔锚点 →
150-200 字深夜独白(LLM) → 红线校验 → 兜底模板 → TTS(8768)。

红线:
- LLM 提示词只给"分类级锚点"(事业/感情/健康/财运),绝不给原文 → 设计上杜绝引用原文;
- 输出校验:100-260 字、以"灯还亮着"开场、以"晚安。灯下的人"落款、无 URL;
- 当日无对话 / 私语开关关闭 / LLM 两次失败 → 纯"明日宜忌+古籍金句+通用晚安"兜底,
  宁缺毋滥,绝不编造记忆。
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "事业": ["事业", "工作", "面试", "加班", "升职", "跳槽", "老板", "同事", "辞职", "上班"],
    "感情": ["感情", "恋爱", "分手", "思念", "结婚", "对象", "喜欢", "想念", "心碎", "前任", "想起"],
    "健康": ["健康", "失眠", "睡不着", "累", "疲惫", "身体", "医院", "生病", "熬夜"],
    "财运": ["财运", "钱", "收入", "开销", "理财", "还债", "工资", "欠款"],
}

_NIGHT_PROMPT = """你是易理明灯,深夜陪伴人格(豆包式五步安慰法降速版)。
请为今日写一段 150-200 字的「枕边灯语」深夜独白。口吻:短句、无说教、不追问、不解决,只陪。

结构(严格按序):
1. 开场一句「灯还亮着」
2. 一句今天的事——只能引用下面给出的「分类级锚点」(如:事业/感情/健康),
   绝对不许提具体人名、具体事件、原文或细节
3. 一句抚慰
4. 一句明日宜忌(给出明日干支与宜忌)
5. 落款「晚安。灯下的人」

规则:
- 全文 150-200 字,短句为主,句子之间用换行
- 锚点只作为"记得你今天聊过这一类事"的依据,不引用用户任何原话
- 不提问、不建议、不说教
直接输出独白正文,不要任何额外说明。"""


def extract_anchors(summary_text: str) -> list:
    """从 L2 摘要提取 1-3 个分类级温柔锚点(绝不取原文)。"""
    if not summary_text:
        return []
    out = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in summary_text for k in kws):
            out.append(cat)
    return out[:3]


def has_today_chat(session_dao, user_id: str, date_str: str) -> bool:
    """北京时间当日是否有过对话(sessions.created_at 为 UTC,需 +8 换算)。"""
    try:
        history = session_dao.get_history(user_id, limit=50)
        from datetime import datetime, timedelta
        for h in history:
            ca = str(h.get("created_at", ""))[:19]  # YYYY-MM-DD HH:MM:SS(UTC)
            if not ca:
                continue
            try:
                bj = datetime.fromisoformat(ca.replace(" ", "T")) + timedelta(hours=8)
                if bj.strftime("%Y-%m-%d") == date_str:
                    return True
            except ValueError:
                if ca[:10] == date_str:
                    return True
        return False
    except Exception as e:
        logger.warning("灯语当日对话判定失败: %s", e)
        return False


def _tomorrow_content(date_str: str) -> dict:
    """明日干支/宜忌/古籍金句(复用晨笺干支+金句管线;generic 缓存兜底)。"""
    from datetime import date, timedelta
    from src.engines.calendar import LuckyCalendar
    from src.engines.jian_quote import generate_daily_quote
    t = (date.fromisoformat(date_str) + timedelta(days=1)).isoformat()
    cal = LuckyCalendar("")
    stem, branch = cal._day_stem_branch(t)
    quote = generate_daily_quote(t, f"{stem}{branch}") or {}
    generic = {}
    try:
        from src.utils.cache import get_cache
        generic = get_cache().get(f"date:generic_daily:{t}") or {}
    except Exception:
        generic = {}
    return {
        "date": t, "day_ganzhi": f"{stem}{branch}",
        "suitable": generic.get("suitable") or ["早睡", "静心", "安神"],
        "unsuitable": generic.get("unsuitable") or ["熬夜", "焦躁"],
        "quote": quote.get("quote", ""), "book": quote.get("book", ""),
    }


def _fallback_soliloquy(date_str: str, tc: dict) -> str:
    """纯模板兜底:明日宜忌 + 古籍金句 + 通用晚安(无记忆/私语关时)。"""
    quote = (tc.get("quote") or "").strip()
    q_line = f"明日{tc['day_ganzhi']}日,宜{'、'.join((tc.get('suitable') or [])[:3])}。"
    if quote:
        q_line += f"「{quote}」"
    return (f"灯还亮着。\n今夜没有太多话要说,也好。{q_line}\n"
            "你只管睡。天大的事,等太阳升起来再说。\n晚安。灯下的人")


def _validate_soliloquy(text: str) -> bool:
    """红线校验:长度 100-260 / 灯还亮着开场 / 灯下的人落款 / 无 URL。"""
    t = (text or "").strip()
    if not (100 <= len(t) <= 260):
        return False
    if "灯还亮着" not in t:
        return False
    if not t.rstrip().endswith("灯下的人"):
        return False
    if any(b in t for b in ("http", "www.", "{{")):
        return False
    return True


def _default_llm(api_key: str, messages: list, **kw) -> str:
    from src.llm.client import deepseek_anthropic_completion
    return deepseek_anthropic_completion(api_key, messages, **kw)


def build_soliloquy(user_id: str, date_str: str, session_dao, llm_fn=None,
                    whisper: bool = True) -> dict:
    """生成当日灯语。返回 {date, text, anchors, fallback}。"""
    tc = _tomorrow_content(date_str)
    anchors = []
    if whisper and has_today_chat(session_dao, user_id, date_str):
        try:
            summary = session_dao.get_summary(user_id) or {}
            anchors = extract_anchors(summary.get("summary", "") or "")
        except Exception as e:
            logger.warning("灯语摘要读取失败 user=%s: %s", user_id, e)
    if not anchors:
        return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
                "anchors": [], "fallback": True}
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
                "anchors": anchors, "fallback": True}
    fn = llm_fn or _default_llm
    prompt = (f"{_NIGHT_PROMPT}\n\n【明日】{tc['day_ganzhi']}日,"
              f"宜{'、'.join((tc.get('suitable') or [])[:3])},"
              f"忌{'、'.join((tc.get('unsuitable') or [])[:3])}。\n"
              f"【今日分类级锚点】{'/'.join(anchors)}")
    for attempt in (1, 2):
        try:
            text = (fn(api_key, [{"role": "user", "content": prompt}],
                       model="deepseek-v4-flash", max_tokens=600, temperature=0.8,
                       timeout=30.0) or "").strip()
        except Exception as e:
            logger.warning("灯语生成失败(尝试 %s) user=%s: %s", attempt, user_id, e)
            continue
        if _validate_soliloquy(text):
            return {"date": date_str, "text": text, "anchors": anchors, "fallback": False}
        logger.warning("灯语校验未通过(尝试 %s) user=%s", attempt, user_id)
    return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
            "anchors": anchors, "fallback": True}


def synth_lamp_audio(text: str) -> str:
    """TTS(8768):柔缓女声、语速 -10%。失败返回空串(文字版仍可用)。"""
    try:
        r = httpx.post("http://127.0.0.1:8768/tts",
                       json={"text": (text or "")[:450],
                             "voice": "zh-CN-XiaoyiNeural", "rate": "-10%"},
                       timeout=90)
        if r.status_code != 200:
            logger.warning("灯语 TTS 失败: %s", r.status_code)
            return ""
        url = r.json().get("audio_url", "")
        if url.startswith("/"):
            url = f"http://127.0.0.1:8768{url}"
        return url
    except Exception as e:
        logger.warning("灯语 TTS 异常: %s", e)
        return ""
