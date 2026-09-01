"""出生档案统一读取（persons 默认档案 = 单一事实源）。

G3c：calendar 今日运势与 G1 对话路径共用同一读取函数，杜绝读取顺序漂移
（数据一致性铁律：同一数据一个事实源，消费点必须同源）。calendar 缓存键
指纹取自本函数返回值 → 建档用户指纹非 "none" → 命中个性化缓存键，
不再命中旧通用缓存（G3b H-7 指纹分键与 G3c 读取源天然同源）。
"""
import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def profile_fingerprint(profile: Optional[dict]) -> str:
    """档案指纹（R1-2 T089 同源）：对话/calendar 缓存键分键共用函数。

    单一事实源：指纹只从 _get_user_birth_profile 的产出计算（handler 与
    calendar 同源读取），保证改八字/改城市 → 指纹变化 → 旧缓存天然失效。

    - 无档案/缺出生 → "none"：通用版与个性化版天然分键；
    - 有档案 → 内容排序 JSON 的 md5（year/month/day/hour/minute/city/
      gender 全参与）：建档/改八字/改城市 → 指纹变化 → 当日立即重算。
    """
    if not profile:
        return "none"
    try:
        return hashlib.md5(
            json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    except Exception:
        return "none"


def get_user_birth_profile(dao, user_id: str, chart_dao=None) -> Optional[dict]:
    """获取用户出生信息档案（G1 P0-B 修复：persons 默认档案 = 单一事实源）。

    读取顺序（2026-08-29 G1 拍板，原 bazi_info 优先 → 编辑页改 persons 永不
    生效，P15 生产实测）：① persons 表主档案（默认优先，逻辑不变）→
    ② users.bazi_info → ③ chart_records 最近排盘（D8 保留）→ ④ None。

    自愈（G1）：① 命中且 users.bazi_info 缺失/与 persons 不一致时，
    用 persons 单向回写刷新 bazi_info（保留 bazi 四柱等既有键）——
    编辑页改动立即生效，消除两库永久分歧；bazi_info 不再当权威
    （gender 沿用档案中文契约 男/女，不产出 male/female）。

    不回写 persons（persons 是唯一权威，单向打通）。

    Args:
        dao: UserDAO（须有 db_path / get_user_bazi / save_user_bazi）。
        user_id: 用户 id（JWT sub）。
        chart_dao: ③ 级兜底用 ChartDAO；None 时按 dao.db_path 自建
            （生产 handler 与 calendar 共用同一 db_path，等价）。
    """
    if not dao:
        return None
    # ① persons 档案优先（单一事实源；db_path 访问已置于守卫内）
    try:
        from src.storage.person_dao import PersonDAO
        pdao = PersonDAO(dao.db_path)
        persons = pdao.list_persons(user_id)
        if persons:
            default = next((p for p in persons if p.get("is_default")), None)
            if default and default.get("birth_year"):
                pick = default
            else:
                candidates = [p for p in persons if p.get("birth_year")]
                if not candidates:
                    candidates = []
                    pick = None
                else:
                    # 选最近更新的有出生数据的档案（updated_at 降序取最大者）
                    pick = max(candidates, key=lambda p: p.get("updated_at") or "")
            if pick:
                out = {
                    "year": pick.get("birth_year"),
                    "month": pick.get("birth_month"),
                    "day": pick.get("birth_day"),
                    "hour": pick.get("birth_hour"),
                    "minute": pick.get("birth_minute"),
                    "city": pick.get("city") or "",
                    "gender": pick.get("gender") or "unknown",
                }
                # 自愈：bazi_info 缺失/与 persons 不一致 → persons 单向回写
                # （保留 bazi 四柱等既有键；写入失败仅告警，不阻塞读取）
                try:
                    bazi = dao.get_user_bazi(user_id)
                    _keys = ("year", "month", "day", "hour", "minute",
                             "city", "gender")
                    stale = (not bazi or not bazi.get("year")
                             or any(bazi.get(k) != out[k] for k in _keys))
                    if stale:
                        new_info = dict(bazi or {})
                        new_info.update(out)
                        dao.save_user_bazi(user_id, new_info)
                except Exception as e:
                    logger.warning("G1 档案自愈回写失败 user=%s: %s",
                                   user_id, str(e)[:160])
                return out
    except Exception:
        pass
    # ② users.bazi_info 兜底
    try:
        bazi = dao.get_user_bazi(user_id)
    except Exception:
        bazi = None
    if bazi and bazi.get("year"):
        return bazi
    # ③ chart_records 排盘结果兜底（D8 保留）：已排盘落库（重看 0 重跑
    # 数据）即视为有档案，问事直接走档案快路径，不再引导建档。
    try:
        if chart_dao is None:
            from src.storage.chart_dao import ChartDAO
            chart_dao = ChartDAO(dao.db_path)
        if chart_dao:
            chart = chart_dao.get_latest_chart(user_id)
            if chart and chart.get("birth") and chart["birth"].get("year"):
                b = chart["birth"]
                out = {k: b.get(k) for k in
                       ("year", "month", "day", "hour", "minute",
                        "city", "gender")}
                bazi = (chart.get("bazi_json") or {}).get("bazi") or []
                if bazi:
                    out["bazi"] = bazi
                return out
    except Exception:
        pass
    return None
