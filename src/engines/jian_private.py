"""轻私语管线:分类模板化输出,红线=不点名事件/人物/原话。"""
import logging
logger = logging.getLogger(__name__)

_CATEGORY_TEMPLATES = {
    "事业": "近日心事多与事业有关,今日宜主动一步。",
    "感情": "心中若有放不下的人,今日宜先与自己和解。",
    "健康": "近来易倦,今日宜早歇,养足精神再出发。",
    "财运": "财宜细水长流,今日花销三思而后行。",
}
_GENERIC = "今日诸事,宜缓不宜急。"

def _recall(user_id: str) -> list:
    """L3 记忆召回,取最相关 1 条(按 topic/event 分类)。"""
    try:
        from src.memory.user_memory import UserMemory
        mem = UserMemory()
        entries = mem.list_entries(user_id)
        if not entries:
            return []
        for e in entries[:5]:
            if e.get("type") in ("topic", "event", "profile"):
                return [e]
    except Exception as e:
        logger.warning("轻私语召回失败: %s", e)
    return []

def _classify(entry: dict) -> str:
    subj = str(entry.get("subject") or "") + str(entry.get("content") or "")
    for cat in _CATEGORY_TEMPLATES:
        if cat in subj:
            return cat
    return ""

def generate_private_line(user_id: str, category: str = "") -> str:
    if category in _CATEGORY_TEMPLATES:
        return _CATEGORY_TEMPLATES[category]
    entries = _recall(user_id)
    if not entries:
        return _GENERIC
    cat = _classify(entries[0])
    return _CATEGORY_TEMPLATES.get(cat, _GENERIC)
