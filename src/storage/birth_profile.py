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
                    # R2-5：三源 out 统一透传 calendar（源值，缺省 solar）——
                    # 消费方据此决定是否 to_solar_date 转公历；year/month/day
                    # 保持原始值不改（保存回写路径 3243/3381/4679 消费原值安全）。
                    "calendar": pick.get("calendar") or "solar",
                }
                # 自愈：bazi_info 缺失/与 persons 不一致 → persons 单向回写
                # （保留 bazi 四柱等既有键；写入失败仅告警，不阻塞读取）
                # R1-3（T009 修复暴露的误触发）：persons 存储层把 hour/minute
                # 0 折叠为 None（_birth_dict），读回 None 与 bazi_info 的 0
                # 永不等 → 每次排盘后读档案都误触发回写。比较与回写统一按
                # 「0 与 None/空 等价」（时辰/分钟未知）归一，gender 等仍严格。
                try:
                    bazi = dao.get_user_bazi(user_id)
                    _keys = ("year", "month", "day", "hour", "minute",
                             "city", "gender", "calendar")

                    def _time_eq(k, a, b):
                        if k == "calendar":
                            # R2-5：旧 bazi_info 行无 calendar 键 → 缺省 solar
                            # 与 out 的显式 'solar' 等价，不误触发；persons 侧
                            # lunar 标记必须回写 bazi_info（两库单一事实源打通）
                            return (a or "solar") == (b or "solar")
                        if k in ("hour", "minute"):
                            return (a in (None, "", 0)) == (b in (None, "", 0))
                        return a == b

                    stale = (not bazi or not bazi.get("year")
                             or any(not _time_eq(k, bazi.get(k), out[k])
                                    for k in _keys))
                    if stale:
                        new_info = dict(bazi or {})
                        for k in _keys:
                            if not _time_eq(k, new_info.get(k), out.get(k)):
                                new_info[k] = out[k]
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
        out = dict(bazi)
        # R2-5：bazi_info 源 out 透传 calendar（源值，旧行缺省 solar）
        out.setdefault("calendar", "solar")
        return out
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
                # R2-5：chart 源 out 透传 calendar（行内值，缺省 solar）
                out["calendar"] = b.get("calendar") or "solar"
                bazi = (chart.get("bazi_json") or {}).get("bazi") or []
                if bazi:
                    out["bazi"] = bazi
                return out
    except Exception:
        pass
    return None


def to_solar_date(profile: Optional[dict]):
    """单点转换：农历出生档案 → 公历 (year, month, day)。

    R2-5（数据一致性铁律：存储=原始输入事实源（含 calendar 标记），消费前
    单点转公历——禁止各处自实现转换，禁止回写改写原始值）。引擎契约=
    公历输入，所有档案消费方（handler 排盘兜底 / _fmt_birth_text /
    calendar 今日运势）统一经此函数转换。

    - calendar != 'lunar'（含旧档案缺省 solar）→ None：调用方直接用原值；
    - lunar 但日期非法（不存在的农历日/月份越界）/ 年份超出前端 lunar.js
      契约 1900-2100 / lunar-python 异常 → None：调用方回落原值并
      logger.warning 一行，不抛异常不阻塞（绝不因转换问题打断用户主流程）。
    - lunar 且转换成功 → (y, m, d) 公历三元组。

    闰月语义：lunar-python fromYmd 的「序数月」口径与前端 lunar.js
    lunar2solar 一致（非闰月序号换算；真闰月出生需带 isLeap 标记，前端
    选择器与后端均未传 → 见 report concerns）。
    """
    if not profile or str(profile.get("calendar") or "solar") != "lunar":
        return None
    y = profile.get("year")
    m = profile.get("month")
    d = profile.get("day")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (y, m, d)):
        return None
    if not (1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 30):
        return None
    try:
        from lunar_python import Lunar
        solar = Lunar.fromYmd(y, m, d).getSolar()
        return (solar.getYear(), solar.getMonth(), solar.getDay())
    except Exception:
        # 非法农历日（如 1999-03-30 三月仅 29 天）→ None，调用方安全回落
        return None
