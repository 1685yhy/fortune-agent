"""深夜时段判定引擎(方案·灯下漫谈):默认 21:00-01:00,三档预设,跨日处理。

- 深夜窗 = [start, end);end > start 表示跨日(次日 end 点整熄灯,01:00 已属白天);
- 三档:早睡党 20:00-23:00 / 标准 21:00-01:00 / 夜猫子 22:00-02:00;
- 从晚安推送进入以入口为准,不校验时间(前端 options.entry='night' 短路本判定)。
"""
from datetime import datetime, timezone, timedelta

# preset -> (start_hour, end_hour);end > start 即跨日
NIGHT_PRESETS = {
    "early": (20, 23),
    "standard": (21, 1),
    "night": (22, 2),
}
DEFAULT_PRESET = "standard"
BJ_TZ = timezone(timedelta(hours=8))


def bj_now() -> datetime:
    """当前北京时间。"""
    return datetime.now(BJ_TZ)


def night_window(preset: str) -> tuple:
    """档位 -> (start_hour, end_hour);未知档位回退标准。"""
    return NIGHT_PRESETS.get(preset, NIGHT_PRESETS[DEFAULT_PRESET])


def is_night_mode(now: datetime = None, preset: str = DEFAULT_PRESET) -> bool:
    """是否深夜模式。now 无时区视为北京时间。

    窗语义:end > start 为同日窗 [start, end);end < start 为跨日窗
    [start, 24) ∪ [0, end)(次日 end 点整熄灯,01:00 已属白天)。
    """
    if now is None:
        now = bj_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=BJ_TZ)
    start, end = night_window(preset)
    h = now.hour
    if end > start:
        return start <= h < end          # 同日窗(早睡党 20:00-23:00)
    return start <= h or h < end         # 跨日窗(标准/夜猫子,含次日 0 点至 end 点前)
