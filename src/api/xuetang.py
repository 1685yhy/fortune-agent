"""八字学堂 API — 教程展示与个性化示例.

提供：
- GET /api/xuetang/lesson?topic=xxx&user_id=xxx  获取教程（支持个性化）
- GET /api/xuetang/topics                          获取课程目录
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query

from ..services.narrative import NarrativeService

router = APIRouter(tags=["xuetang"])

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
    """获取八字学堂全部课程目录。"""
    from src.engines.xuetang import CURRICULUM

    # Build a structured curriculum listing
    levels = []
    for level_name, topics in CURRICULUM.items():
        topic_list = [
            {"name": topic, "level": level_name}
            for topic in topics
        ]
        levels.append({
            "level": level_name,
            "topics": topic_list,
        })

    return {
        "status": "ok",
        "curriculum": levels,
        "narrative": "",
    }


@router.get("/api/xuetang/lesson")
async def get_lesson(
    topic: str = Query(..., description="教程话题名称，如「十神分析」「五行生克」"),
    user_id: str = Query("", description="用户ID（可选，提供后将生成个性化示例）"),
):
    """获取八字学堂教程内容。

    如果不提供 user_id 或用户没有保存的八字信息，返回标准教程。
    如果提供了 user_id 且有八字数据，教程末尾会附加个性化示例。
    """
    from src.engines.xuetang import get_lesson as _get_lesson
    from src.engines.xuetang import personalized_lesson as _personalized_lesson
    from src.engines.xuetang import CURRICULUM, _get_topic_section

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

    # Try personalization if user_id is provided
    bazi_data = None
    if user_id and _dao:
        try:
            saved = _dao.get_user_bazi(user_id)
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
    }
