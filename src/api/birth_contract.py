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
#:
#: ── k81 必修1：别名表补全（原表缺**最常见的中文写法**）────────────────────
#: 终验实测（`POST /api/report/generate`）：`'男性'` / `'女性'` 改前改后都落到
#: `unknown` → 引擎按"未知默认男"排盘 ⇒ **女性用户送「女性」会被当成男性排**
#: （大运顺排而非逆排）。这是**既有的、一直没修的 bug**，不是 k80 引入的。
#: 本批按"系统地把常见写法一次收全"补齐，取值**以既有契约为准**（不自己发明）：
#:
#:   既有契约逐处核对（**结论：输出值集到处一致 = `男|女|unknown`；输入集不同**）
#:     ① `storage/person_dao._normalize_gender`：`男|male` → 男、`女|female` → 女，
#:        其余（含 None/空/历史髒值）→ unknown；
#:     ② `engines/bazi.BaziEngine.calculate`：同上两个词 + 其余 → unknown（计算按男）；
#:     ③ 小程序 `miniprogram/utils/persons.js`（`genderCode`/`normalizePerson`）：
#:        同上两个词 + 其余 → unknown；`genderCN` 兼容 `male|female` 显示；
#:     ④ 小程序 `miniprogram/tests/gender_contract.test.js`：钉死上一条；
#:     ⑤ `api/user.py::_person_birth`：同上两个词 → 男/女，其余 → None（=不覆盖）。
#:   ⇒ 全仓**没有**任何一处认过 `男性/女性/男士/女士/先生`；这份表就是唯一的收口点。
#:     本次新增的 5 个词全部是**唯一性别指向的中文写法**（互为对称的 男/女 对），
#:     不含任何"可男可女/语义含糊"的词（`男命/女命`、`好人` 这类**故意不收**：
#:     宁可 unknown，也不猜）。
#:
#: 值集一致性（终验点名的"16/40 处逐输入不同"已处置，见 `gender_of_alias`）：
#:   本表**声明为**全仓唯一的别名表；`person_dao` / `engines.bazi` / `api/user`
#:   三处改为查同一张表（k81），于是**字符串输入**逐输入一致。**仍允许不同**的
#:   只剩**非字符串边界**（None/bool/int），各处的策略是"刻意不同"且已各自
#:   写进文档：本函数是**入参边界**（缺省＝男，BaziEngine 的历史默认），
#:   `person_dao` 是**存储哨兵**（None ＝ 未提供 → unknown，绝不替用户认领性别）。
#:
#: ── 残留的"表"清单（k81 全仓盘点；**未改**，附理由，供下次收敛）──────────
#: 下面 4 处也各有一份性别映射，但都**不决定排盘/落盘**，且只吃上面 3 个规范值
#: （男/女/unknown）—— 对这三个值它们与本表**逐值等价**，故本批不改（红线：
#: `normalize_gender` 影响面广，只收"决定性别"的点）。**新增消费点时先知会本表**：
#:   ① `utils/fact_guard.py::gender_label`（sc. 男/male/m → 男、女/female/f → 女，
#:      其余 unknown）：吃的是引擎 `result.gender` / 档案 gender（已归一），
#:      只用于"称谓词该不该去"，对男/女/unknown 三者等价；
#:   ② `api/paipan.py::_history_item`（`"女" if gender in ("female","女") else "男"`）：
#:      把 unknown 显示成"男命"——**既有展示口径**，改动会动历史列表文案；
#:   ③ `main.py` 基础命书 / `engines/advisor_v2.py` 的展示映射
#:      （`{"male":"男","female":"女"}.get(...)`）：同样是展示层；
#:   ④ `engines/chenggu.py::chenggu_verse`（`"male" if gender in ("男","male") else
#:      "female"`）：**唯一一处"刻意不同"的语义**——它的口径来自**问真**（权威对比）：
#:      "sex=1 → 男命歌诀；其余 → 女命歌诀"，故 `unknown` 取**女命歌诀**，
#:      与 BaziEngine 的"unknown 按男排"**故意相反**。**此处不许按本表对齐**，
#:      否则与问真对不上（教研对比规则：对不上＝我们的 bug）。
GENDER_ALIASES = {
    "男": "男", "m": "男", "male": "男", "man": "男", "boy": "男",
    "1": "男", "male1": "男",
    # k81 新增：最常见的中文写法（终验实测 `女性` → 男 的那条）
    "男性": "男", "男士": "男", "先生": "男",
    "女": "女", "f": "女", "female": "女", "woman": "女", "girl": "女",
    "0": "女",
    # k81 新增：与 `男性` 对称
    "女性": "女", "女士": "女",
    # "unknown"/"未知"/"" 等"不知道"的写法（含历史脏值）统一进 unknown
    "unknown": "unknown", "未知": "unknown", "性别未知": "unknown",
    "": "unknown", "none": "unknown", "null": "unknown", "nan": "unknown",
}

#: 兼容旧名（k80 及之前的私有拼写；**同一份 dict**，不是第二张表）。
_GENDER_ALIASES = GENDER_ALIASES


def is_valid_gender(gender) -> bool:
    """该值是否**已经在**白名单内（`男`/`女`/`unknown`）。

    给 API 边界（pydantic 校验器）用：只用它判"要不要拒"，归一化一律走
    `normalize_gender`（两者同一个词表，不会再出现第二个白名单）。
    """
    return normalize_gender(gender) in GENDERS


def gender_of_alias(value) -> str:
    """**别名表的唯一查表入口**（单一事实源）—— 只查表，不带任何 None/int 策略。

    为什么单独存在（k81）：终验实测 `normalize_gender` 与
    `storage/person_dao._normalize_gender` "值集相同、逐输入 16/40 不同" —— 两张
    别名表各写各的。修法**不是**让存储层调用 `normalize_gender`（那会把它的
    "None → 男" 的**入参边界**口径带进存储层：一份 gender 缺失的老报告/老档案会
    被认领成"男"，正是本批要消灭的那类失败），而是把**表**与**查表规则**收敛到
    这里，各处只保留自己的 None/int 策略。于是字符串输入逐输入一致，非字符串
    边界的差异是**刻意保留**的（见 `GENDER_ALIASES` 注释）。
    """
    return GENDER_ALIASES.get(str(value or "").strip().lower(), "unknown")


def normalize_gender(gender: Union[int, str, None]) -> str:
    """性别归一化：1/0、male/female、男/女、男性/女性 → 男/女；**其余一律 "unknown"**。

    改前（k80 必修1 的根因）：白名单之外**原样透传** `str(gender)` —— 于是
    `POST /api/report/generate` 的 `gender` 可以是任意字符串（如
    `</script><script>alert(document.cookie)</script>`），经 `profile.gender`
    落盘、再被报告页内嵌进 `<script>`，形成**存储型 XSS**（匿名分享页
    `/share/{id}` 上执行）。"未知"是**受控值**（白名单成员），不是"转发用户给的串"。

    None/bool/int 的既有口径不变：None → 男（历史默认）、True/1 → 男、False/0 → 女。

    ── k81 必修1：**行为变更披露**（控制方定规"报的都要修"，本批补别名表）──────
    同一份输入在 k80 之前 / k80 起 / k81 起的归一结果不同，**且存量报告不回溯**
    （重算涉及数据迁移，控制方未拍板 ⇒ 按"不重算 + 显式披露"处置，见下）：

      · k80 起（**已生效**）：白名单外的串不再透传，一律 `unknown`。
        受影响输入：除 `1/0/m/male/female/男/女/man/woman/boy/girl/male1/unknown/
        未知/性别未知/空/none/null/nan` 之外的一切字符串（含攻击载荷、`男性`、
        `女性`）。此前这些值会**原样**落进 `profile.gender`。
      · k81 起（**本批**）：新增 `男性/男性→男`、`女性/女士→女`、`先生→男`。
        受影响输入 ⇔ **归一变且排盘变**，逐条：
          `男性` / `男士` / `先生`：k80- → 原样透传（引擎按非女 ⇒ **男**）
                                    k80  → `unknown` ⇒ 排盘**男**（未变）
                                    k81  → `男`     ⇒ 排盘**男**（未变，值变准）
          `女性` / `女士`：        k80- → 原样透传（引擎按非女 ⇒ **男**，**错**）
                                    k80  → `unknown` ⇒ 排盘**男**（**错，未修**）
                                    k81  → `女`     ⇒ 排盘**女**（★ 修正：大运改逆排）
        ⇒ **唯一排盘结果发生变化的是 `女性` / `女士`**（男 → 女，方向为修正）。

    ── 存量报告处置（结论：**不重算**，理由与受影响清单）────────────────────
      - 报告 JSON（`data/reports/*.json`）存的是**生成当时的排盘结果**（大运已算好
        落盘），改代码不会改写它们；要一致就得**重算全量存量报告** —— 那是一次
        数据迁移，控制方未拍板 ⇒ 本批**不做**，在此显式留痕：
        **"k80 起性别归一化生效、k81 起补全中文写法；此前生成的报告按旧口径
        （`女性` 等按男排），不回溯。"**
      - 受影响存量报告的识别（不需要扫库即可判定，供将来决定重算时用）：
        ① `profile.gender == "unknown"`（k80 前生成且送了白名单外写法）；
        ② `profile.gender` 是 `女性|女士`（k80 前生成且原样透传）；
        ③ k80 之后生成、`profile.gender == "unknown"` 且用户自称女性。
        重算命令与影响面评估**不做**（超出本批范围；若控制方要重算，另开批次）。
    """
    if gender is None:
        return "男"
    if isinstance(gender, bool):
        return "男" if gender else "女"
    if isinstance(gender, int):
        return "男" if gender == 1 else "女"
    return gender_of_alias(gender)


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
