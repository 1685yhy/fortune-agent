"""八字学堂 API — 教程展示与个性化示例.

提供：
- GET /api/xuetang/lesson?topic=xxx&user_id=xxx  获取教程（支持个性化）
- GET /api/xuetang/topics                          获取课程目录
"""

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..security.auth import require_user
from ..services.narrative import NarrativeService

router = APIRouter(tags=["xuetang"])

# 小程序 topics 列表用的简短介绍（与 src/engines/xuetang.py CURRICULUM 键对应）
_TOPIC_DESCRIPTIONS = {
    "什么是八字": "了解四柱八字的组成原理，学会排盘与解读基本框架",
    "天干地支": "十天干与十二地支的含义、属性与组合",
    "五行生克": "深入理解金木水火土的相生相克与在命理中的运用",
    "阴阳学说": "阴阳的哲学基础与在命局中的平衡之道",
    "十神分析": "十神关系详解：正官、七杀、正印、偏印等",
    "格局判断": "八字格局分类：正格、变格与特殊格局",
    "用神取法": "用神忌神的取法：扶抑、调候、通关",
    "大运流年": "大运排法与流年运势的解读方法",
    "财运分析": "从八字看财星、财库与财富格局",
    "感情婚姻": "配偶星与夫妻宫看感情婚姻走向",
    "事业官运": "官星印星与事业运势分析",
    "健康养生": "从五行强弱看健康与养生方向",
}

# ── 全局依赖注入 ──────────────────────────────────────────────────

_dao = None
_retriever = None
_narrative: Optional[NarrativeService] = None


def setup(dao, retriever, narrative: NarrativeService = None):
    """在主应用生命周期中注入 DAO 和 Retriever 实例。"""
    global _dao, _retriever, _narrative
    _dao = dao
    _retriever = retriever
    _narrative = narrative


# ── API 端点 ─────────────────────────────────────────────────────


@router.get("/api/xuetang/topics")
async def get_topics():
    """获取八字学堂全部课程目录。

    返回两种结构：
    - curriculum: 原有分级目录（向后兼容）
    - topics:     小程序契约（xuetang.js）res.topics: [{id, name, description, lessonCount}]
    """
    from src.engines.xuetang import CURRICULUM

    # Build a structured curriculum listing
    levels = []
    topics_out = []
    for level_name, topics in CURRICULUM.items():
        topic_list = [
            {"name": topic, "level": level_name}
            for topic in topics
        ]
        levels.append({
            "level": level_name,
            "topics": topic_list,
        })
        for topic in topics:
            topics_out.append({
                "id": topic,  # 与 lesson 的 topic 参数一致，可直接用于获取课程
                "name": topic,
                "level": level_name,
                "description": _TOPIC_DESCRIPTIONS.get(topic, f"{level_name}阶段课程：{topic}"),
                "lessonCount": 1,
            })

    return {
        "status": "ok",
        "curriculum": levels,
        "topics": topics_out,
        "narrative": "",
    }


@router.get("/api/xuetang/lesson")
async def get_lesson(
    topic: str = Query(..., description="教程话题名称，如「十神分析」「五行生克」"),
    uid: str = Depends(require_user),
):
    """获取八字学堂教程内容。

    必须登录；个性化示例使用的八字一律取 JWT sub 对应数据
    （query 的 user_id 被忽略，防 IDOR 读取他人八字）。
    """
    from src.engines.xuetang import get_lesson as _get_lesson
    from src.engines.xuetang import personalized_lesson as _personalized_lesson
    from src.engines.xuetang import CURRICULUM, _get_topic_section

    user_id = uid

    # Validate topic
    section, _ = _get_topic_section(topic)
    if section is None:
        all_topics = []
        for sec, topics in CURRICULUM.items():
            all_topics.extend(topics.keys())
        raise HTTPException(
            status_code=404,
            detail=f"未找到话题「{topic}」。可用话题：{'、'.join(all_topics)}",
        )

    # Try personalization with the authenticated user's bazi
    # k8：四柱只取「出生档案匹配的 chart_records 盘」（get_user_birth_profile_full）
    # ——不再读 users.bazi_info.bazi（旧行 bazi 键可能为历史他人盘污染，21:44
    # 事故源；无匹配盘 → 通用课程，不把他人盘四柱用于个性化）
    bazi_data = None
    if user_id and _dao:
        try:
            from src.storage.birth_profile import get_user_birth_profile_full
            saved = get_user_birth_profile_full(_dao, user_id)
            if saved and saved.get("bazi"):
                bazi_data = {
                    "day_master": saved.get("day_master", ""),
                    "bazi": saved.get("bazi", []),
                    "geju": saved.get("geju", ""),
                    "yongshen": saved.get("yongshen", ""),
                }
        except Exception:
            pass

    personalized_example = ""
    try:
        if bazi_data:
            content = _personalized_lesson(topic, bazi_data=bazi_data, retriever=_retriever)
            personalized_example = f"以用户八字（日主{bazi_data.get('day_master','?')}）个性化"
        else:
            content = _get_lesson(topic, retriever=_retriever)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成教程失败：{str(e)[:200]}")

    # Build related topics list — only from the section that matches
    related_topics = []
    for sec, topics in CURRICULUM.items():
        if sec == section:
            for t in topics:
                if t != topic:
                    related_topics.append(t)
            break

    return {
        "topic": topic,
        "level": section,
        "content": content,
        "personalized": bool(bazi_data),
        "personalized_example": personalized_example,
        "related_topics": related_topics,
        "narrative": "",
        # 小程序契约（xuetang.js）res.lesson: {title, content, ...}
        "lesson": {
            "title": topic,
            "topicLabel": f"{section} · {topic}",
            "content": content,
            "icon": "",
        },
    }
