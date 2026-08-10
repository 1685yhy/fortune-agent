"""前后端生辰字段契约适配 — 共享工具.

小程序各页面（love/hehun/qimen）发送的字段与后端八字引擎入参存在差异，
这里统一做规范化转换（A 类缺口：前端契约 birthYear/birthMonth/... 与
后端 year/month/... 对齐，且禁止把时辰序号当时钟小时传给引擎）：

- gender: 1/0、'male'/'female'、'男'/'女' → 引擎接受的 '男'/'女'
- birthHour: 0-11 视为时辰序号（子时..亥时，小程序 picker 的取值），
  映射为时钟小时（子时按 23 点晚子时，与 BaziEngine 的晚子时处理一致）；
  12-23 视为时钟小时原样返回（兼容后端原生契约）
- myBirth/taBirth: 'YYYY-MM-DD' / 'YYYY年M月D日' 等字符串 → (year, month, day)
"""
import re
from typing import Optional, Tuple, Union

# 时辰序号 → 时钟小时（取该时辰的起点：子=23 晚子时，丑=1, 寅=3, ... 亥=21）
SHICHEN_TO_HOUR = {0: 23, 1: 1, 2: 3, 3: 5, 4: 7, 5: 9,
                   6: 11, 7: 13, 8: 15, 9: 17, 10: 19, 11: 21}


def normalize_gender(gender: Union[int, str, None]) -> str:
    """性别归一化：1/0、male/female、男/女 → 男/女（八字引擎只认 男/女）。"""
    if gender is None:
        return "男"
    if isinstance(gender, bool):
        return "男" if gender else "女"
    if isinstance(gender, int):
        return "男" if gender == 1 else "女"
    g = str(gender).strip().lower()
    if g in ("1", "m", "male", "男", "man", "boy", "male1"):
        return "男"
    if g in ("0", "f", "female", "女", "woman", "girl"):
        return "女"
    return str(gender)


def normalize_hour(hour: Optional[int]) -> int:
    """时辰归一化：0-11 视为时辰序号（小程序 picker 的取值）映射为时钟小时；
    12-23 视为时钟小时原样返回；缺省取 12（午时）。"""
    if hour is None:
        return 12
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return 12
    if 0 <= h <= 11:
        return SHICHEN_TO_HOUR[h]
    return max(0, min(23, h))


def parse_birth_str(s: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """解析 '1992-08-15' / '1992.8.15' / '1992年8月15日' 为 (year, month, day)。

    解析失败或数值越界返回 None。
    """
    if not s:
        return None
    text = str(s).strip()
    m = re.match(r"^(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})日?$", text)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day
