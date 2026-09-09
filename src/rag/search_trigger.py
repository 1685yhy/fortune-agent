"""联网搜索语义触发判定（k11b，2026-09-07）— 纯函数，单一事实源。

背景：PM 实诉「易宝支付这家公司怎么样」→ AI 甩锅「你自己查证」。搜索只活 chat 域
tool-loop，命理意图域（career/bazi 等引擎主链）无 needs_search/工具通道。业界共识
（/tmp/research-github-solutions.md 专项）=模型/语义决策为主 + 护栏兜底；纯关键词
门控是过时做法（OpenWebUI 实证翻车）。本模块=触发判定两层（确定性实体层 + 确定性
时效/查证层）+ 复用既有 LLM 信号（MessageAnalysis.needs_search，OR 语义）+ 硬否决
（金融行情排除、命理本地概念锚——防「今年运势如何」乱搜回归，T074 语义沿袭）。

判定顺序（decide_search）：
  1. 金融行情硬排除（产品无行情数据源：不搜不编造，T074 防回归，词表沿 handler 原文）
  2. 实体层：机构后缀族（含定中指代清理）+ 主流实体名表；实体 + 强实体问词 → 必搜；
     实体 + 弱问词（怎么样/如何）且无命理本地锚 → 搜；实体纯陈述/本地问挂靠 → 不搜
  3. 命理本地概念锚（运势/流年/取名/择日/合婚/解梦…）：无实体强问时一律不搜
  4. 时效/查证层（最近/最新/新闻/政策/多少钱/官网…）→ 无本地锚即搜
  5. 研究白名单兜底（原 handler._WEB_SEARCH_RESEARCH_RE 词条原文迁移，向后兼容）
  6. LLM 语义信号 needs_search（分析器同一调用内已产出，OR 叠加；层 1/3 仍硬否决）

护栏（执行侧，不拦判定）：频控 3 次/60s/用户（handler._search_rate_ok）、域名仅
http(s)+去重（黑名单扩展占位）、结果长度钳制 2400 字符（handler 侧）——原三层关键词
硬门控降级为护栏。

无后缀非主流实体（「极兔」类）= 本模块实体名表扩展点（注释注明追加位置），当前由
needs_search LLM 信号兜底。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# ============================================================
# 硬否决 ①：金融行情（T074 防回归——词表沿 handler.py 原 _WEB_SEARCH_FINANCE_EXCLUDE_RE
# 逐字迁移，产品无行情数据源：不搜索不编造，诚实说明）
# ============================================================
FINANCE_EXCLUDE_RE = re.compile(
    r"股市|行情|股票|基金|大盘|指数|股价|涨跌|炒股|收盘|开盘")

# ============================================================
# 硬否决 ②：命理本地概念锚（防「今年运势如何」类本地计算问题误触搜索）。
# 只收明确命理计算/场景域词；「怎么样/如何」等通用评价词刻意不收（本地问的
# 问词可挂在本地概念上，「在易宝支付上班 今年运势怎么样」的怎么样属于运势）。
# ============================================================
LOCAL_FORTUNE_ANCHOR_RE = re.compile(
    r"运势|运程|运气|财运|正财|偏财|流年|流月|大运|小运|岁运|八字|命盘|命格|排盘|生辰|"
    r"起名|取名|改名|名字|姓名学|择日|择吉|选日子|挑日子|吉日|黄历|宜忌|"
    r"搬家|入宅|乔迁|嫁娶|开业|开张|动土|奠基|提车|"
    r"合婚|合八字|合不合|配不配|般配|婚配|配对|"
    r"风水|阳宅|阴宅|面相|手相|紫微|斗数|六爻|摇卦|占卦|卜卦|奇门|遁甲|"
    r"解梦|梦见|梦到|抽签|解签|塔罗|占星|星座|"
    r"桃花|姻缘|正缘|感情运|事业运|职业运|工作运|财运|桃花运|婚姻运|姻缘运|"
    r"健康运|学业运|考试运|考运|官运|升迁运|生意运|"
    r"喜用神|日主|十神|用神|生肖|属相|"
    r"手机号|手机号码|车牌号|门牌号|尾号|数字吉凶")

# 命理本地问句口语模式（k11b-r1 审查 P1-A 扩面，真实复现必须零触发）：
# ① 「X运」族已进上表；② 个人决策族（跳槽/换工作/求职/创业/开公司/搬家/出行/
#    考编…）+ 决策问句 cue（适合…吗/好不好/该不该/什么时候/怎么选…）同句；
# ③ 命理主题词（行业/方位/公司/岗位/五行…）+ 个人适配 cue。
# 三族命中 → 本地命理问句（引擎域与 chat 域一致抑制，不搜）；
# 例外：命名实体 + 强实体问词（"易宝支付适合我吗"）由实体层优先放行（需事实查证）。
_LOCAL_DECISION_VERB_RE = re.compile(
    r"跳槽|换工作|找工作|求职|创业|开公司|开个公司|做生意|自己干|单干|"
    r"升职|晋升|考编|考公|考研|考公务员|考公职|搬家|出行|入职|离职|面试")
# 决策问句 cue：适合…吗（≤8 字内）|适合我/我适合|适不适合|好不好|该不该|要不要|
# 应不应该|可不可以|能不能|什么时候|何时|哪个方向|怎么选|去不去|值不值得|行不行|
# 时机|合不合适
_LOCAL_ASK_CUE_RE = re.compile(
    r"适合[^，。！？?]{0,8}吗|合适[^，。！？?]{0,8}吗|适合我|我适合|适不适合|好不好|"
    r"该不该|要不要|应不应该|可不可以|能不能|什么时候|何时|哪个方向|怎么选|"
    r"去不去|值不值得|行不行|时机|合不合适")
# 命理主题词（与 cue 搭配表示"本地命理决策问"，非外部事实查询）——
# 含泛行业词（银行/金融/公务员/国企… 无命名实体的"行业词+命理问"归本地）
_LOCAL_FORTUNE_THEME_RE = re.compile(
    r"行业|方位|公司|单位|岗位|职位|职业|事业|工作|五行|喜用|喜忌|命理|八字|"
    r"银行|金融|公务员|国企|央企|事业单位|体制内|教师|医生|互联网")

# ============================================================
# 实体层：机构后缀族（最长优先 alternation；定中指代清理见 _clean_entity_name）
# ============================================================
_ENTITY_SUFFIXES = (
    "股份有限公司", "有限责任公司", "责任公司", "有限公司",
    "公司", "集团", "控股", "股份", "支付", "银行", "证券", "保险", "基金",
    "信托", "期货", "科技", "网络", "信息", "软件", "数据", "智能", "电商",
    "零售", "物流", "快递", "医药", "医疗", "生物", "食品", "乳业", "饮料",
    "酒业", "地产", "置业", "物业", "能源", "石油", "石化", "矿业", "航空",
    "铁路", "传媒", "影视", "娱乐", "文化", "体育", "教育", "汽车", "家电",
    "电子", "通信", "游戏", "连锁", "商城", "旅行", "旅游", "酒店", "餐饮",
    "大学", "学院", "研究院", "医院", "出版社", "中学", "小学", "学校",
    "品牌", "产品", "楼盘", "旗舰店", "直营店", "公众号", "小程序",
    "平台", "app", "App", "APP",
)
_ENTITY_SUFFIX_ALT = "|".join(re.escape(s) for s in _ENTITY_SUFFIXES)

# 紧贴后缀前的名称串：中文/字母/数字 1-18 个字符（含定中指代，后面统一清理）
_ENTITY_NEAR_RE = re.compile(r"([一-龥A-Za-z0-9]{1,18}?)(?:" + _ENTITY_SUFFIX_ALT + r")")

# 名称串起点清理（从串尾剥）：定中结构「这家公司/这个/该公司」+ 领属「的」
# 「的/和/与/及/或/叫/为/是」仅在名称长于 2 字时剥离（「美的集团」的 的 属真名，
# 长度 2 保留；「小米的公司」长度 3 剥 的 → 小米）
_TAIL_DEICTIC_RE = re.compile(
    r"(?:这|那|该|哪)(?:家|个|所|间|些|款|款产品)?$|^我(?:们)?(?:家|公司|单位)?$")
_TAIL_LONG_JOIN_RE = re.compile(r"(?:的|和|与|及|或|叫|为|是)$")
# 名称串起点清理（从头剥）：对话虚词前缀（说/想/觉得 等绝不会出现在公司名首）
# k11b-r1（P2-C）：补 某/某些 等泛化指代（「某公司/某些平台」→ 无命名实体）
_HEAD_DEICTIC_RE = re.compile(
    r"^(?:我|你|他|她|我们|你们|他们|她们|这|那|该|哪|谁|有|是|叫|说|想|觉得|"
    r"听说|看到|梦见|梦到|请问|想问|想了解|了解一下|介绍一下|查一下|搜一下|帮我|"
    r"看看|在|到|去|某|些)*")

# 名称串可性过滤器（k11b-r1 P1-A/P2-C）：候选名称内嵌句子功能词/命理主题词/
# 行业语料词 → 判定为垃圾候选（「五行属水的行业适合开公司吗」不得抽出
# 「五行属水的行业适合开」这类伪实体）。只查词/单字的存在性：
# - 多字 token（适合/行业/五行/怎么样…）任何位置命中即拒；
# - 单字只查「内部位」（非首非尾）——「美的集团」的 的、末尾 的 规则另在
#   _clean_entity_name；真实品牌名内部几乎不含这类字，句子虚词必中招。
_ENTITY_JUNK_TOKEN_RE = re.compile(
    r"适合|行业|五行|喜用|喜忌|跳槽|怎么样|如何|还是|属于|成立|什么|哪里|哪个|"
    r"为什么|干嘛|做啥|属于|请问|帮我|这(?:家|个|些|款)|那(?:家|个|些|款)")
_ENTITY_JUNK_INTERIOR_CHARS = frozenset(
    "属我你他她它谁吗呢吧啊这那哪某些是想能要不要会是")
# 注：单字集刻意保守（首尾位豁免；太/最/更/应/和/与/及/或/到 等不收入——
# 「中国太保」「新和成」「得到」类真实品牌内部会命中，宁漏勿伤）

# 通用标签后缀（公司/机构/平台/品牌/产品/学校/单位/企业…）：实体名优先取
# 去掉标签后的名称部分（「易宝支付这家公司」→ 易宝支付 而非 易宝支付这家公司）；
# 但如果去掉后名称太短或本身是泛化词干（培训/教育/医疗…），视为无命名实体
_GENERIC_TAG_SUFFIXES = ("公司", "平台", "品牌", "产品", "机构", "企业",
                         "学校", "单位", "部门", "门店", "厂", "店")
# 泛化词干（「培训机构」「教育机构」类：不是命名实体，不做 query）
_COMMON_STEM_WORDS = frozenset(
    {"培训", "教育", "医疗", "健身", "美容", "美发", "餐饮", "金融", "理财",
     "房产", "留学", "婚庆", "摄影", "装修", "家政", "物流", "网校", "驾校",
     "中介", "门店", "单位", "部门", "平台", "机构"})

# 主流无后缀实体名表（大厂/品牌：不带「公司/科技」等后缀直接发问的形态）。
# 命中即命名实体（query=词表名）；带后缀形态由后缀族覆盖。
# 【扩展点】用户实诉/评测暴露的新实体名在此行追加（按最左最长词优先匹配）。
_KNOWN_ENTITIES = (
    "阿里巴巴", "字节跳动", "腾讯", "百度", "京东", "拼多多", "美团",
    "小米", "华为", "苹果", "三星", "谷歌", "微软", "亚马逊", "特斯拉",
    "比亚迪", "宁德时代", "理想", "蔚来", "小鹏", "快手", "哔哩哔哩",
    "抖音", "网易", "新浪", "搜狐", "滴滴", "顺丰", "中通", "圆通", "韵达",
    "申通", "极兔", "中国移动", "中国联通", "中国电信", "中国石油", "中国石化",
    "中海油", "国家电网", "南方电网", "中国人寿", "中国人保", "中国平安",
    "招商银行", "浦发银行", "兴业银行", "中信银行", "光大银行", "民生银行",
    "微信支付", "支付宝", "银联", "网联",
)
_KNOWN_ENTITY_RE = re.compile(
    "|".join(re.escape(e) for e in sorted(_KNOWN_ENTITIES, key=len, reverse=True)))

# ============================================================
# 实体问词：
# 强实体问词 = 只对命名实体有意义的事实/查证词（命中即搜，不受本地锚否决）；
# 弱问词 = 通用评价词（怎么样/如何/咋样），可能挂在命理本地概念上，
#          需无本地锚才搜（「在易宝支付上班 今年运势怎么样」不搜）。
# ============================================================
_ENTITY_ASK_STRONG_RE = re.compile(
    r"靠不靠谱|靠谱吗|靠谱|可靠吗|可靠|正规吗|正规|可信吗|可信|"
    r"评价|口碑|评分|待遇|工资|薪资|薪酬|福利|五险一金|加班|"
    r"裁员|欠薪|欠钱|暴雷|跑路|倒闭|破产|清算|"
    r"上市|融资|市值|财报|营收|盈利|业绩|规模|员工|创始|"
    r"是做什么|做什么的|做啥的|干啥的|主营业务|主营|业务范围|"
    r"查一下|查查|帮我查|搜一下|搜搜|帮我搜|了解一下|了解下|介绍下|介绍一下|"
    r"多少钱|价格|贵不贵|值不值得|值得(?:去|进|投|买|考)?|适不适合|适合我吗|适合我|"
    r"合不合适|合适吗|合适我吗|合适我|"
    r"能进吗|好进吗|难进吗|怎么进|面试|招聘|校招|社招|内推|"
    r"官网|网址|官网地址|总部|电话|法人|CEO|ceo|总裁|董事长|发展前景|前景")
# 注：时效/查证词（最新/最近/今年/新闻/政策/消息/近况/动态）刻意不进强问词——
# 它们可能挂在命理本地概念上（「最近运势」），实体+时效由实体层 timely 分支
# （须无本地锚）覆盖；强问词只收对命名实体有意义的事实/查证词。
_ENTITY_ASK_WEAK_RE = re.compile(r"怎么样|怎样|咋样|如何|怎么样啊|咋回事")

# ============================================================
# 时效/查证层（无实体时的外部事实词；需无本地锚才搜）
# ============================================================
_TIMELY_EXTERNAL_RE = re.compile(
    r"最近|最新|近期|近况|新闻|新规|政策|法规|规定|发布会|赛事|比分|票房|"
    r"多少钱|价格|房价|油价|金价|"
    r"网上|网上说|报道|媒体|官方|官网|辟谣|真假|传闻|"
    r"查一下|查查|搜一下|搜搜|搜|了解一下|了解下|看下|看看")
# 注：评价/口碑/待遇/工资/裁员/财报等"对某主体的评估/事实词"不进时效层——
# 它们需要命名实体作主语（实体层强问词覆盖）；无实体时触发只会产出垃圾 query
# （「某新成立的医馆口碑如何」不搜，除非 LLM 语义信号 needs_search）。

# 研究白名单兜底（原 handler._WEB_SEARCH_RESEARCH_RE 词条原文迁移——
# 含「今天股市行情怎么样」族白名单词时仍被金融排除与本地锚否决）
RESEARCH_WHITELIST_RE = re.compile(
    r"公司|行业|政策|新闻|最新|数据|报告|研究|人物|事件|天气|航班|比赛|"
    r"赛事|比分|电影|产品|品牌|评测|排名|价格|楼盘|地产|学校|专业|医院|"
    r"职位|薪资|招聘|面试|考试|世界杯|发布会")

# 纯指代无命名主体（「这家公司/哪个公司/什么样的工作」）——白名单/时效层不得
# 为这类无实体的泛化表述产出搜索（query 无检索价值）；命名实体由实体层先捕获
# k11b-r1（P2-B）：行业/方向 不入表——「最近有什么行业新闻」是合法外部时效问
# （什么+行业 的泛化表述有检索价值），须放行；个人适配类由命理主题族（本地锚）
# 拦截
_DEICTIC_NAME_RE = re.compile(
    r"(?:这|那|哪|该|什么|啥|几)(?:[家个所种些款类样]{0,3})?"
    r"(?:公司|机构|平台|品牌|产品|单位|企业|医院|学校|专业|职位|岗位|"
    r"工作|部门|楼盘|电影|比赛|项目|城市|地方)")


def _is_deictic_only(text: str) -> bool:
    """是否纯指代泛化表述（无命名主体可检索）。"""
    return bool(_DEICTIC_NAME_RE.search(text or ""))


@dataclass
class SearchDecision:
    """decide_search 结果。

    - should_search: 是否应发起联网检索
    - query: 检索关键词（实体名 或 原句精简；空=无 query 可搜）
    - entity: 抽取出的命名实体（空=未抽到，query 为整句精简）
    - reason: 触发依据（entity / entity_weak / timely / whitelist / llm /
      none; finance / local / deictic = 否决/不触发）
    """
    should_search: bool
    query: str = ""
    entity: str = ""
    reason: str = "none"


def _clean_entity_name(name: str) -> str:
    """清理后缀前的名称串：剥尾部的定中指代（这家/那个/该公司/的）与虚词前缀。"""
    if not name:
        return ""
    n = name
    while True:
        m = _TAIL_DEICTIC_RE.search(n)
        if m and m.end() == len(n):
            n = n[: m.start()].rstrip("，。 ")
            continue
        if len(n) > 2:
            m = _TAIL_LONG_JOIN_RE.search(n)
            if m and m.end() == len(n):
                n = n[: m.start()].rstrip("，。 ")
                continue
        break
    while True:
        m = _HEAD_DEICTIC_RE.match(n)
        if m and m.end() > 0:
            n = n[m.end():]
            continue
        break
    return n.strip()


def _entity_candidate_plausible(name: str) -> bool:
    """垃圾候选过滤：名称串内嵌句子功能词/命理主题词 → 非命名实体。

    k11b-r1（P1-A 复现③「五行属水的行业适合开公司吗」会抽到
    「五行属水的行业适合开」伪实体）：多字 token 任何位置命中即拒；
    单字只查内部位（首尾豁免——「中国太保」的 太/「美的」的 的 不误伤）。
    """
    if not name:
        return False
    if _ENTITY_JUNK_TOKEN_RE.search(name):
        return False
    inner = name[1:-1] if len(name) > 2 else ""
    return not any(ch in _ENTITY_JUNK_INTERIOR_CHARS for ch in inner)


def extract_entity_mentions(text: str) -> List[str]:
    """实体抽取：返回命名实体候选列表（已去重保序）。

    覆盖：① 后缀族（机构/支付/银行/平台/APP/大学…），自动剥离定中指代与虚词；
    ② 主流实体名表（无后缀大厂形态）。
    通用标签后缀（公司/平台…）且名称部分过短或为泛化词干（培训/教育…）→ 不算
    命名实体（「哪个公司靠谱」无实体可搜）；名称串含句子功能词/命理主题词
    （适合/行业/五行/是/的…内嵌）→ 垃圾候选不算（「去开公司适合我吗」不抽出
    「开公司」类伪实体）。仅探测，不做归一/消歧。
    """
    if not text:
        return []
    out: List[str] = []
    for m in _ENTITY_NEAR_RE.finditer(text):
        raw = m.group(1)
        name = _clean_entity_name(raw)
        if len(name) < 2 or len(name) > 16:
            continue
        if not _entity_candidate_plausible(name):
            continue
        suffix = m.group(0)[len(raw):]
        if suffix in _GENERIC_TAG_SUFFIXES:
            stem = name.lower()
            if stem in _COMMON_STEM_WORDS or not stem:
                continue
            entity = name  # 去掉通用标签：「易宝支付这家公司」→ 易宝支付
        else:
            entity = name + suffix
        if entity not in out:
            out.append(entity)
    for m in _KNOWN_ENTITY_RE.finditer(text):
        e = m.group(0)
        if e not in out:
            out.append(e)
    return out


def has_entity_ask(text: str) -> tuple:
    """返回 (strong, weak)：实体问词命中情况（强=只对命名实体有意义的事实/查证词；
    弱=通用评价词，可能挂在命理本地概念上）。"""
    return (bool(_ENTITY_ASK_STRONG_RE.search(text or "")),
            bool(_ENTITY_ASK_WEAK_RE.search(text or "")))


def _local_fortune_verdict(text: str) -> tuple:
    """命理本地判定两档语义（k17-3：单扫 LOCAL_FORTUNE_ANCHOR_RE，去重装饰开销）。

    原实现 decide_search 内先整扫一次算出 calc（硬锚）再经 has_local_fortune_anchor
    内再整扫同一正则（软路径如「跳槽什么时候合适」=同正则两遍，k11b-r1 审查残留
    P3 记录）。抽本函数后 has_local_fortune_anchor / decide_search 共用一次扫描。

    语义（k11b-r1 P1-A/P2-B 两档，逐字未变）：
    - calc（硬锚）= 本地概念词表命中（运势/流年/X运/取名/择日/合婚/解梦/风水…），
      无论有无命名实体都否决（实体只是背景：「在易宝支付上班 今年运势怎么样」不搜）；
    - local = calc 或 口语决策族/命理主题族（跳槽+什么时候、行业词+适合我吗…
      无命名实体时否决；有真实命名实体时不拦——「XX科技公司什么时候发财报」是
      合法外部时效问，实体层负责放行）。
    返回 (calc, local)。
    """
    if not text:
        return (False, False)
    hard = bool(LOCAL_FORTUNE_ANCHOR_RE.search(text))
    if hard:
        return (True, True)
    cue = _LOCAL_ASK_CUE_RE.search(text)
    if not cue:
        return (False, False)
    if _LOCAL_DECISION_VERB_RE.search(text):
        return (False, True)
    return (False, bool(_LOCAL_FORTUNE_THEME_RE.search(text)))


def has_local_fortune_anchor(text: str) -> bool:
    """命理本地判定（k11b-r1 P1-A 扩面，三层任一命中 → True，调用方零搜索）：

    ① 本地概念词表（运势/流年/取名/择日/合婚/解梦/风水… + 「X运」族：
       工作运/事业运/财运/桃花运/婚姻运/健康运/学业运/考试运/官运…）；
    ② 个人决策族动词（跳槽/换工作/求职/创业/开公司/搬家/出行/考编/面试…）
       与决策问句 cue（适合…吗/我适合/好不好/该不该/什么时候/怎么选…）同句；
    ③ 命理主题词（行业/方位/公司/五行/银行/金融/公务员…）+ cue 同句。
    例外：命名实体+强实体问词（「易宝支付适合我吗」）由实体层优先放行——
    实体层需要该实体的外部事实（见 decide_search 顺序注释）。

    k17-3：实现委托 _local_fortune_verdict()[1]（单扫，语义逐字未变）。
    """
    return _local_fortune_verdict(text)[1]


def is_finance_excluded(text: str) -> bool:
    """金融行情硬排除（产品无行情数据源：不搜不编造，T074 语义沿袭）。"""
    return bool(FINANCE_EXCLUDE_RE.search(text or ""))


def build_search_query(text: str, entity: str = "") -> str:
    """构造检索关键词：实体名优先；无实体 → 复用 web_search._simplify_query 精简
    （剥请求前缀/问句尾巴/当前年份/堆叠修饰段），再补剥「我想/我打算…」头。"""
    if entity:
        return entity[:70]
    from src.rag.web_search import _simplify_query
    q = (text or "").strip()
    q = re.sub(
        r"^(?:我|我想|我想要|我打算|我准备|我考虑|我在考虑|考虑|准备|打算|"
        r"想去|要去|想|要|想问问|想问|想知道|我想知道|请问)*", "", q, count=1)
    return _simplify_query(q)[:70]


def decide_search(text: str, llm_needs_search: bool = False) -> SearchDecision:
    """语义触发判定（纯函数，无网络无 LLM）：见模块注释的顺序表。

    llm_needs_search: MessageAnalysis.needs_search（分析器同一次 LLM 调用产出，
    复用既有通道的模型语义信号；OR 叠加，层 1/3 硬否决仍生效）。
    """
    msg = (text or "").strip()
    if not msg:
        return SearchDecision(False)
    # 层 1：金融行情硬排除（词表原文，T074）
    if is_finance_excluded(msg):
        return SearchDecision(False, reason="finance")
    entities = extract_entity_mentions(msg)
    strong, weak = has_entity_ask(msg)
    # 命理本地判定分两档（k11b-r1 P1-A/P2-B；k17-3 单扫，语义逐字未变，
    # 见 _local_fortune_verdict 注释）：
    # - calc = 硬锚（运势/流年/X运/取名/择日/合婚/解梦…词表）——无论有无命名实体
    #   都否决（实体只是背景：『在易宝支付上班 今年运势怎么样』不搜）；
    # - local = 硬锚 + 口语决策族/命理主题族（跳槽+什么时候、行业词+适合我吗…
    #   无命名实体时否决；有真实命名实体时不拦——『XX科技公司什么时候发财报』
    #   是合法外部时效问，实体层负责放行）
    calc, local = _local_fortune_verdict(msg)
    # 层 2：实体层
    if entities:
        entity = entities[0]
        # 强实体问词（靠谱/评价/待遇/是做什么/适合我吗…）→ 必搜
        if strong:
            return SearchDecision(True, query=build_search_query(msg, entity),
                                  entity=entity, reason="entity")
        # 弱问词（怎么样/如何）须无硬锚才搜——「在易宝支付上班，今年运势怎么样」
        # 的怎么样挂在运势上，实体只是背景（硬锚否决）
        if not calc and weak:
            return SearchDecision(True, query=build_search_query(msg, entity),
                                  entity=entity, reason="entity")
        # 实体 + 时效/查证词（易宝支付 最近新闻…）→ 搜（query=实体名）
        if not calc and (_TIMELY_EXTERNAL_RE.search(msg)
                         or RESEARCH_WHITELIST_RE.search(msg)):
            return SearchDecision(True, query=build_search_query(msg, entity),
                                  entity=entity, reason="entity")
        # 实体纯陈述（无任何问词）+ LLM 判需实时 → 搜（对话续问形态）
        if not calc and llm_needs_search:
            return SearchDecision(True, query=build_search_query(msg, entity),
                                  entity=entity, reason="entity")
        # 其余（纯陈述 / 硬锚本地问挂靠）→ 不搜
        return SearchDecision(False, reason="local" if calc else "none")
    # 层 3：命理本地判定（无命名实体时：本地计算/决策问句绝不触发——
    # 含口语决策族与命理主题族，见 has_local_fortune_anchor 注释）
    if local:
        return SearchDecision(False, reason="local")
    # 层 4：时效/查证层（纯指代泛化表述——无命名主体，query 无检索价值——不触发）
    if _TIMELY_EXTERNAL_RE.search(msg) and not _is_deictic_only(msg):
        return SearchDecision(True, query=build_search_query(msg),
                              reason="timely")
    # 层 5：研究白名单兜底（向后兼容 chat 域旧门控语义；纯指代不触发）
    # k17-4（k11b-r1 P2-B 残留语义记录）：「这个行业怎么样」类——行业/公司 为旧
    # handler._WEB_SEARCH_RESEARCH_RE 头号词，行业移出 _DEICTIC_NAME_RE 后经本层
    # 触发 = 与旧关键词硬门控语义等价（非回归），query=整句精简（「这个行业」类
    # 泛化词，检索价值有限但走真实检索+来源尾注，绝不甩锅）。方向建议（宁搜勿漏
    # 为本位——PM 实诉「你自己查」教训；收紧须先有实体/主题解析覆盖证据）：
    # 后续收紧候选 = 语义路由（semantic-router，k11b plan §七备选）或本层加
    # 「可检索主体」启发（纯指代句 + 无时效词才考虑拦），待实体解析增强批评估。
    if RESEARCH_WHITELIST_RE.search(msg) and not _is_deictic_only(msg):
        return SearchDecision(True, query=build_search_query(msg),
                              reason="whitelist")
    # 层 6：LLM 语义信号（复用既有分析器通道，不新增二判 LLM）
    if llm_needs_search:
        return SearchDecision(True, query=build_search_query(msg),
                              reason="llm")
    return SearchDecision(False, reason="none")


# ============================================================
# 来源痕迹确定性尾注（回复层兜底；LLM 漏写来源时补齐）
# ============================================================

_FEEDBACK_PROMPT_MARK = "———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"


def _site_label(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def append_source_trace(reply: str, entity: str, domains: list) -> str:
    """回复缺来源痕迹时确定性补来源尾注（纯函数，禁裸 JSON/泄漏）。

    条件（全部满足才补）：回复非空、含实体名、不含「来源」字样、未出现任一
    检索站点域名（LLM 已在正文给站点 → 视为已有来源痕迹，不重复补）。
    注意：不因「http」整体跳过——回复可能带排盘卡图片/图床 URL（124.221…
    类），与检索来源无关，不得因此漏补。
    尾注插在反馈提示行（———…准/不准）之前，避免破坏「准/不准」交互位；
    无反馈提示则追加在回复末尾。
    """
    if not reply or not entity or not domains:
        return reply
    if entity not in reply:
        return reply
    if "来源" in reply:
        return reply
    sites = [_site_label(d) for d in domains[:5] if _site_label(d)]
    if not sites:
        return reply
    if any(s in reply for s in sites):  # 正文已含站点域名 → 有来源痕迹
        return reply
    note = (f"\n\n（信息来源：{'、'.join(sites)} 等第三方公开网络内容，"
            "仅供参考，具体请以官方渠道核实为准）")
    # 回复以反馈提示收尾（———…准/不准）时，尾注插在反馈提示之前，
    # 保证「准/不准」交互位不被来源行挤开；其余情况追加在末尾
    idx = reply.rfind(_FEEDBACK_PROMPT_MARK)
    if idx > 0 and reply[idx:].strip() == _FEEDBACK_PROMPT_MARK:
        return reply[:idx].rstrip() + note + "\n\n" + reply[idx:]
    return reply + note
