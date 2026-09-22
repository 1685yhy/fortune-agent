"""k11 事实纪律 · 输出后纯规则校验器（B 性别称谓 / C 神煞白名单 / D schema 回显）——零 LLM。

事故背景（2026-09-06 行动建议卡）：男命收到「醒醒吧姐妹」式女性口吻；正文/卡引用
排盘卡上看不到的神煞（显示截断 [:6] 而喂全集 → 观感编造；主链另有真幻觉「文昌贵人」
不在引擎全集）。G1 性别链只覆盖主链/运势卡，未到达 advisor 三消费点；prompt 修复之外
需要"输出后校验器"兜底（任何 LLM 环节重写后仍可能违规）。

职责（纯规则，无 LLM、无网络）：
- guard_gender_terms：男/未知命文本中的女性称谓词 → 去词 + 返回命中（女命不处理）
- guard_shensha_refs：文本中出现的引擎神煞词典词（shensha.SHENSHA_LUCK 键集，59 型
  全集单一事实源）不在本盘 allow（引擎算出全集）→ 去词 + 返回命中
- guard_schema_echo（k63-r2 / D）：文本形态是"给模型看的 schema/规格说明" → **整段置空**
  （降级档 GLM 会把 prompt 里的 JSON schema 示例原样回显进字段，用户可见即露馅）
- scrub_turn：组合入口（性别 + 神煞 allow），供 handler/advisor/chat_stream 接线
  —— **语义与调用方保持不变**；D 是**独立函数**，不并入 scrub_turn（见下）

去词语义：命中词整体删除（规范允许的"去词/告警"档；不做整句改写）。防误伤：
allow 为空（无上下文/非命理轮）→ 神煞段跳过不 scrub。

为什么 D 与 scrub_turn **分开**（不扩 scrub_turn 的判据）：
1. scrub_turn 是 k11-B **称谓/神煞**防线，被 handler/advisor/chat_stream **多处**消费；
   在其中加"schema 判据"会让所有消费方**隐式**获得一个与它们无关的删段行为
   （主链正文若出现合法 `[{` 片段也会被连带整段置空），风险面与收益面不对等；
2. D 的判据是**形态学**的（JSON 键/占位符/元指令），与 B/C 的**词表**判据正交，
   混在一个返回值里无法区分"命中了什么"，排障与告警语义会糊掉；
3. 消费面不同：D 目前只接 **advisor 建议卡**（唯一挂了 GLM 降级链且字段来自 schema
   驱动的 prompt 的链）。分开后主链/流式链一点不受影响。
"""
import logging
import re
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# B：女性向称谓/闺蜜口吻词（男命/未知性别文本命中即去词+告警；仅女命放行）。
# 词表刻意保守（显式女性指向），避免误伤男性向文本（如"兄弟"不在此列）。
FEMALE_ADDRESS_TERMS: Tuple[str, ...] = (
    "姐妹", "闺蜜", "亲爱的", "姑娘", "小仙女", "小姐姐", "集美", "姐们儿",
)

# C：引擎神煞词典缓存（SHENSHA_LUCK 键集 = 59 型全集单一事实源；惰性加载防环）
_SHENSHA_LEXICON: Optional[Tuple[str, ...]] = None


def gender_label(gender) -> str:
    """性别归一：男/male → 男；女/female → 女；其余（unknown/空/None）→ unknown。"""
    _g = str(gender or "").strip().lower()
    if _g in ("男", "male", "m"):
        return "男"
    if _g in ("女", "female", "f"):
        return "女"
    return "unknown"


def shensha_lexicon() -> Tuple[str, ...]:
    """引擎神煞全集词典（词长降序，先长后短匹配防子串误拆）。"""
    global _SHENSHA_LEXICON
    if _SHENSHA_LEXICON is None:
        try:
            from src.engines.shensha import SHENSHA_LUCK
            _SHENSHA_LEXICON = tuple(
                sorted({str(k) for k in (SHENSHA_LUCK or {})},
                       key=len, reverse=True))
        except Exception:
            logger.warning("fact_guard: 神煞词典加载失败（降级为空词典）", exc_info=True)
            _SHENSHA_LEXICON = ()
    return _SHENSHA_LEXICON


def guard_gender_terms(text: str, gender) -> Tuple[str, List[str]]:
    """B：称谓词校验——男/未知命出现女性称谓词 → 去词并返回命中列表；女命原样。

    :return: (cleaned_text, hits)
    """
    if not text:
        return text, []
    if gender_label(gender) == "女":
        return text, []
    hits = [t for t in FEMALE_ADDRESS_TERMS if t in text]
    if not hits:
        return text, []
    cleaned = text
    for t in hits:
        cleaned = cleaned.replace(t, "")
    logger.warning("fact_guard: 性别称谓过滤（去词 %s）", "、".join(hits))
    return cleaned, hits


def guard_shensha_refs(text: str, allow_names: Iterable[str],
                       lexicon: Optional[Iterable[str]] = None) -> Tuple[str, List[str]]:
    """C：神煞引用校验——文本中出现的引擎词典神煞词必须在 allow（本盘引擎全集）内。

    allow 为空 → 视为"无本盘白名单上下文"，跳过（防误伤自由对话/非命理轮）。
    命中白名单外神煞词 → 去词 + 返回命中（纯规则，去词/告警档）。
    """
    if not text:
        return text, []
    allow = {str(a) for a in (allow_names or ())}
    if not allow:
        return text, []
    lex = tuple(lexicon) if lexicon is not None else shensha_lexicon()
    if not lex:
        return text, []
    hits: List[str] = []
    cleaned = text
    for w in lex:
        if w in allow or w not in cleaned:
            continue
        cnt = cleaned.count(w)
        cleaned = cleaned.replace(w, "")
        hits.append(w)
        logger.warning("fact_guard: 神煞白名单外引用去词 %s×%d（本盘全集=%d 个）",
                       w, cnt, len(allow))
    return cleaned, hits


def scrub_turn(text: str, gender, shensha_allow: Optional[Iterable[str]] = None
               ) -> str:
    """组合出口：称谓 + 神煞去词（幂等，可对同文本多次调用）。"""
    if not text:
        return text
    cleaned, _h1 = guard_gender_terms(text, gender)
    cleaned, _h2 = guard_shensha_refs(cleaned, shensha_allow)
    return cleaned


# ============================================================
# D：schema/结构文本泄漏（k63-r2）——呈现层兜底，零 LLM，纯规则
# ============================================================
# 事故形态（2026-09-20 实测，非推测）：advisor_v2._call_llm 挂 **GLM-4-Flash 降级链**
# （src/llm/client.py glm_openai_completion，ZHIPU_API_KEY 存在时优先），小模型会把
# **prompt 里给模型看的 JSON schema 示例**原样当字段值回显 → 用户直接看到
# `你问的是[领域]，…格式：…如果实在没有特别信息，输出空字符串。` 这类**对模型说的话**。
# 生产档（deepseek）未观察到该形态。
#
# 判据按**形态**（不是匹配某段 schema 原文 —— 那种补丁换个 prompt 就失效）：
#   强信号：命中 1 条即判泄漏（自然中文用户文案几乎不可能出现的结构/元语言形态）
#   弱信号：命中 ≥2 条才判泄漏（"格式：/示例："等元语言 + 英文字段名裸词，单条易误伤）
# 正反例清单见 tests/test_k63_schema_echo_guard.py（**中文引号/全角括号/`{}` 合法表达/
# `[1]` 引用编号/“30字以内”日常建议**均不得命中）。
#
# ⚠️ 精度优先于召回（控制方裁决 · k62k63 集成修复）：D 只被 `advisor_v2` 两处消费
# （`:146` / `:150`），且 `advice` 命中会**整条建议摘除** ⇒ **误杀代价 = 用户少一条
# 建议**（5 条全摘 → 退回通用兜底）。因此宁可漏判（用户看到一段模板说明）也不许
# 误杀（用户丢掉真建议）：**加信号必须同时给出"误杀未上升"的实测**，否则不加。
# 已知代价（阈值设计的固有代价，已用用例钉住，不隐藏）：`时间窗口` + 任一"必须X"
# 两条弱信号同时出现在**正常建议**里 → 会被整段置空
# （examples: "2026年的关键时间窗口在立秋之后，签合同必须包含违约条款。"）。
# 召回面（k62k63 集成修复补齐，此前 8/18 漏判 → 现 18/18）：prompt schema 里 4 个
# 领域用的是短规格（`最佳时间窗口，包含具体日期范围` 出现 4 次 /
# `具体的行动建议，结合…的个性化分析` 等），每条只带 1 条弱信号时会被放行 →
# 补入下面三条"规格从句"弱信号（带逐条形态论证与实测数字）。
# 残留缺口（如实登记，已用测试钉住）：**非逐字**的规格回显仍不命中 ——
#   ① 只回显半句（`包含具体日期范围`，丢了前面的"最佳时间窗口，"）；
#   ② 同义改写（"包含"→"含"：`最佳时间窗口，含具体日期范围`）；
#   ③ 只回显 advice 规格的后半句（`结合八字五行的个性化分析`，丢了 spec 头）——
#      它只有 1 条弱信号，被阈值（≥2）**故意**放行。
# 刻意不追：这三条都是"像规格的自造文案"，追下去要把 `时间窗口`/`具体的行动建议`
# 升成强信号或堆词表，而 D 的误杀代价是**用户少一条真建议** ⇒ 精度优先于召回。
# 复现口径与数字见 tests/test_k63_schema_echo_guard.py::RESIDUAL_GAPS 与集成修复报告 §③。
SCHEMA_ECHO_STRONG: Tuple[str, ...] = (
    r'"[A-Za-z_][A-Za-z0-9_]{2,}"\s*:',          # "advice": / "serendipity":（JSON 键）
    r'\[\s*\{',                                    # [{（JSON 数组开头）
    r'\}\s*\]',                                    # }]（JSON 数组结尾）
    r'\[[一-龥]{1,6}\]',                   # [领域]（中文占位符；[1]/[n] 不算）
    r'(?i)(?:high|medium|low)\s*/\s*(?:high|medium|low)',  # 取值域枚举
    r'(?:只|仅)?输出\s*JSON',                        # 元输出指令
    r'不要\s*markdown', r'输出空字符串', r'不输出(?:其他|其它|任何)文字',
    # 规格形态：值**以**「（N字以内）」结尾 —— 用户向文案不会用括号约束作者长度
    r'[（(]\s*\d+\s*字以内\s*[)）]\s*$',
)
SCHEMA_ECHO_WEAK: Tuple[str, ...] = (
    r'格式[：:]', r'示例[：:]', r'取值[：:]', r'请按以下(?:格式|JSON|要求)',
    r'\d+\s*(?:[-~—至]\s*\d+\s*)?字以内',
    r'JSON\s*(?:对象|格式)',
    # 元指令形态（对模型下的要求，不是对用户说的话）
    r'\d+\s*[-~—]\s*\d+\s*句话', r'不超过\s*\d+\s*字',
    r'必须(?:包含|结合|基于|覆盖|符合|给出|使用|遵守)',
    r'(?<!例)如[’‘\'"]', r'时间窗口',
    # 英文字段名裸词（schema 的 key；中文正文里出现即是回显特征）
    r'(?i)\b(?:serendipity|daily_tip|style_notes|actions|confidence|category|'
    r'timing|advice|concrete_steps|success_metric|celebrity_match|insight)\b',
    # ── 规格从句（k62k63 集成修复 · 召回补齐；每条都是**形态**，非抄某段原文）──
    # 事故面（审查 I-1 实测）：prompt schema 块里 4 个领域用的是**短规格** ——
    # `"timing": "最佳时间窗口，包含具体日期范围"`（在 prompt 里出现 **4 次**）与
    # `"advice": "具体的行动建议，结合八字五行的个性化分析"` 等 4 条；它们此前
    # 各只带 1 条弱信号 → 被阈值放行 ⇒ **真·原文回显漏判**。下面三条补上第二条
    # 弱信号。为什么不是"给某个例子打的补丁"：三条各自对应一类**写法差异**
    # （规格在描述"值该长什么样"，用户向文案在给出内容），且都已被 55 条自造正常
    # 文案（含 12 条对抗性"最像规格"写法）+ 229 条真实 LLM 字段（归档 137 + 本次
    # 自造 92）实测**零新增误杀**（改前/改后对照见
    # tests/test_k63_schema_echo_guard.py 的正/反例与集成修复报告 §③）：
    # ① 规格尾：`，包含具体<X>`（**不带「的/了」**）且**收在串尾** —— 无主语的
    #    祈使式名词短语收尾是规格写法；用户向文案不会这么写（写了"的"就不算，
    #    句中还有下文（逗号/句号）也不算 ⇒ 不误伤"…，包含具体的责任人"这类正常句）。
    r'[，,]\s*(?:必须|需要|需|应)?(?:包含|涵盖)具体(?![的了的这那哪])[^。！？；，,\n]{0,8}\s*[。！？]?\s*$',
    # ② schema 里 5 条 advice spec 全部是「具体的行动建议，结合…」这一搭配；
    #    用户向文案即使出现"具体的行动建议"，后面接的是冒号/具体事由，不会紧跟"结合"。
    r'具体的行动建议[，,]\s*结合',
    # ③ `结合…的<个性化|长远>(分析|建议…)` 是**名词化的"分析物描述"**（描述分析该
    #    长什么样，而不是给出分析）；"结合…的"偏正结构在用户向文案里极少见
    #    （那里写"结合你的情况…"，不带"的个性化/长远"）。
    r'结合[^。；！？，,\n]{0,12}的(?:个性化|长远)(?:发展)?(?:健康)?(?:分析|建议|方案)',
)
_SCHEMA_ECHO_STRONG_RE = tuple(re.compile(p) for p in SCHEMA_ECHO_STRONG)
_SCHEMA_ECHO_WEAK_RE = tuple(re.compile(p) for p in SCHEMA_ECHO_WEAK)


def schema_echo_hits(text: str) -> List[str]:
    """命中明细（强/弱分开，供告警与测试取证）。"""
    if not text:
        return []
    hits = [f"strong:{rx.pattern}" for rx in _SCHEMA_ECHO_STRONG_RE if rx.search(text)]
    hits += [f"weak:{rx.pattern}" for rx in _SCHEMA_ECHO_WEAK_RE if rx.search(text)]
    return hits


def is_schema_echo(text: str) -> bool:
    """形态判定：强信号 ≥1 或 弱信号 ≥2（阈值化，防单条弱信号误杀）。"""
    hits = schema_echo_hits(text)
    n_strong = sum(1 for h in hits if h.startswith("strong:"))
    return n_strong >= 1 or (len(hits) - n_strong) >= 2


def guard_schema_echo(text: str) -> Tuple[str, List[str]]:
    """D：schema/规格说明回显 → **整段置空** + 返回命中。

    为什么是整段置空而不是"删掉命中片段"：回显物是**给模型看的模板说明**，
    片段删除只会留下语义残缺的半句（更像 bug）；字段本身可空
    （schema 明说 serendipity 可给空串），置空后由调用方走各自兜底。
    """
    if not text:
        return text, []
    if not is_schema_echo(text):
        return text, []
    hits = schema_echo_hits(text)
    logger.warning("fact_guard: schema 回显整段置空（%d 条信号：%s）",
                   len(hits), "、".join(hits[:4]))
    return "", hits


def scrub_schema_echo(text: str) -> str:
    """便捷出口（与 scrub_turn 同风格；**独立于** scrub_turn，不改其语义）。"""
    return guard_schema_echo(text)[0]


# ============================================================
# E：无依据的具体结论（LEGACY-FABRICATION-01）——降级档编盘/编日期输出侧拦截
# ============================================================
# 事故形态（k65 r3 实跑实测，glm-4-flash、N=12、temperature=0.7）：
#     「精简模式暂不提供排盘服务，不过根据你的出生日期和时间，
#        你应该是庚午年、己巳月、乙巳日、丙申时。」
# —— 模型先说了**对的出口句**，随后仍补出一整张盘；同类还有把用户生日换算成
# 农历给出具体日期（「生于农历四月廿五」，同批 A/after/rep1）。另见择日档
# 「这个月15号或16号比较适合搬家」。
#
# 为什么必须落在输出侧：k65 r3 加「能力边界」后编盘 4/12→1/12、择日 3/12→0/12，
# **压不到 0**（提示词只能收敛概率，不能保证为 0）——这是生成侧概率行为，
# 需要结构层兜底。
#
# ⚠️ **主判据不是文本形态，而是「本轮到底有没有真实工具结果」**
# （形参 `has_real_tool_result`）。它与 k65 r2-1 那条 gate 是**同一个事实源**：
# `chat_quota.downgraded` / `lite` —— 降级档没有工具能力 ⇒ 任何四柱/日期都
# 没有依据。判据**不按消息内容猜**（同一句话在有/无工具的轮次里结论相反）。
#   · has_real_tool_result=True → **一字不改**（边界 5：主链有真实工具结果时
#     给出四柱/日期完全合法，不得一刀切）；
#   · has_real_tool_result=False → 才进入「具体值有没有依据」的检查。
#
# 依据（`allowed_refs`）= 已在本轮上下文里出现过的字面值：用户自报的生日、
# 真实工具结果、检索到的古籍原文。调用方用 `grounded_refs_from_messages`
# 从本轮 messages 取。**出现在依据里的值 ⇒ 属回显/引用，放行**（边界 1/2）：
# 本批宽口径 5 例里 4 例是「用户自报生日的回显」，误杀它就是最大的回归。
# 无依据的具体值 ⇒ 删除其所在**分句**（不是删词：删词会留下「你应该是。」
# 这类残句，更像 bug）。整条都是编造 → 清空（由调用方决定兜底）。
#
# 与 D 段的分工（同层、单一事实源，不各链各修）：
#   D = 形态学（"给模型看的 schema 被当成回复"），接 advisor_v2 呈现字段；
#   E = 事实源（"没有真实结果却给出具体结论"），接降级档对话出口
#       （src/llm/client.py::_chat_lite —— 该路径结构上无工具，
#       has_real_tool_result 恒为 False，与 r2 的 gate 同源）。
#
# 刻意不追（精度优先，已用测试钉住，勿当缺陷）：
#   ① 只提术语不给值（「建议从八字中找出喜用神」）= 合法（边界 3，k65 已判假阳性）；
#   ② 黄历泛述（「参考黄历，选个吉日」/「黄道吉日」/「双日子」）无具体日期 = 合法
#      （边界 4）；但「避开初八、十八、二十八」是具体日期 ⇒ 拦；
#   ③ 生肖复述（「属马」）不算日期、不算命盘结论（跟随 k65 审计口径）；
#   ④ 五行旺衰的**形容词**断言（「可能火土较旺」）不在本段口径内（见 tests 钉住）；
#   ⑤ 神煞名裸词（「命带桃花」）不拦（k65 已判假阳性；神煞白名单是 C 段职责，
#      且 C 段要求本盘全集，降级档没有）；
#   ⑥ X点/X时（钟点）不作为日期形态（多数是用户自报时间的回显）；
#   ⑦ 依据取"本轮 messages 全文"⇒ 历史里已有的值算已上桌（不再二次审计）；
#      代价是**未修版本落库的旧编造**会自我洗白（登记项，见报告）。

# ① 干支对（六十甲子：天干+地支相邻）——无真实结果时出现即"具体命盘结论"
_UNG_GANZHI_RE = re.compile(r'[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]')
# ② 术语 + 具体值（「喜用神为木火」「用神取水」「日主是庚金」）。
#    只提术语不给值**不匹配**（边界 3）：值必须紧跟 为/是/取/用/：/:
_UNG_TERM_VALUE_RE = re.compile(
    r'(?:喜用神|用神|忌神|调候)[^。！？；，,、\n]{0,4}(?:为|是|取|用|：|:)\s*[金木水火土]')
_UNG_DAYMASTER_RE = re.compile(
    r'(?:日主|日干)[^。！？；，,、\n]{0,3}(?:为|是|：|:)\s*[甲乙丙丁戊己庚辛壬癸]')
# ③ 具体日期：公历（含 15号/20日）、农历（含 初八/十八/廿八）
#    刻意**不含**「吉日/黄道吉日/双日子」等泛述（边界 4）
_UNG_DATE_RES = (
    re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]'),
    re.compile(r'\d{1,2}\s*月\s*\d{1,2}\s*[日号]'),
    # 「10号楼/3号线/2号院」这类门牌标签不是日期（误杀防线）
    re.compile(r'\d{1,2}\s*[日号](?![楼房线院车馆区机栋座台铺仓库单])'),
    re.compile(r'农历\s*(?:[一二三四五六七八九十]{1,3}\s*月)?\s*'
               r'[初廿十]?[一二三四五六七八九十]+'),
    re.compile(r'[初廿][一二三四五六七八九十]'),
    # 裸「十八/二十八」（k65 口径：避开初八、十八、二十八 ⇒ 拦）；
    # 「十二生肖/三十六计/十分」这类非日期词用否定前瞻挡掉（误杀防线）
    re.compile(r'十[一二三四五六七八九]'
               r'(?![生肖计般分年月份足全方万字答进制])'),
)
# 分句切分：**不在「、」上切** ——「庚午年、己巳月、乙巳日、丙申时」要整句删，
# 切开就会留下「己巳月、乙巳日」这类残片。
_UNG_CLAUSE_RE = re.compile(r'[^，,。！？；;\n]+')
_UNG_SENT_END = '。！？'


def _ung_norm(s) -> str:
    """归一：去所有空白（中文里「1990 年 5 月 20 日」与「1990年5月20日」同值）。"""
    return re.sub(r'\s+', '', str(s or ''))


def _ung_digits(s) -> Tuple[int, ...]:
    """数字序列（用于跨书写格式比对：「1990-05-20」=「1990年5月20日」）。"""
    return tuple(int(x) for x in re.findall(r'\d+', str(s or '')))


def _ung_grounded(value: str, refs_norm, refs_digits) -> bool:
    """具体值是否有依据：字面包含，或其数字序列是某依据的**连续子序列**。

    例：用户说「1990年5月20日 下午3点」→ 回复「5月20日」「20号」都算回显
    （数字序列 (5,20)/(20) 是 (1990,5,20,3) 的连续子序列）。
    """
    v = _ung_norm(value)
    if not v:
        return True
    for r in refs_norm:
        if v in r:
            return True
    vd = _ung_digits(value)
    if vd:
        n = len(vd)
        for rd in refs_digits:
            if n > len(rd):
                continue
            for i in range(len(rd) - n + 1):
                if rd[i:i + n] == vd:
                    return True
    return False


def _ung_values_in(clause: str, refs_norm, refs_digits) -> List[str]:
    """本分句里**无依据的具体值**清单（空 = 放行）。"""
    out: List[str] = []
    for m in _UNG_GANZHI_RE.finditer(clause):
        if not _ung_grounded(m.group(0), refs_norm, refs_digits):
            out.append("chart:" + m.group(0))
    for rx, kind in ((_UNG_TERM_VALUE_RE, "value"),
                     (_UNG_DAYMASTER_RE, "daymaster")):
        for m in rx.finditer(clause):
            if not _ung_grounded(m.group(0), refs_norm, refs_digits):
                out.append(kind + ":" + m.group(0))
    for rx in _UNG_DATE_RES:
        for m in rx.finditer(clause):
            if not _ung_grounded(m.group(0), refs_norm, refs_digits):
                out.append("date:" + m.group(0))
    return out


def ungrounded_claim_hits(text: str, has_real_tool_result: bool,
                          allowed_refs: Iterable[str] = ()) -> List[str]:
    """命中明细（供告警与测试取证）。主判据为 `has_real_tool_result` + 依据表。"""
    if not text or has_real_tool_result:
        return []
    refs = tuple(allowed_refs or ())
    refs_norm = tuple(x for x in (_ung_norm(r) for r in refs) if x)
    refs_digits = tuple(x for x in (_ung_digits(r) for r in refs) if x)
    hits: List[str] = []
    for m in _UNG_CLAUSE_RE.finditer(text):
        hits.extend(_ung_values_in(m.group(0), refs_norm, refs_digits))
    return hits


def has_ungrounded_claims(text: str, has_real_tool_result: bool,
                          allowed_refs: Iterable[str] = ()) -> bool:
    """布尔判定（与 `is_schema_echo` 同风格）。"""
    return bool(ungrounded_claim_hits(text, has_real_tool_result, allowed_refs))


def _ung_rebuild(text: str, spans, drops) -> str:
    """删掉被标分句后重建：分隔符取「下一个保留分句之前」那一个；被删序列里
    出现过句末符（。！？）则优先用它收尾，避免把未完成的半句并进下句。"""
    seps = []
    for i, (s, e) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        seg = text[e:nxt]
        stripped = seg.strip()
        seps.append(stripped if stripped else ('\n' if '\n' in seg else ''))
    parts: List[str] = []
    for i, (s, e) in enumerate(spans):
        if i in drops:
            continue
        j = i + 1
        while j < len(spans) and j in drops:
            j += 1
        if j - 1 > i:
            sent = [k for k in range(i + 1, j)
                    if seps[k][:1] in _UNG_SENT_END]
            sep = seps[sent[0]] if sent else (seps[j - 1] if j - 1 < len(seps) else "")
        else:
            sep = seps[i]
        parts.append(text[s:e] + sep)
    return "".join(parts).strip()


def guard_ungrounded_claims(text: str, has_real_tool_result: bool,
                            allowed_refs: Iterable[str] = ()
                            ) -> Tuple[str, List[str]]:
    """E：无依据的具体结论 → **删除其所在分句** + 返回命中。

    :param text: 待校验文本（降级档 LLM 回复）
    :param has_real_tool_result: **主判据**——本轮有没有真实工具结果（与 k65 r2
        的 lite/downgraded gate 同一事实源）。True → 一字不改。
    :param allowed_refs: 依据表（用户自报事实 / 真实工具结果 / 检索到的原文）；
        出现在其中的具体值 = 回显或引用，放行。
    :return: (cleaned_text, hits)
    """
    if not text:
        return text, []
    if has_real_tool_result:
        return text, []
    refs = tuple(allowed_refs or ())
    refs_norm = tuple(x for x in (_ung_norm(r) for r in refs) if x)
    refs_digits = tuple(x for x in (_ung_digits(r) for r in refs) if x)
    spans = [(m.start(), m.end()) for m in _UNG_CLAUSE_RE.finditer(text)]
    drops, hits = set(), []
    for i, (s, e) in enumerate(spans):
        bad = _ung_values_in(text[s:e], refs_norm, refs_digits)
        if bad:
            drops.add(i)
            hits.extend(bad)
    if not drops:
        return text, []
    cleaned = _ung_rebuild(text, spans, drops)
    logger.warning("fact_guard: 无依据具体结论去句（%d 条命中：%s；去句 %d/%d）",
                   len(hits), "、".join(hits[:4]), len(drops), len(spans))
    return cleaned, hits


def scrub_ungrounded_claims(text: str, has_real_tool_result: bool,
                            allowed_refs: Iterable[str] = ()) -> str:
    """便捷出口（与 scrub_turn / scrub_schema_echo 同风格，独立不改它们语义）。"""
    return guard_ungrounded_claims(text, has_real_tool_result, allowed_refs)[0]


def grounded_refs_from_messages(messages: Iterable[dict]) -> Tuple[str, ...]:
    """从本轮 messages 取依据表（**已在上下文里出现过的字面值**）。

    只取文本段（content 为 str 的消息）。用户自报事实与真实工具结果都在这里，
    故模型**复述**它们不会被误杀（边界 1/2）；乱码/空段跳过。
    """
    refs: List[str] = []
    for m in messages or ():
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            refs.append(c)
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and isinstance(blk.get("text"), str) \
                        and blk["text"].strip():
                    refs.append(blk["text"])
    return tuple(refs)
