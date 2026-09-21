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
# R1-3（T045 校准修复·午时时柱错）：午时序号 6 由 11 → 12（取时辰中点），
# 与 handler.CHINESE_HOUR_MAP 同步改（数据一致性铁律：同一口径一个事实源）。
# 根因：11 点经真太阳时修正（北京 ≈ -23 分）落回巳时块（10:37 → 癸巳时），
# 12 点修正后 ≈11:37 仍在午时块（甲午时）——校准锚点「2019-03-15 午时 →
# 甲午时」稳定命中。
SHICHEN_TO_HOUR = {0: 23, 1: 1, 2: 3, 3: 5, 4: 7, 5: 9,
                   6: 12, 7: 13, 8: 15, 9: 17, 10: 19, 11: 21}


#: 性别**白名单**（唯一事实源）—— 与 `storage/person_dao._normalize_gender`、
#: `engines/bazi.BaziEngine.calculate` 的 `_gender_out`、小程序
#: `miniprogram/tests/gender_contract.test.js` 声明的契约**同一个词表**：
#: `'男' | '女' | 'unknown'`（中文单一契约；不产出 male/female，也不透传任意串）。
GENDERS: Tuple[str, ...] = ("男", "女", "unknown")

#: 别名 → 白名单值（大小写/空白无关）。**白名单之外的输入不在这里兜底**，
#: 一律按 `unknown` 处理（见下）。
_GENDER_ALIASES = {
    "1": "男", "m": "男", "male": "男", "男": "男", "man": "男",
    "boy": "男", "male1": "男",
    "0": "女", "f": "女", "female": "女", "女": "女", "woman": "女",
    "girl": "女",
    # "unknown"/"未知"/"" 等"不知道"的写法（含历史脏值）统一进 unknown
    "unknown": "unknown", "未知": "unknown", "性别未知": "unknown",
    "": "unknown", "none": "unknown", "null": "unknown", "nan": "unknown",
}


def is_valid_gender(gender) -> bool:
    """该值是否**已经在**白名单内（`男`/`女`/`unknown`）。

    给 API 边界（pydantic 校验器）用：只用它判"要不要拒"，归一化一律走
    `normalize_gender`（两者同一个词表，不会再出现第二个白名单）。
    """
    return normalize_gender(gender) in GENDERS


def normalize_gender(gender: Union[int, str, None]) -> str:
    """性别归一化：1/0、male/female、男/女 → 男/女；**其余一律 → "unknown"**。

    改前（k80 必修1 的根因）：白名单之外**原样透传** `str(gender)` —— 于是
    `POST /api/report/generate` 的 `gender` 可以是任意字符串（如
    `</script><script>alert(document.cookie)</script>`），经 `profile.gender`
    落盘、再被报告页内嵌进 `<script>`，形成**存储型 XSS**（匿名分享页
    `/share/{id}` 上执行）。"未知"是**受控值**（白名单成员），不是"转发用户给的串"。

    None/bool/int 的既有口径不变：None → 男（历史默认）、True/1 → 男、False/0 → 女。
    """
    if gender is None:
        return "男"
    if isinstance(gender, bool):
        return "男" if gender else "女"
    if isinstance(gender, int):
        return "男" if gender == 1 else "女"
    g = str(gender).strip().lower()
    return _GENDER_ALIASES.get(g, "unknown")


def normalize_hour(hour: Optional[int], clock_signal: bool = False) -> int:
    """时辰归一化：0-11 视为时辰序号（小程序 picker 的取值）映射为时钟小时；
    12-23 视为时钟小时原样返回；缺省取 12（午时）。

    clock_signal（k19 分钟精度）：调用方显式声明该 hour 为**钟表时钟小时**
    （表单选了精确钟表时间档，如 10:55）时——0-23 全部原样直通引擎做真太
    阳时校准，不再把 0-11 当时辰序号。**只认显式声明**：旧 BaziInput 契约
    的 birthHour 0-11=时辰序号 + minute=时辰内偏置（如 6+25 = 午时 25 分，
    k19 review 回归：m>0 误判为时钟 6:25 → 时柱壬午错成己卯），minute 本
    身不携带语义、不参与判定；新前端钟表档必带 birthClock=True（k19 前端
    已实现），非钟表调用方不带 → 行为零变化。
    """
    if hour is None:
        return 12
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return 12
    if clock_signal:
        return max(0, min(23, h))  # 钟表时间 → 时钟小时直通
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
