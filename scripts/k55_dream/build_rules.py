#!/usr/bin/env python3
"""规则层生成器：**从语料统计结果**产出 80+ 条梦境模式（brief §2）。

输入（全部由 stats_elements.py / public_domain.py 产出，均为真实语料统计）：
- reports/element_freq_top.csv     元素覆盖率排名
- reports/element_symbols.json     每元素的核心象征候选（语料共现实词）
- clean/public_domain_quotes.jsonl 公版古籍条文（传统释义来源）
- data/competitor_data/zgjm_*.jsonl 站点自有分类（→ 规则「类型」的数据来源）

产出：
- src/engines/dream_rules.py   生成的规则模块（入库、可复现、带统计依据）
- reports/rule_table.csv       规则清单（含覆盖量），便于人工复核

设计口径：
- **类型**：优先取语料来源站点的自有分类（dongwu→动物类 等，站点分类是数据
  不是我们编的）；无站点分类时按吉凶/情绪统计归入 吉兆类/警示类/中性类。
- **核心象征**：取该元素语料正文共现最高的实词（已滤语料套话）。
- **情绪基调**：由该元素语料的吉/凶判词倾向 + 高频情绪词合成。
- **传统释义**：优先引公版古籍条文；无则取该元素语料中最高频的判词句。
- **覆盖量**：含该元素的语料条目数（真实统计值，可为 0——见「交通驾驶族」）。
- **match 正则**：每个候选分支都 ≥2 字（k49 教训：单字会前缀误伤）；
  单字元素（蛇/水/车…）用 n-gram 统计出的 ≥2 字组合成 pattern。

用法：
    python scripts/k55_dream/build_rules.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream import collocation as colloc_mod  # noqa: E402
from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402
from scripts.k55_dream.stats_elements import (  # noqa: E402
    META_STOPWORDS, load_entries, normalize_core,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# 输出位置可用环境变量重定向（**只给测试/冒烟用**：让入口能在临时目录里真跑一遍，
# 不碰受审的冻结表与线上 reports）。r10 C-1 的教训：入口从没被跑过，崩了都没人知道。
REPORTS = Path(os.environ["K55_REPORTS_DIR"]) if os.environ.get("K55_REPORTS_DIR") \
    else DATA_ROOT / "reports"
OUT_MODULE = Path(os.environ["K55_OUT_MODULE"]) if os.environ.get("K55_OUT_MODULE") \
    else Path(__file__).resolve().parents[2] / "src" / "engines" / "dream_rules.py"
OUT_TABLE = REPORTS / "rule_table.csv"

# 站点自有分类 → 我们对外呈现的「梦境类型」（站点分类是数据，映射表是口径）
SITE_CAT_TO_TYPE = {
    "dongwu": "动物类", "zhiwu": "植物类", "wupin": "物品类",
    "shenghuo": "生活类", "ziran": "自然类", "guishen": "鬼神类",
    "jianzhu": "建筑类", "renwu": "人物类", "huodong": "活动类",
    "yunfujiemeng": "孕育类", "mengjing": "心理类", "wenhua": "文化类",
    "health": "健康类", "qita": "其他类",
}

# 叙述性动词/虚词：语料里高频但不是「梦境意象」，进规则层只会产生假命中
# （实测清单：变成/参加/发生/得到/看到/送给/交谈/出来/成为/没有/开始…）
NARRATIVE_STOP = {
    "变成", "参加", "发生", "得到", "看到", "送给", "交谈", "出来", "成为",
    "没有", "开始", "继续", "进行", "出现", "遇到", "发现", "告诉", "知道",
    "觉得", "感觉", "认为", "希望", "方向", "事情", "时候", "地方", "东西",
    "问题", "情况", "身上", "里面", "外面", "旁边", "一起", "可以", "应该",
    "什么", "怎么", "因为", "所以", "但是", "而且", "并且", "或者", "还是",
    "就是", "不是", "就是", "一样", "非常", "特别", "真的", "好像", "突然",
    "一直", "已经", "正在", "之后", "之前", "现在", "最近", "很多", "很多",
    "话", "时", "事", "空", "成", "梦", "夢", "人", "好", "多", "大", "小",
    # 触发词/元词：绝不能变成规则条目（「梦见」当正则会命中一切梦境文本）
    "梦见", "梦到", "梦见了", "做梦", "解梦", "周公", "梦境", "预兆", "征兆",
}

# brief 明令必须覆盖的交通/驾驶族（控制方真实梦例所在族）。
# 覆盖量取自真实语料统计（含新增爬取的真实网友梦境），统计不到就如实记 0。
#
# r2 整改（控制方 2026-09-18 裁决）：
# ① **luck 一律「中性」**，不许按语料词频自动判吉——「梦见车祸是吉」是信任事故。
#    语料里这些词确实常出现在吉向句子里，但那些判词谈的是**别的场景**
#    （如「梦见老人出车祸…虽有财运可得」），拿词频自动推吉凶是错的。
# ② **match 收窄到车辆语义**：「迟到/来不及」不是车（实测「梦见上学迟到了」
#    被误判成赶不上车），已移出本族，另立独立规则（见 MANDATORY_EXTRA）。
# ③ gloss 必须取**同场景句**，取不到走诚实兜底（见 same_scenario_gloss）。
# 强制中性族（k58 r5 裁决三：**进族判据**，勿按「看起来都是车」扩族）：
#   族内成员 = **现代**交通工具/情境，古籍成书时无对应意象 → 无传统依据可依
#              → 不做吉凶推断（立论源自 k55-r3 的 `车祸` 教训：把它判成「传统倾向=吉」
#                是拿现代题材硬套古法）。
#   **不入族** = 有古籍依据的古代意象。典型：`马车` —— 古代本以车马为象
#              （《敦煌本梦书》「梦见马，吉；乘行，大富」），故按**语料统计**走
#              （当前 `吉多于凶`），不以「现代无依据」为由中性化（理由不成立）。
#   新增成员前先自问：这个意象在古籍梦书里有依据吗？有 → 不入族。
MANDATORY_DRIVING = {
    "开车": {"match": ["开车", "驾驶", "自驾", "开夜车", "开快车"],
             "type": "现代类", "tone": "提醒：掌控感与失控担忧", "luck": "中性"},
    "车祸": {"match": ["车祸", "出车祸", "撞车", "翻车", "撞人", "被撞", "追尾"],
             "type": "现代类", "tone": "提醒：惊吓与安全焦虑", "luck": "中性"},
    "停车": {"match": ["停车", "停车位", "泊车", "倒车", "车位"],
             "type": "现代类", "tone": "提醒：秩序与掌控需求", "luck": "中性"},
    "找不到车": {"match": ["找不到车", "车不见", "车没了", "车丢了", "忘了车停"],
                 "type": "现代类", "tone": "提醒：失控与遗失焦虑", "luck": "中性"},
    "迷路": {"match": ["迷路", "找不到路", "找不到回", "走错路", "认不得路", "绕不出去"],
             "type": "压力类", "tone": "提醒：方向感缺失与焦虑", "luck": "中性"},
    "赶不上车": {"match": ["赶不上车", "没赶上车", "错过车", "误车", "误机",
                        "坐过站", "赶车", "赶飞机", "错过班车", "错过火车",
                        "错过航班", "错过地铁", "没赶上"],
                 "type": "压力类", "tone": "提醒：时间压迫与错失恐惧", "luck": "中性"},
    "堵车": {"match": ["堵车", "塞车", "堵在路上", "堵在路"],
             "type": "现代类", "tone": "提醒：停滞与无力感", "luck": "中性"},
    "坐车": {"match": ["坐车", "坐公交", "坐地铁", "乘公交", "坐火车", "坐飞机", "搭车"],
             "type": "现代类", "tone": "提醒：被动与随波", "luck": "中性"},
}

# 从交通族里**拆出来**的独立规则（r2 I-3）：「迟到/来不及」表达的是时间压迫，
# 不含车辆语义，塞进「赶不上车」会误伤（实测「梦见上学迟到了」）。
MANDATORY_EXTRA = {
    "迟到": {"match": ["迟到", "来不及", "赶时间", "时间不够", "误点", "拖延"],
             "type": "压力类", "tone": "提醒：时间压力与自责", "luck": "提醒类"},
}

# 规则候选的停用词 = 叙述词 ∪ 体裁性元词（r2 I-4）。
# 元词表来自 stats_elements.META_STOPWORDS（其注释原文就是「语料体裁的产物，
# 不是梦的象征」），初版只把它用在 symbols 上，没用在规则候选上 → 工作/表示/
# 生活/说明/关系/可能/方面/象征/女性/运势/心理/梦者 这些词混进了规则表。
RULE_STOPWORDS = set(META_STOPWORDS) | set(NARRATIVE_STOP) | {
    "意味", "受到", "看见", "认识", "起来", "感到", "作为", "关于", "对于",
    "以及", "或者", "如果", "虽然", "然后", "于是", "终于", "结果", "原因",
    "状态", "程度", "方式", "内容", "部分", "以上", "以下", "其中", "其他",
    "一切", "所有", "各种", "一种", "这个", "那个", "这样", "那样",
    # k58：方位/趋向补语类叙述词（实测「回来」混进过规则表并被控制方梦例命中）
    "回来", "过来", "回去", "进去", "出去", "上来", "下来", "回来",
    "来到", "离开", "走过", "路过", "回去", "起来",
    # k58 r2（I-3）：词缀/功能字/程度副词/碎片 —— 实测混进规则表并在真实梦例上开火
    "子", "面", "公", "母", "活", "男", "身", "女", "老", "小", "大",
    "很大", "不到", "不见", "上长", "上长", "不见到", "一", "个", "们",
    "吉兆", "凶兆", "征兆", "解夢", "意思", "含义", "寓意", "解析", "分析",
    # r4（审查 m-4）：抽象/叙述词 —— 不在任何停用表里但也不是梦境意象
    "情景", "场面", "使用", "接受", "收到", "喜欢",
}

# 元素词性门槛（I-3 不变式）：规则名允许的词性 —— 名词类 + 动词类（开车/迟到）。
# 明确排除：副词(d)、形容词(a)、数词(m)、量词(q)、代词(r)、介词(p)、连词(c)、
# 助词(u)、方位(f)、时间(t)、语气(y)、拟声(o)、字符串(x)、区别词(b)、名动词以外的前缀(ng)。
# 只**拦明确的功能/程度/数量/代词类**，不认识的标记一律放行 ——
# 反向白名单会误伤（jieba 把「车」标成 zg、「迷路」标成 n 之外的标记，
# 白名单式过滤把「车/头/菜/鸟」这些正经意象挡在门外，实测踩到）。
# 只拦**功能词/形容词/状态词/名语素**（这些不是「梦境意象」）；
# 不拦 ns/nr/nt（jieba 把 河/太阳/大海/乌龟/玉米 都标成地名/人名类，
# 它们是正经意象 —— 反向白名单式过滤会误伤，实测踩到）。
BLOCKED_POS = {"u", "d", "r", "p", "c", "m", "q", "f", "t", "y", "e", "o",
               "x", "b", "a", "z", "ad", "an", "ag", "ng"}


def pos_allowed(name: str) -> bool:
    """词性门槛（k58 r3 m-1 修正：看**中心词**，不是「任一 token」）。

    jieba 把「大蛇」切成 大(a)+蛇(n) —— 若按「任一 token 被拦就拒」，
    这个正经名词（覆盖 143）会被误排（实测：梦见大蛇 → [] 而梦见小蛇正常）。
    改为：只有**中心词（最后一个 token）**属于被拦词性，或**全部 token** 都被拦时才拒。
    """
    try:
        import jieba.posseg as pseg
        flags = [f for _, f in pseg.lcut(name)]
    except Exception:
        return True
    if not flags:
        return True
    if flags[-1] not in BLOCKED_POS:
        return True
    return not all(f in BLOCKED_POS for f in flags)


# 吉凶判定词（语料判词口径，用于从真实语料统计吉凶倾向）
# k58 r3（A-1）：词表**扩到与审查口径对齐**（审查用更宽的词表扫出 9 条反向）。
# 原词表只认判词术语，「前途光明/特别有发展/财运不佳/非常窝火/波折/量入为出/欠顺」
# 这类表述一律返回「无极性」→ 选择器（同向>无极性>反向）合法地选了反向句。
# r4（C-1/I-1）：**再补裸「吉」等** —— 古籍引文「梦见马，吉；乘行，大富」里的
# 「吉」「大富」旧词表都不认 → 判成「无极性」→ 在「凶」luck 下被直接采用，
# 造成用户可见的自相矛盾。词表只增不减（k49 教训：每轮都能构造新变体，
# 所以除了补词，选句语义也一并收紧 —— 见 same_scenario_gloss 的「同向硬要求」）。
JI_RE = re.compile(r"(大吉|吉利|吉兆|吉凶指数\d+【?大吉|好运|发财|得财|进财|升官|富贵|"
                   r"喜事|顺利|成功|贵子|添丁|有财|主吉|大吉昌|"
                   r"光明|发展|兴旺|顺遂|幸福|美满|高升|如意|发达|转运|喜讯|和睦|健康"
                   # 判词位置的**裸「吉」**（C-1：「梦见马，吉；乘行，大富」）——
                   # 必须锚定在分隔符之间，否则「吉」会命中 吉尔吉斯/吉祥物 这类无关串，
                   # 把 counts 抬成噪声（实测裸子串会把 马 抬到 28:2）
                   r"|(?<![一-鿿])吉(?![一-鿿])|大富|大贵|得利|有益|有喜|利好|安泰|丰盈|荣华)")
XIONG_RE = re.compile(r"(大凶|不祥|凶兆|灾祸|倒霉|损失|破财|疾病|病痛|死亡|丧事|口舌|"
                      r"官司|离别|不顺|小人|血光|主凶|凶事|有灾|患病|"
                      r"不佳|窝火|波折|欠顺|谨慎|小心|量入为出|吃亏|争吵|矛盾|"
                      r"担忧|麻烦|阻碍|困难|"
                      # 同理：裸「凶」只在判词位置认（锚定分隔符）
                      r"(?<![一-鿿])凶(?![一-鿿])|不幸|不吉|不利|不测|灾厄)")
SENT_SPLIT_RE = re.compile(r"[。！？!?\n]")


def split_sentences(text: str) -> list:
    """**句子池的唯一来源**（k55-r3 教训：句池与计数必须同源，否则出假兜底）。

    k60 已把切分收敛成 `split_sentences()`；本基线（55ea3f7）还没有该函数，
    因此这里先提供**同名同语义**的可替换单点：k60 合并后直接换成它的实现即可，
    调用点（scan_corpus / 测试）无需改动。
    """
    return [x.strip() for x in SENT_SPLIT_RE.split(text or "") if x.strip()]


# ── 折叠键（r6 I-1）：标点/空白归一 ───────────────────────────────────
# 只折叠「逐字相同」的串是不够的：站点转码会把 `，；` 写成 `,;`，
# 「梦见马，吉；乘行，大富」与「梦见马，吉;乘行，大富」是**同一句**，
# 却被算成两条独立证据 —— r5 报告里我把这两个变体写成了「唯一句 2:1」，
# 即把**未折叠**的证据当成已折叠的（自相矛盾，控制方裁决一已认下）。
# 归一化口径由本函数**单点**提供：句池去重与方向计数共用（同源）。
PUNCT_FOLD_TABLE = str.maketrans({
    "，": ",", "、": ",", "。": ".", "；": ";", "：": ":", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "“": '"', "”": '"', "‘": "'", "’": "'",
    "．": ".", "～": "~", "—": "-", "－": "-", "·": ".",
})


def sentence_key(s: str) -> str:
    """句子的**折叠键**：标点/空白归一后的串（唯一句折叠 + 句池去重共用）。

    **显示文本不变**（逐字引文约束：语料原文与规则表引文照原样保留），
    本键只回答「这两句是不是同一句」。
    """
    return re.sub(r"\s+", "", (s or "").translate(PUNCT_FOLD_TABLE))


# **同源锁（r7 裁决一）**：句池去重键与计数折叠键**必须是同一个函数对象**。
# 审查植入「只改句池的键、不改计数的键」时锁全绿 —— 说明「同源」当时只是
# 「恰好一致」，不是「改了就会红」。现在两条路径各自引用下面这两个**常量**，
# 并由 `test_pool_and_counting_share_one_folding_key` 断言二者 `is` 同一个函数
# （任何一侧被换成别的归一化 → 断言直接失败，而不是静默分裂）。
SENTENCE_KEY_FOR_POOL = sentence_key      # 句池去重键
SENTENCE_KEY_FOR_COUNT = sentence_key     # 计数折叠键（必须与上者同源）


def count_direction_sentences(scope_sents, exclude_keys=None) -> tuple:
    """方向计数（**唯一句·折叠后**，r5 Important-1 + r6 I-1）—— 档位判定的唯一输入。

    返回 (吉向唯一句**键**集合, 凶向唯一句**键**集合)。**出现次数不参与**：
    实测 `马` 的 27 个"吉"来自同一句古籍的两个标点变体、`酒` 的 23 个"吉"
    来自同一句（唯一句 1 条）却被判 `大吉` —— 按出现次数定档就是被模板/重复句绑架。

    r6：先按 `sentence_key()` 折叠（标点/空白归一）再计数（裁决一：归一化必须在计数前，
    且与句池口径同源）；`exclude_keys` 排除**公式句**（站点模板/大批量转载，见
    `formula_sentence_keys()`，裁决四）。
    """
    ji_s, xiong_s = set(), set()
    for s in scope_sents:
        k = SENTENCE_KEY_FOR_COUNT(s)
        if exclude_keys and k in exclude_keys:
            continue
        if k in ji_s or k in xiong_s:
            continue
        j, x = bool(JI_STRONG.search(s)), bool(XIONG_STRONG.search(s))
        if j and not x:
            ji_s.add(k)
        elif x and not j:
            xiong_s.add(k)
    return ji_s, xiong_s


def sentence_is_usable(s: str, el: str) -> bool:
    """句子能否作为**释义依据**：必须**提及该元素**、且不是空壳。

    这是 k60 回放抓到的漏口（`死亡` 的 gloss 曾是书名碎片「《敦煌本梦书》」）：
    原来 `is_head`（词条即该元素）分支「**整条都算**」，书名碎片就从这条路径
    混进 gloss —— 它既不是模板句、也不含该元素，却因为「整条都算」被选中。
    现在**两条判据同源**：无论走 head 路径还是同场景路径，
    候选句一律要与该元素相关，且过 is_citation_only / is_heading / is_fragment。
    """
    if not (6 <= len(s) <= 120):
        return False
    if el and el not in s:
        return False                      # 不提及该元素的句子当不了它的依据句
    return not (is_citation_only(s) or is_heading(s) or is_fragment(s)
                or TITLE_JUNK_RE.search(s))


# ── 方向计数专用：**判词术语**（r4，控制方裁决一/二）─────────────────────
# 与「一致性检查」用的宽词表（JI_RE/XIONG_RE）分开：
# - JI_RE/XIONG_RE（宽）：判断「这句话的语气是不是跟结论相反」——宁宽勿漏；
# - JI_STRONG/XIONG_STRONG（窄，只用判词术语）：**统计方向**——宁准勿滥。
# 为什么必须分开：宽词表里有 光明/发展/健康/幸福/成功/顺利 这类**泛化正向词**，
# 现代解读文本几乎每句都有 → 全体偏吉（实测句级单向统计 猫 577:0、狗 424:0），
# 方向完全失真。改用判词术语后：猫 19:431（凶）、孔雀 6:4（吉多于凶）、
# 蛇 95:69（吉多于凶）—— 与控制方裁决与古籍口径同时吻合。
# 口径说明（r4）：判词术语 + 少量**已被控制方明确认可**的实质正向词（光明/发展/兴旺
# —— 裁决一以「前途光明、事业特别有发展」作为 孔雀 的吉向证据）；不含 健康/成功/
# 顺利/幸福 这类在解读文本里饱和的泛化词（实测它们会让每个元素都变吉）。
JI_STRONG = re.compile(
    r"(大吉|吉利|吉兆|吉凶指数|主吉|大吉昌|得财|发财|进财|有财|升官|富贵|"
    r"贵子|添丁|喜事|好运|大富|大贵|得利|有喜|光明|发展|兴旺)")
XIONG_STRONG = re.compile(
    r"(大凶|不祥|凶兆|灾祸|主凶|凶事|有灾|血光|破财|疾病|病痛|死亡|丧事|"
    r"口舌|官司|离别|不顺|小人|患病|倒霉|损失)")
# 纯出处句（k58 M-2）：整句只有书名/站点名，没有解读内容 —— 例如
# 「《敦煌本梦书》」被当成 head_entry 选出来，gloss 就等于没有依据。
CITATION_ONLY_RE = re.compile(r"^[《》〈〉()（）【】\[\]\s·、，。;；:：0-9A-Za-z-]*$")
CITATION_MARK_RE = re.compile(r"[《》〈〉()（）【】\[\]\s·、，。;；:：0-9A-Za-z-]")
JUDGE_MARK_RE = re.compile(r"[主有宜忌吉凶祸福]")


# 页面小标题（无解读内容）：`1. 梦见堵车的周公解梦：`、`梦见蛇的解析：`
HEADING_RE = re.compile(
    r"^\s*[0-9０-９１-９]*\s*[.、)）]?\s*梦见?.{0,24}?(的)?"
    r"(周公解梦|解梦|解析|分析|解说|说法|含义|寓意)\s*[：:]?\s*$"
    # M-e：编号开头的页面句（`1、梦见鸭子…`）也属小标题/列表项，无解读内容
    r"|^\s*[0-9０-９１-９]+\s*[.、)）]\s*\S{0,20}$")


DANGLING_TAIL_RE = re.compile(r"(是|的|和|与|在|为|把|被|对|向|从|给|让|或|及|而|就|都|也|还)$")


# 站点模板句标记（k58 r5，Important-2）：这类句子是页面模板的**固定成分**，
# 且会被网页折行截断（「…按周易五行分析，吉祥色彩是」），选中当释义依据等于没给依据。
TEMPLATE_MARK_RE = re.compile(
    r"(周易五行|吉祥色彩|五行分析|五行属|幸运数字|吉凶指数|今日运势|开运|"
    r"请登录|版权所有|点击查看|扫码|官方微信)")


def is_fragment(sentence: str) -> bool:
    """半截句：① 站点模板句（含模板标记）；② 以虚词/系动词结尾的悬挂句。

    r5 修正（Important-2，本批新引入的回归）：原判据只覆盖「<15 字」的短悬挂句，
    挡不住被折行的**模板片段**（「梦见了奶奶，按周易五行分析，吉祥色彩是」20 字）
    → 实测 12 条 gloss 变成模板碎片（r3 是 0 条）。现在两条一起判。
    """
    s = (sentence or "").strip("，,、；;：:。 ")
    if TEMPLATE_MARK_RE.search(s):
        return True
    # 悬挂结尾：长句也认（模板片段常以「是/的/和」这类词收尾），但要求不含判词
    if DANGLING_TAIL_RE.search(s) and not JUDGE_MARK_RE.search(s):
        return len(s) < 30
    return len(s) < 15 and bool(DANGLING_TAIL_RE.search(s))


def is_heading(sentence: str) -> bool:
    return bool(HEADING_RE.match(sentence or ""))


DREAM_MARK_RE = re.compile(r"(梦见|梦到|梦|见)")


def is_citation_only(sentence: str) -> bool:
    """是否「纯出处句」：去掉书名号/标点后正文 < 6 字，且既不是梦句也不含判词字。

    r4 修正：原判据只看「正文 <6 字 + 无判词字」，把短判词句「梦见神，得财」
    （正文 5 字）也误判成空壳。补一个**梦句豁免**：只要出现「梦见/梦/见」
    就是梦句（k60 那条真违规「《敦煌本梦书》」两者都没有 → 仍被拦 ✓）。
    """
    core = CITATION_MARK_RE.sub("", sentence or "")
    if DREAM_MARK_RE.search(sentence or ""):
        return False
    return len(core) < 6 and not JUDGE_MARK_RE.search(sentence or "")


# 标题/疑问式句子 + 页面套话（都无解读内容，选中当 gloss 等于没给依据）
TITLE_JUNK_RE = re.compile(
    r"(好不好|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|怎么回事|"
    r"怎么办|有什么预兆|什么征兆|的解析|请看下面|是什么意思呢)[？?。]?$"
    r"|(希望能为网友答疑解惑|走出迷途|转载请注明|周公解梦权威解梦|由.{0,10}整理|"
    r"小编|权威解梦|免费查询|本文来源|点击查看|扫一扫|关注我们)"
    # k58 M-4：繁体页面导航串（实测「解夢」条目的 gloss 整句是站点导航）
    r"|(周公解夢|農民曆|黃曆|看相|十二星座|十二生肖|心理測試|姓名測試|血型性格|"
    r"風水知識|民俗預測|抽籤|占卜師|解夢大全|收起|星座運勢)")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


# ── 载入统计产物 ────────────────────────────────────────────────────
def load_retained_names(spec: str) -> set:
    """加载「上一版规则名」白名单（git ref:path 或本地文件）。"""
    try:
        if ":" in spec and not Path(spec).exists():
            ref, path = spec.split(":", 1)
            out = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO_ROOT,
                                 capture_output=True, text=True, check=True).stdout
        else:
            out = Path(spec).read_text(encoding="utf-8")
    except Exception as e:
        log(f"[pick] 保留白名单加载失败（{type(e).__name__}: {e}）→ 跳过保留逻辑")
        return set()
    return set(re.findall(r'"name": \'([^\']+)\'', out))


def load_stats() -> tuple:
    top = list(csv.DictReader(open(REPORTS / "element_freq_top.csv", encoding="utf-8")))
    symbols = json.loads((REPORTS / "element_symbols.json").read_text(encoding="utf-8"))
    full = {}
    for line in (REPORTS / "element_freq_full.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        el, c = line.split("\t")
        full[el] = int(c)
    return top, symbols, full


def load_site_categories() -> dict:
    """站点自有分类 → {元素: 类型}。

    数据来源（都是站点自己的分类，不是我们编的）：
    1. data/competitor_data/*.jsonl 的 `category` 字段（query → 分类）；
    2. k55 新抓的好梦网详情记录里的 `category`（页面所属栏目目录）。
    """
    out: dict = {}
    for p in sorted((Path(__file__).resolve().parents[2] / "data" / "competitor_data").glob("*.jsonl")):
        try:
            for line in p.open(encoding="utf-8"):
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                cat, q = d.get("category"), (d.get("query") or "")
                if not cat or cat not in SITE_CAT_TO_TYPE:
                    continue
                el = normalize_core(q)
                if el:
                    out.setdefault(el[:6], SITE_CAT_TO_TYPE[cat])
        except Exception:
            continue
    # k55 抓取产物（好梦网栏目分类）
    hmw = DATA_ROOT / "raw" / "haomengwang_detail.jsonl"
    if hmw.exists():
        for line in hmw.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            cat = d.get("category")
            if not cat or cat not in SITE_CAT_TO_TYPE:
                continue
            el = normalize_core(d.get("title") or "")
            if el:
                out.setdefault(el[:6], SITE_CAT_TO_TYPE[cat])
    return out


def load_classic_quotes() -> dict:
    """{元素: [公版古籍条文]}（用于「一句传统释义」）。"""
    out: dict = defaultdict(list)
    p = DATA_ROOT / "clean" / "public_domain_quotes.jsonl"
    if not p.exists():
        return out
    for line in p.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        el = (d.get("element") or "").strip()
        if el and not is_citation_only(d.get("content", "")):
            # content = 「<条文>。（《书名》·转录未校勘）」—— 出处由 gloss 统一标注，
            # 这里剥掉，免得「《敦煌本梦书》（转录未校勘）记载：…（《敦煌本梦书》·转录未校勘）」
            text = re.sub(r"。（《[^》]+》[^）]*）\s*$", "", d["content"])
            out[el].append({"text": text, "book": d.get("book", "")})
    return out


def load_ngram_map() -> dict:
    """{元素: [(n-gram, 频次)]}（从 element_freq_top.csv 的 top_ngrams 列）。

    必须走 csv.DictReader：样本列含引号内换行，按行切会解析错位。
    """
    out = {}
    with open(REPORTS / "element_freq_top.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            grams = []
            for g in (row["top_ngrams"] or "").split(","):
                m = re.match(r"(.+?)\((\d+)\)$", g)
                if m and len(m.group(1)) >= 2:
                    grams.append((m.group(1), int(m.group(2))))
            out[row["element"]] = grams
    return out


# ── 语料扫描：每元素的判词句 / 吉凶倾向 ──────────────────────────────
# ══════════ 同源句池（r6 I-3：**唯一实现**，生成器与审计脚本共用） ══════════
# 审查指出的缺陷：审计脚本（敏感性/模板族）自行实现句池，对**所有**条目都施加
# `same_scenario`，而生成器对 **head 条目（词条即该元素）不施加** → 审计池偏小，
# 于是「死亡」表内 0:10 而脚本池为空却判「稳健」，13/15 强档池偏小，
# 连 drift 对照也走同一缺陷管线 → 那 39 条「漂移」里 ≥20 条实为该缺陷。
# 现在句池、句子清理、折叠键、DF 全部收敛到下面这一处。
def dedup_corpus(entries) -> list:
    """按标题核心串去重的语料条目（唯一来源）。"""
    seen, uniq = set(), []
    for e in entries:
        core = normalize_core(e["title"])
        if core and core not in seen:
            seen.add(core)
            uniq.append(e)
    return uniq


def prepare_raw_sentences(text: str) -> list:
    """切分 + 版权尾巴/列表编号清理（唯一来源）。"""
    raw = split_sentences(text)
    raw = [re.sub(r"[\(（](©|&|版权|来源)[^)）]*[)）]", "", s).strip() for s in raw]
    raw = [re.sub(r"^\s*[0-9０-９]+\s*[.、)）]\s*", "", s).strip() for s in raw]
    return raw


def element_sentences(raw_sents, el: str, is_head: bool) -> list:
    """元素的**同源句池**单点：head 条目不施加 same_scenario，其余条目施加。"""
    out = [s for s in raw_sents if sentence_is_usable(s, el)]
    if not is_head:
        out = [s for s in out if same_scenario(s, el)]
    return out


def element_scope_pool(elements: set, entries=None) -> tuple:
    """扫全语料 → 每个元素的同源句池 + 句子文档频率（DF）。**唯一实现**。

    返回 (uniq_entries, pools)；`pools[el]`:
      · `sents` : Counter{代表原句: 合并后的出现次数}（**按折叠键去重后合并**，
                  标点变体不再各算一条 —— r6 I-1）
      · `variants` : list[原句]（**未折叠**、含重复；仅供口径对照/审计还原旧口径）
      · `head`  : Counter{代表原句: 次数}（来自「词条即该元素」的条目）
      · `df`    : {折叠键: 出现在多少个不同条目}
      · `raw`   : {折叠键: 代表原句（原样文本，逐字引文用）}
      · `occ`   : {折叠键: 出现次数}
    """
    entries = load_entries() if entries is None else entries
    uniq = dedup_corpus(entries)
    pools = {el: {"sents": Counter(), "head": Counter(), "df": defaultdict(set),
                  "raw": {}, "occ": Counter(), "head_occ": Counter(),
                  # **未折叠**的原始句序列（含标点变体、含重复）：仅供口径对照/审计
                  # 使用（r6 归因纪律：任何「改口径前后」的对比都必须在同一快照上做，
                  # 且能还原旧口径，否则会把语料漂移误报成口径效应）。
                  "variants": []} for el in elements}
    for doc_id, e in enumerate(uniq):
        core, text = normalize_core(e["title"]), e["content"]
        hits = [el for el in elements if el in core]
        if not hits:
            continue
        raw_sents = prepare_raw_sentences(text)
        for el in hits:
            is_head = (core == el)
            for s in element_sentences(raw_sents, el, is_head):
                k = SENTENCE_KEY_FOR_POOL(s)
                p = pools[el]
                if k not in p["raw"]:
                    p["raw"][k] = s              # 代表原句（首次出现的原样文本）
                p["variants"].append(s)          # 未折叠原始句（口径对照用）
                p["df"][k].add(doc_id)
                p["occ"][k] += 1
                if is_head:
                    p["head_occ"][k] += 1
    for p in pools.values():
        for k, n in p["occ"].items():
            p["sents"][p["raw"][k]] += n          # 标点变体计数合并到代表原句
        for k, n in p["head_occ"].items():
            p["head"][p["raw"][k]] += n
    return uniq, pools


# 公式句（**不得充当判词证据**，r6 裁决四）：
#   (a) 跨元素套话 —— 把元素掩成 X 后，同一模板被 ≥3 个元素使用且 DF ≥3
#       （措辞逐字相同的句子套在多个元素下 = 不是关于某个元素的判词）
#   (b) 高频转载 —— 同一句（折叠键）出现在 ≥ MIN_DF_FORMULA 个不同条目里
#       （站点批量复制的固定判词 = 单一来源被抄 N 次，不是 N 份证据）
# ⚠️ 纪律代价（控制方已裁决接受）：这也会打掉**真实但被广泛转载**的传统判词
# （实测 `猫`「见猫者，皆主不祥」×284、`狼`「见狼者，主有凶事」×37 都会被排除，
# 两条新条款强档随之消失）。该减就减，不许为了保住强档数量放宽纪律。
MIN_DF_FORMULA = 10
MIN_FORMULA_ELEMENTS = 3
MIN_FORMULA_DF = 3


# ── 古籍引文豁免（r6，控制方裁决一已批准）────────────────────────────────
# **凭什么豁免**：高频转载 ≠ 站点套话 —— 权威判词正因为权威才被到处转载。
# 拿「被转载得多」去降级权威判词，方向就反了。
# **按什么识别「古籍引文」**：句子的**折叠键**（`sentence_key()`）出现在
# `clean/public_domain_quotes.jsonl` 里 —— 该库是 1,992 条**带书名出处**的公版古籍
# 引文（敦煌本梦书 / 梦林玄解 / 断梦秘书 / 周公解梦 等，见 `load_classic_quotes()`），
# 即「可溯源到某本古籍」这一档最高证据。识别**只按文本折叠键相等**，不做模糊匹配。
# **豁免半径（实测，防止变成「高 DF 一律放行」的暗门）**：引文库贡献 1,104 个折叠键，
# 其中**只有 8 句**本来会被 (a)/(b) 判掉 → 豁免实际放行 8 句（144 vs 152），
# 逐条见报告 r6 段「8 句救回清单」：蛇「梦见蛇，主移徙事」DF180、
# 车「梦见车，必谋谈军事」DF74、「梦见车，必谋升迁事」DF69、神「梦见神，得财」DF49、
# 老人「…大吉，主寿年绵永，财帛丰盈」DF35、「…主年寿永工，财帛丰盈」DF34、
# 牛「梦见牛，所求皆得」DF32、马「梦见马，吉；乘行，大富」DF27。
# ⚠️ 已知残留误伤（**转 k60，勿在本批修**）：**不在引文库里的经典行**仍会被 (b) 排除
# （如语料里的《周公解梦》文本「见鸡，闻鸡鸣，主吉」DF 187、「梦见鸡，必有征召事」DF 82）。
# k60 在 140 万条上必须改按「**来源站点去重**」（同站内只算 1 次）或「**可否溯源到古籍**」
# 分级，而不是按条目 DF —— 否则语料越大，越权威的判词越容易被误杀。
#
# ── 边界登记（r6 裁决二：**不加严到骨架级套话**）────────────────────────
# 「骨架级」（把判词也掩成 V，如 `见X者,皆主V`）会**连 `猫` 一起打掉**，但**同样会误伤
# 骨架相同的敦煌引文**（`梦见X,主V事` ⊃ 蛇「梦见蛇，主移徙事」）—— 那是用「更准」换来
# 「误杀权威」的更大代价。当前结果是**只打掉 `狼`**（其唯一凶句「见狼者，主有凶事」DF37
# 被排除），而 `猫` 靠 3 条 **DF=1 的元素专属**凶句保住 `凶` —— 这比骨架级更准确。
# 该边界由测试 `test_mao_strong_band_rests_on_element_specific_sentences_not_exemption` 钉住。
def formula_sentence_keys(pools: dict, min_df: int = None,
                          min_elements: int = None,
                          min_df_family: int = None,
                          classic_keys: set = None) -> set:
    """公式句（站点模板/大批量转载）的**折叠键**集合 —— 计数前必须排除。

    `classic_keys` = 可溯源古籍引文的折叠键集合（**豁免**，见上）。
    """
    # r8 I-1：阈值**在调用时**从模块常量取（原来写成参数默认值 → 默认值在 def 时绑定，
    # 改常量**不改行为**、也不被测试捕获：一个改不动的旋钮 = 假旋钮）。
    min_df = MIN_DF_FORMULA if min_df is None else min_df
    min_elements = MIN_FORMULA_ELEMENTS if min_elements is None else min_elements
    min_df_family = MIN_FORMULA_DF if min_df_family is None else min_df_family
    classic_keys = classic_keys or set()
    tmpl_elems: dict = defaultdict(set)
    for el, p in pools.items():
        for k in p["raw"]:
            tmpl = k.replace(el, "X")
            tmpl_elems[tmpl].add(el)
    formula = set()
    for el, p in pools.items():
        for k, docs in p["df"].items():
            if k in classic_keys:
                continue                                      # 古籍引文豁免
            if len(docs) >= min_df:
                formula.add(k)                                # (b) 高频转载
                continue
            tmpl = k.replace(el, "X")
            if (len(tmpl_elems[tmpl]) >= min_elements
                    and len(docs) >= min_df_family):
                formula.add(k)                                # (a) 跨元素套话
    return formula


def classic_quote_keys(classics: dict = None) -> set:
    """可溯源古籍引文的**折叠键**集合（用于公式句判据豁免）。"""
    classics = load_classic_quotes() if classics is None else classics
    keys = set()
    for quotes in classics.values():
        for q in quotes:
            t = (q.get("text") or "").strip()
            if t:
                keys.add(sentence_key(t))
    return keys


def scan_corpus(elements: set) -> tuple:
    """扫语料 → (去重条目, 句池, 吉计数, 凶计数, head 句池)。

    r6 起全部走 `element_scope_pool()`（**唯一实现**，生成器与审计脚本同源）：
      · 句池与计数均按**折叠键**去重（标点变体不再各算一条 —— I-1）；
      · 方向计数先排除**公式句**（站点模板/大批量转载 —— 裁决四）再折叠计数；
      · 出现次数（ji_occ/xiong_occ）只作附加信息，不参与定档（r5 Important-1）。
    不变式（沿用 r3 I-A）：句池与计数**同源** → `fallback_no_same_scenario ⟹ ji+xiong == 0`。
    """
    uniq, pools = element_scope_pool(elements)
    formula = formula_sentence_keys(pools, classic_keys=classic_quote_keys())
    log(f"[scan] 去重语料 {len(uniq)} 条，目标元素 {len(elements)} 个；"
        f"公式句（跨元素套话/大批量转载）{len(formula)} 条（计数前排除）")

    sentences: dict = defaultdict(Counter)       # 同场景句池（与计数同源，按折叠键去重）
    head_sentences: dict = defaultdict(Counter)  # **词条就是该元素**的同场景句
    ji: Counter = Counter()
    xiong: Counter = Counter()
    ji_occ: Counter = Counter()                  # 出现次数（附加信息，不定档）
    xiong_occ: Counter = Counter()
    for el, p in pools.items():
        sentences[el] = p["sents"]
        head_sentences[el] = p["head"]
        ji_keys, xiong_keys = count_direction_sentences(
            [p["raw"][k] for k in p["raw"]], exclude_keys=formula)
        ji[el], xiong[el] = len(ji_keys), len(xiong_keys)
        ji_occ[el] = sum(n for k, n in p["occ"].items()
                         if k in ji_keys and k not in formula)
        xiong_occ[el] = sum(n for k, n in p["occ"].items()
                            if k in xiong_keys and k not in formula)
    log(f"[scan] 同场景句池覆盖 {sum(1 for p in pools.values() if p['sents'])} 个元素；"
        f"其中「词条即元素」覆盖 {sum(1 for p in pools.values() if p['head'])} 个；"
        f"唯一句定档（**折叠后**；出现次数仅附加）")
    # ⚠️ `formula` 必须由这里**返回**（r10 C-1）：展示层要用它排除公式句，而它只是本函数的
    # 局部变量 —— main 自己重新推导会产生**两份事实源**（本函数用的那份才是计数口径）。
    return uniq, sentences, ji, xiong, head_sentences, formula


# 同场景判词：句子里的元素必须是**这句梦的主角**（起式即 梦见X／梦X／见X…），
# 而不是「梦见老人出车祸」这种元素只是配角、判词谈的是别的场景的句子。
SAME_SCENARIO_PREFIXES = ("梦见", "梦到", "梦", "见")


def same_scenario(sentence: str, el: str) -> bool:
    """元素必须是这句梦的**主角且不被复合词吞掉**。

    r2 加严（第一版仍不够）：只要求「句子以梦见+元素开头」会把
    「梦见**开车撞人**…」当成「开车」的同场景判词、把「梦见**水泥**…」
    当成「水」的（实测两例都出现了）。
    现在要求元素后面紧跟**分隔符或常见虚词**——即元素在该句里是完整的词，
    而不是更长复合词/别的场景的一部分（开车撞人 → 归「开车撞人」那个场景，
    不归「开车」）。
    """
    s = sentence.strip()
    tail = r"(?:了|着|过|的|者|时|后|之)?"
    delim = r"[，,。：:；;！？!?、\s]|$"
    for p in SAME_SCENARIO_PREFIXES:
        if re.match(rf"^{p}{re.escape(el)}{tail}(?:{delim})", s):
            return True
    return False


# 一致性检查用的**更宽极性词表**（r3 I-B 加严）：
# 计数口径（JI_RE/XIONG_RE）只认判词术语，漏掉了「不很顺利」「慎防小人」这类
# 表述 —— 实测「异性」luck=大吉 却配「代表**不很顺利**」（否定式，JI_RE 里的
# 「顺利」被反向使用）。否定前缀 + 明确负面词一并纳入，只用于**一致性判定**。
NEG_PREFIX_RE = re.compile(r"(不|没|未|难以|无法|避免|别|勿)\s*(很|太|会|能|要|可)?\s*$")
# r5 Important-4 追加：上面的词表只挡得住「不凶 / 不很顺利」这类**紧贴**否定。
# 实测漏判：「梦见蛇，**不一定是**凶兆，别自己吓自己」——「不」与「凶兆」之间
# 隔着「一定是」三个字，窗口（原为 3）不够 → 判成凶。改为 6 字窗口 + 允许
# 中间夹 1~4 个非标点汉字（「不一定是/不见得/不至于/不能说」都覆盖）。
NEG_GAP_RE = re.compile(r"(不|没|未|难以|无法|避免|别|勿)[^，,。；;！？!?、\s]{0,4}\s*$")


def negated_before(s: str, pos: int, window: int = 6) -> bool:
    """pos 处的吉/凶词是否被**前置否定**（「不一定是凶兆」「无法顺利」）。"""
    prefix = s[max(0, pos - window):pos]
    return bool(NEG_PREFIX_RE.search(prefix) or NEG_GAP_RE.search(prefix))
EXTRA_NEG_RE = re.compile(r"(损失|不利|不顺|慎防|小心|谨慎|警惕|防范|挫折|失败|"
                          r"纠纷|忧伤|烦恼|灾|病痛|愁|破坏|障碍|阻碍|是非|口舌)")


def sentence_polarity(s: str, extended: bool = True) -> str:
    """句子的吉凶极性：吉/凶/空（同时含两向或都不含 → 空）。

    extended=True（默认，用于一致性校验）：识别否定式与更多负面表述。
    """
    pos = False
    negated_pos = False
    for m in JI_RE.finditer(s):
        if negated_before(s, m.start()):
            negated_pos = True          # 「不很顺利」= 反向使用吉词 → 计入负面证据
        else:
            pos = True
    # 反向否定：被否定的凶词**不**计为凶（「不一定是凶兆」「不凶」）。
    # r5：原来是「任意一处被否定就整句清空 neg」，会把同句另一处真实负面一起抹掉；
    # 现在逐条判定（有真实负面仍计凶）。
    neg = negated_pos
    for m in XIONG_RE.finditer(s):
        if negated_before(s, m.start()):
            continue
        neg = True
        break
    if extended:
        neg = neg or bool(EXTRA_NEG_RE.search(s))
    if pos and neg:
        # k58 r3（A-1）：双向混合时**看末句**——审查口径以「句末是否转向正面」定调
        # （「…不得不低价出售，依然可能成功」判为吉向）。末句仍混合/看不出 → 无极性。
        sents = [x for x in re.split(r"[。！？!?\n]", s or "") if x.strip()]
        last = sents[-1] if sents else ""
        tail_pos = bool(JI_RE.search(last))
        tail_neg = bool(XIONG_RE.search(last))
        # Important-4a：末句里的**否定式吉词**（「无法顺利发展」）要算负面，
        # 否则「大吉」会配上一句「可惜…无法顺利发展」（否定式盲区，实测 小男孩）
        for m in JI_RE.finditer(last):
            if negated_before(last, m.start()):
                tail_pos = False
                tail_neg = True
                break
        if tail_pos and not tail_neg:
            return "吉"
        if tail_neg and not tail_pos:
            return "凶"
        return ""
    return "吉" if pos else ("凶" if neg else "")


def luck_polarity(luck: str) -> str:
    if luck in ("大吉", "吉", "吉多于凶"):
        return "吉"
    if luck in ("凶", "凶多于吉"):
        return "凶"
    return ""


def same_scenario_gloss(el: str, sentences: dict, classics: dict,
                        head_sentences: dict = None,
                        want_luck: str = "",
                        exclude_keys: set = None) -> tuple:
    """取**同场景**释义依据；取不到则诚实兜底。

    证据档次（从强到弱，写进 gloss_evidence 字段，可复核）：
    1. `classic_quote`          —— 该元素的古籍引文（敦煌本梦书/梦林玄解/断梦秘书…）
    2. `head_entry`             —— 语料里**词条就是该元素**（「梦见X」）的条目判词句
    3. `corpus_same_scenario`   —— 句子的主角是该元素（梦见X…）的语料判词句
    4. `fallback_no_same_scenario` —— 都没有：明说是泛化倾向，不做吉凶判断

    r2 I-2：旧实现直接取「含该元素」的最高频句，会拿别的场景顶
    （车祸 → 「梦见老人出车祸…虽有财运可得」；水 → 「梦见水泥…」），
    并把 luck 带偏。
    r3 I-B：再加**极性一致性**——优先取与聚合 luck 同向的句子，避免
    「传统倾向=吉多于凶」与「释义依据=…是凶兆」同时出现在一行里打对台。

    r9（控制方裁决④）：`exclude_keys` = **公式句**（站点模板/大批量转载）的折叠键集合。
    我们已裁定「模板句不构成关于该元素的**证据**」，那就不该把它当**释义依据**再展示一次
    （换个出口把同一个错误印象再给一次）。**优先取非公式句**；只有在池里**确实没有**
    非公式句时才退化，并在 `gloss_evidence` 上标注 `…_formula_only` 档次，
    免得用户以为它是元素专属依据。**计数口径不受影响**（展示与计数继续分离）。
    """
    want_luck_pol = luck_polarity(want_luck)
    def pick(pool, kind, tag):
        """从池里挑与 want_luck **同向**的句子；无同向则退无极性，再退反向。"""
        ranked = {"same": [], "none": [], "opp": []}
        for sent, _ in pool.most_common(40):
            pol = sentence_polarity(sent)
            if not want_luck_pol:
                # 无方向档（中性/提醒类）：**优先无极性句**，有极性的放最后
                ranked["none" if not pol else "opp"].append((sent, kind, tag))
            elif not pol:
                ranked["none"].append((sent, kind, tag))
            elif pol == want_luck_pol:
                ranked["same"].append((sent, kind, tag))
            else:
                ranked["opp"].append((sent, kind, tag))
        for key in ("same", "none", "opp"):
            if ranked[key]:
                # 同档内优先「有内容的句子」：含判词字 / 长度 ≥10 的排在前面，
                # 避免选到「梦见自己迷路」这种没有解读信息的短句
                ranked[key].sort(key=lambda x: (0 if JUDGE_MARK_RE.search(x[0])
                                                else (1 if len(x[0]) >= 10 else 2),
                                                -len(x[0])))
                sent, k, t = ranked[key][0]
                sent = sent.strip("，,、；;：:。 ")
                return f"{sent}（{t}）", k
        return None

    for q in classics.get(el, [])[:1]:
        # C-1 修法（r5 收窄）：古籍引文只在**判出反向**时跳过；
        # 判不出极性（""）**不该丢**（Important-3：无极性 ≠ 反向）——
        # `蛇`「蛇主移徙事」、`牛`「所求皆得」这类权威引文此前被误丢，
        # 导致 classic_quote 7→5、`蛇` 的依据降级成现代惊悚句。
        # 安全性由 C-1 的词表修复保证（裸「吉」已能识别 → 真反向会被判出来）。
        qpol = sentence_polarity(q["text"])
        if want_luck_pol and qpol and qpol != want_luck_pol:
            continue
        return f"《{q['book']}》（转录未校勘）记载：{q['text']}", "classic_quote"
    # 两个池**合并后统一定向挑选**（r4 修）：先 head 池再同场景池，逐个过 pick；
    # pick 内部按「同向 > 无极性 > 反向」排。这样当 head 池只有反向句、而同场景池
    # 有同向句时，能取到同向句（`猫`：head 是吉向句、同场景池有「见猫者，皆主不祥」
    # 这类凶向句 —— 分别挑池会先返回吉向的 head 句，用户看到的就是反向）。
    def _no_formula(counter: Counter) -> Counter:
        if not exclude_keys:
            return counter
        return Counter({k: v for k, v in counter.items()
                        if SENTENCE_KEY_FOR_POOL(k) not in exclude_keys})

    head_pool = _no_formula((head_sentences or {}).get(el, Counter()))
    same_pool = _no_formula(sentences.get(el, Counter()))
    # 退化判定：过滤后**两池都空**，但未过滤时有候选 → 只能用公式句，标注档次
    degraded = (exclude_keys
                and not head_pool and not same_pool
                and (bool((head_sentences or {}).get(el)) or bool(sentences.get(el))))
    if degraded:
        head_pool = (head_sentences or {}).get(el, Counter())
        same_pool = sentences.get(el, Counter())
    candidates = []
    for pool, kind, tag in ((head_pool, "head_entry", f"语料「梦见{el}」词条原文"),
                            (same_pool, "corpus_same_scenario", "语料同场景判词")):
        got = pick(pool, kind, tag)
        if got:
            candidates.append(got)
    if not candidates:
        return ("", "fallback_no_same_scenario")
    if want_luck_pol:
        for gloss, kind in candidates:
            if sentence_polarity(gloss) == want_luck_pol:
                return gloss, (kind + "_formula_only" if degraded else kind)
    gloss, kind = candidates[0]
    return gloss, (kind + "_formula_only" if degraded else kind)


# 吉凶语义字：fallback（无同场景依据）的规则不得带这类「象征词」——
# 否则「车祸」的核心象征里会出现「吉祥」（来自别的场景的共现），
# 与「不做吉凶判断」自相矛盾（r2 I-2 同类问题）。
LUCK_SEMANTIC_RE = re.compile(r"[吉凶祥瑞福禄寿财喜祸灾煞克破败亡死病]")
# 极性语义字（用于「象征 vs 倾向」一致性，Important-4b）
POS_SEMANTIC_RE = re.compile(r"(吉|祥|瑞|福|禄|寿|喜|财|富|贵|顺|旺)")
NEG_SEMANTIC_RE = re.compile(r"(凶|祸|灾|煞|克|破|败|亡|死|病|厄|难)")


def drop_luck_semantic(symbols: list) -> list:
    return [s for s in symbols if not LUCK_SEMANTIC_RE.search(s)]


def mandatory_gloss(el: str, sentences: dict, classics: dict, head_sentences: dict = None,
                    reason: str = "现实驾驶/事故类梦境是现代新增题材，"
                                  "古典梦书成书时无此类") -> tuple:
    """强制族（交通/事故/时间压力）的释义依据：**只认同场景**，否则诚实兜底。

    r2 I-2 实测错误：旧实现给「车祸」取到的句子是「梦见老人出车祸，得此梦，
    虽有财运可得…」——含「车祸」但讲的是别人的场景，被当成了车祸梦的判词。
    """
    g, ev = same_scenario_gloss(el, sentences, classics, head_sentences)
    if g:
        return g, ev
    return ("本批语料里没有匹配到与「" + el + "」同场景的吉凶判词句"
            f"（{reason}；判定口径见 build_rules.same_scenario）；"
            "本条不做吉凶判断，只作情境提醒，释义来自该情境的普遍心理描述",
            "fallback_no_same_scenario")


# 强档（大吉/吉/凶）所需的**最少判词句数**：小样本只给温和档
MIN_SAMPLE_FOR_STRONG = 5
# 强档允许的**反向句上限**（近似一致才给「大吉/吉/凶」；否则只给温和档）
MAX_MINORITY_FOR_STRONG = 2
# r5 追加：**一致无死角**条款 —— 反向唯一句为 0 时，唯一句 ≥3 即可给强档。
# 依据：控制方在 r4 认可「大蛇 6:1（有 1 条反例）→ 大吉」，而「0 条反例」在证据上
# 严格强于「1 条反例」；r5 的「单句重复 26 次不得强档」由唯一句计数本身挡住
# （重复句折叠成 1 条 → 达不到 3）。此条款只对**零反例**生效，是加严而非放松：
# 有任何一条同向反例，仍须走 MIN_SAMPLE_FOR_STRONG=5 + MAX_MINORITY=2 的老路。
MIN_UNANIMOUS_FOR_STRONG = 3

# 比值档阈（r9 从字面量提为常量，**调用时读取**）：吉向占比 r = n_ji / tot
RATIO_BIG = 0.8        # r ≥ 0.8 → 「大吉」（仍需 tot≥5 且零反例），否则「吉多于凶」
RATIO_JI = 0.62        # r ≥ 0.62 → 「吉」（强档）否则「吉多于凶」
RATIO_JI_MILD = 0.42   # r ≥ 0.42 → 「吉多于凶」
RATIO_XIONG = 0.25     # r ≥ 0.25 → 「凶多于吉」；r < 0.25 → 「凶」（强档）否则「凶多于吉」


def luck_from_counts(n_ji: int, n_xiong: int) -> str:
    """真实语料吉/凶判词计数 → 吉凶基线（与既有 DREAM_LUCK_RANK 词表一致）。

    r4 两条修正（控制方裁决）：
    - **无证据不给方向**（I-2）：tot==0 一律「中性」，不再默认「吉多于凶」
      （旧行为会把「零判词证据」表述成一个有方向的档位，且 luck_basis 谎称词频依据）；
    - **小样本不给强档**（裁决一②）：判词句数 < MIN_SAMPLE_FOR_STRONG 时，
      只给温和档（吉多于凶/凶多于吉）—— 例如 `孔雀` counts 3:1 只有 4 句，
      句级真实优势 57%（审查逐句判读 35:26），给「吉」是高估。
    """
    tot = n_ji + n_xiong
    if tot == 0:
        return "中性"
    if n_ji == n_xiong:
        # 平票（含 1:1 这类小样本平票）：方向信息为零，不得表述成有方向的档位
        # （审查指出「购买 1:1 被判吉多于凶」是 luck_from_counts 的既有语义偏差）
        return "中性"
    # r5：小样本**近似平票**（差 ≤1 且唯一句 <10）同样不判方向 ——
    # 控制方裁决：「蛇 唯一句 吉4:凶4 平票 → 中性」；推广到 3:4/2:3 这类
    # 差一票的小样本，避免把一个 0.43 的比例说成「吉多于凶」。
    if abs(n_ji - n_xiong) <= 1 and tot < 10:
        return "中性"
    r = n_ji / tot
    # 比值档阈（r9）：**模块常量、调用时读取** —— 与 r8 的教训一致：写成字面量或
    # 参数默认值时，「调阈值」是假旋钮（改不动、也测不出）。夹具对这四个阈值
    # 各有两侧语料级探针（见 tests/test_k58_fixture_pipeline.py）。
    # 强档条件（r4）：样本足够 **且** 反向句 ≤2（近似一致）。
    # 依据：控制方裁决一（孔雀 12:4 只给温和档——4 条反例不能被忽略）与
    # 已认可的 大蛇 6:1→大吉（仅 1 条反例）——两者比例相近，差别在**反例条数**。
    minority = min(n_ji, n_xiong)
    strong = (tot >= MIN_SAMPLE_FOR_STRONG and minority <= MAX_MINORITY_FOR_STRONG) or \
             (minority == 0 and tot >= MIN_UNANIMOUS_FOR_STRONG)
    if r >= RATIO_BIG:
        # 「大吉」是**极端**断言：零反例条款不放宽它 —— 仍需唯一句 ≥5 且零反例
        return "大吉" if (tot >= MIN_SAMPLE_FOR_STRONG and minority == 0) else "吉多于凶"
    if r >= RATIO_JI:
        return "吉" if strong else "吉多于凶"
    if r >= RATIO_JI_MILD:
        return "吉多于凶"
    if r >= RATIO_XIONG:
        return "凶多于吉"
    return "凶" if strong else "凶多于吉"


def tone_from(n_ji: int, n_xiong: int, symbols: list, luck: str = "") -> str:
    """情绪基调：由语料吉凶倾向 + 象征词合成（数据导出，非人工撰写）。

    r3 I-B：luck 为无方向档时不带吉凶形容词（否则「中性」结论会配一句
    「偏吉、期待与安抚」的基调，同一行里自相矛盾）。
    """
    if luck in ("中性", "提醒类", "fallback_no_same_scenario"):
        focus = "、".join(symbols[:2])
        return f"情境关注（语料关注点：{focus}）" if focus else "情境关注"
    tot = n_ji + n_xiong
    r = (n_ji / tot) if tot else 0.55
    if r >= 0.62:
        base = "偏吉、期待与安抚"
    elif r >= 0.42:
        base = "吉凶掺半、观望"
    elif r >= 0.25:
        base = "偏凶、警惕与压力"
    else:
        base = "警示、焦虑与不安"
    focus = "、".join(symbols[:2])
    return f"{base}（语料关注点：{focus}）" if focus else base


def assemble_rule_fields(el: str, cov: int, n_ji: int, n_xiong: int, luck: str,
                         gloss: str, ev_kind: str,
                         ptype: str, syms: list) -> dict:
    """规则表**组装层**单点（r8 I-2）：gloss/luck 一致性补丁 + 象征/类型一致性。

    生成器 `main()` 与冻结夹具 `freeze_fixture.run_pipeline()` **共用这一处** ——
    否则夹具只锁到「组装前」的 gloss，而**产物 gloss 的最终形态产自这里**：
    实测夹具里 `马` 的 gloss 不带「不代表吉凶」、产物表却带 ⇒ 夹具的 gloss 列比产物
    低一层，三类补丁（y1 限定语 / y2 反向降级 / y3 兜底文案）的改动夹具**全绿**。
    返回 `{luck, gloss, ev_kind, luck_basis, ptype, syms, tone}`（tone 用**最终**
    luck/syms 计算）。
    """
    luck_basis = ("语料同场景判词词频" if (n_ji + n_xiong) > 0
                  else "无同场景判词证据（ji+xiong=0）→ 中性")
    if ev_kind == "fallback_no_same_scenario":
        # 无同场景证据（不变式：此分支 ji+xiong 必为 0）→ 不给方向
        luck = "中性"
        luck_basis = "无同场景判词证据（ji+xiong=0）→ 中性"
        gloss = (f"本批语料里没有匹配到与「{el}」同场景的吉凶判词句"
                 f"（含该元素的条目 {cov} 条，但判词句谈的是其他场景/复合情境）；"
                 f"此处只给泛化倾向，不构成吉凶判断")
    else:
        # 一致性校验（r4 改口径）：**不再因为「没有同向句」就把方向丢掉**
        # （控制方裁决二：有强向证据弃权＝把已知结论扔掉，比如 `猫` 270:35）。
        # 新口径：同向句 > 无极性句 > 「只有反向句」——最后一种情况下
        # **保留方向**，但**不引那句反向句当依据**，换成如实说明（引用聚合词频）。
        gp, lp = sentence_polarity(gloss), luck_polarity(luck)
        if gp and lp and gp != lp:
            gloss = (f"语料中与「{el}」相关的判词句多为反向语气，未选作依据；"
                     f"本条方向按聚合判词词频给出（吉向 {n_ji} 句 / 凶向 "
                     f"{n_xiong} 句），不引单句")
            ev_kind = "aggregate_only_reverse_gloss"
            luck_basis = (f"聚合词频（吉 {n_ji} / 凶 {n_xiong}）；"
                          f"无同向单句可引，故不引依据句")
            gp = ""
        if luck in ("中性", "提醒类") and gp:
            # M-3：无方向档**确无**无极性句时，加一句传统说法限定（与 k55-r3
            # prompt 口径一致），而不是把吉/凶引文当成判断端出来。
            # 顺序很重要：必须在「反向降级」**之后**判 —— 否则降级成中性的
            # 条目会漏掉限定语（实测漏了「红色/墙」两条）。
            if "（语料" in gloss:
                gloss = gloss.replace("（语料", "（传统说法，不代表吉凶；语料")
            elif "不代表吉凶" not in gloss:
                # r5：**古籍引文**结尾是「记载：…」没有「（语料」标记 →
                # 原来的 replace 是空操作（实测 `马` 中性 却端出
                # 「梦见马，吉；乘行，大富」——同行自相矛盾）。此处补上。
                gloss = gloss + "（传统说法，不代表吉凶）"
    # Important-4b（r5）：核心象征不得与 luck 极性相反
    # （`蛇`=凶多于吉 却列着「吉利/吉兆」、`龟`=凶 却列「财富」——同行自相矛盾）
    if luck_polarity(luck) == "凶":
        syms = [x for x in syms if not POS_SEMANTIC_RE.search(x)] or syms
    elif luck_polarity(luck) == "吉":
        syms = [x for x in syms if not NEG_SEMANTIC_RE.search(x)] or syms
    # type 与 luck 不许并存矛盾（刀：type=警示类 + luck=中性 那种）
    if luck in ("中性", "提醒类") and ptype in ("吉兆类", "警示类"):
        ptype = "中性类"
    return {"luck": luck, "gloss": gloss, "ev_kind": ev_kind, "luck_basis": luck_basis,
            "ptype": ptype, "syms": syms,
            "tone": tone_from(n_ji, n_xiong, syms, luck)}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成解梦规则层（80+ 条，语料统计得出）")
    ap.add_argument("--top", type=int, default=400)
    ap.add_argument("--min-coverage", type=int, default=60)
    ap.add_argument("--min-standalone", type=int, default=5,
                    help="单字元素在语料里独立成词的最低条目数（词缀碎片判据）")
    ap.add_argument("--min-coverage-for-retain", type=int, default=20,
                    help="保留白名单元素的最低语料覆盖量（有证据就不许丢）")
    ap.add_argument("--retain-from", default="55ea3f7:src/engines/dream_rules.py",
                    help="上一版规则模块（git ref:path 或本地文件），用于覆盖非回归")
    args = ap.parse_args()

    top, symbols_map, full = load_stats()
    site_cat = load_site_categories()
    classics = load_classic_quotes()
    ngrams = load_ngram_map()
    log(f"[load] Top 表 {len(top)} 行；站点分类映射 {len(site_cat)} 元素；"
        f"古籍引文 {len(classics)} 元素")

    # 候选：覆盖量达标 ∪ **保留白名单**（上一版规则表里出现过的元素）。
    # k58 r2（I-1）：上一版把候选池从 531 顶到 1020 后，Top-N 截断 + 阈值提高
    # **误删了 89 条规则**（月亮/镜子/钱包/大海/钥匙/苹果…覆盖量 42–76，
    # 语料里明明有证据）。现在：曾在规则表出现过的元素，只要覆盖量达统计下限
    # （--min-coverage-for-retain，默认 20）就**一律保留**，不受 Top-N 截断影响。
    # r2 I-4：初版只过滤 NARRATIVE_STOP，漏了 META_STOPWORDS（工作/表示/生活/
    # 说明/关系/可能/方面/象征/女性/运势/心理/梦者…）——那些词在注释里就写明
    # 「是语料体裁的产物，不是梦的象征」，却混进了规则表，被写进给 LLM 的 notes。
    # 现在两张表合并生效，并把剔除清单落盘（可复核，不是静默丢弃）。
    retained = load_retained_names(args.retain_from)
    log(f"[pick] 保留白名单（上一版规则名）：{len(retained)} 个，其中语料覆盖 "
        f"≥{args.min_coverage_for_retain} 的有 "
        f"{sum(1 for n in retained if full.get(n, 0) >= args.min_coverage_for_retain)} 个")
    excluded = []
    cands, cand_set = [], set()
    # ① 覆盖量达标的候选（全量 TSV 里取，不再受 Top-N 表 300 行限制）
    for el, cov in sorted(full.items(), key=lambda kv: (-kv[1], kv[0])):
        if not el or cov < args.min_coverage:
            continue
        if el in RULE_STOPWORDS:
            excluded.append({"element": el, "coverage": cov,
                             "reason": "narrative_or_meta_word"})
            continue
        if not pos_allowed(el):
            excluded.append({"element": el, "coverage": cov, "reason": "pos_not_noun_like"})
            continue
        cands.append(el)
    cands = cands[:args.top]
    cand_set = set(cands)
    # ② 保留白名单：不受 Top-N 截断，只要求有语料证据
    kept_back = []
    for el in retained:
        if el in cand_set or el in RULE_STOPWORDS:
            continue
        cov = full.get(el, 0)
        if cov < args.min_coverage_for_retain:
            excluded.append({"element": el, "coverage": cov,
                             "reason": "retained_but_no_corpus_evidence"})
            continue
        if not pos_allowed(el):
            excluded.append({"element": el, "coverage": cov, "reason": "pos_not_noun_like"})
            continue
        kept_back.append(el)
    if kept_back:
        log(f"[pick] 保留白名单补回 {len(kept_back)} 个元素（Top-N 截断外）")
        cands.extend(sorted(kept_back, key=lambda e: (-full.get(e, 0), e)))
    (REPORTS / "rule_exclusions.json").write_text(
        json.dumps({"generated_at": now_iso(),
                    "stopwords": sorted(RULE_STOPWORDS),
                    "excluded_from_rules": excluded}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"[pick] 语料 Top 候选 {len(cands)} 个（覆盖 ≥{args.min_coverage}）；"
        f"按元词/叙述词剔除 {len(excluded)} 个（清单见 rule_exclusions.json）")

    elements = set(cands) | set(MANDATORY_DRIVING)
    # k58 M-1：单字元素的搭配/护栏统计（一次扫语料，之后复用）
    colloc_stats = colloc_mod.corpus_stats(set(elements))
    # 已登记的规则名（含强制族）：单字元素的搭配词不得与它们重名，
    # 否则同一段文本被两条规则重复命中（见 collocation.collocations 注释）
    rule_names = set(elements) | set(MANDATORY_EXTRA)
    uniq, sentences, ji, xiong, head_sentences, formula = scan_corpus(elements)

    # 交通驾驶族的覆盖量：在**真实语料**里现算（含新增爬取的真实网友梦境文本）
    real_text_hits: Counter = Counter()
    for e in uniq:
        blob = (e["title"] or "") + (e.get("content") or "")[:200]
        for name, spec in MANDATORY_DRIVING.items():
            if any(m in blob for m in spec["match"]):
                real_text_hits[name] += 1

    rules = []
    for el in cands:
        cov = full.get(el, 0)
        syms = [s for s in symbols_map.get(el, []) if s != el][:6]
        if not syms:
            syms = ["情境变化", "现实压力"]
        ptype = site_cat.get(el) or site_cat.get(el[:2])
        if not ptype:
            r = (ji[el] / (ji[el] + xiong[el])) if (ji[el] + xiong[el]) else 0.5
            ptype = "吉兆类" if r >= 0.62 else ("警示类" if r < 0.35 else "中性类")
        # match 正则：≥2 字分支（k49）。
        # 多字元素：沿用 n-gram 里 ≥3 次、长度 2~3 的搭配（4 字 n-gram 常跨越短语
        # 边界，实测会产出「天开车」「友开车」这类分词碎片）。
        # **单字元素（k58 M-1）**：改用 collocation.py 的语料统计产物 ——
        # 触发形态（梦见X/梦到X/梦见了X，实测覆盖量）+ 含 X 的 2~3 字搭配（Top-N，
        # 按覆盖条数排序）+ **边界护栏**（词典/语料推导，防「梦见水杯」这类前缀误伤）。
        match_evidence, guard_evidence, match_str = [], {}, ""
        if len(el) == 1:
            # I-3 数据判据：单字元素必须在语料里**独立成词**（jieba 分词下 X 是
            # 独立 token）达到门限 —— 这是「具体意象」与「词缀碎片」的分界：
            # 菜/鞋/肉/车 独立成词（买菜/鞋/肉/车），面/身/子/公/母 只作为
            # 面条/身上/面子/老公/母亲 的一部分出现（实测独立成词数≈0）。
            if colloc_stats["standalone"].get(el, 0) < args.min_standalone:
                excluded.append({"element": el, "coverage": full.get(el, 0),
                                 "reason": "no_bare_form_entry"})
                continue
            built = colloc_mod.build_match(el, colloc_stats, top_colloc=5,
                                           exclude_names=rule_names)
            alts = [b["branch"] for b in built["branches"] if b["coverage"] > 0]
            if not alts:                      # 语料零证据 → 不放裸单字分支（k49）
                continue
            match_evidence = built["branches"]
            guard_evidence = built["guard"]
            # 分支本身就是正则（head_form 带护栏 lookahead）→ **不能再 escape**，
            # 否则 (?![…]) 会变成字面量、分支永远匹配不上（实测踩过）
            match_str = "|".join(alts)
        else:
            # m-4：多字元素的名字分支也加**边界护栏**（「梦见狗尾巴草」不该命中 尾巴
            # —— 它是更长复合词的一部分）。n-gram 搭配分支不加（它们是短语碎片，
            # 加了会误伤正当形态）。
            alts = [el + f"(?={colloc_mod.TRAILING_BOUNDARY})"]
            for g, c in sorted(ngrams.get(el, []), key=lambda x: (-x[1], x[0])):
                if c < 3 or not (2 <= len(g) <= 3) or g == el or g in alts:
                    continue
                alts.append(g)
                if len(alts) >= 5:
                    break
        if not alts:
            continue
        # I-3：排序**必须以内容为键**（不能只按长度——长度相同的分支顺序会随
        # set 迭代序/PYTHONHASHSEED 变化，实测 156/396 条规则的 match 分支顺序漂移，
        # 而 re.finditer 是最左优先，分支顺序变化会改变命中 span → 打分与去重结果漂移）
        alts = sorted(set(alts), key=lambda x: (-len(x), x))[:8]
        if not match_str:
            # 含护栏的正则分支原样保留，其余（字面 n-gram）转义
            match_str = "|".join(a if "(?=" in a else re.escape(a) for a in alts)
        # 聚合词频给出的方向 → 用它作为「想要的方向」去挑同向句（r3 I-B）
        luck = luck_from_counts(ji[el], xiong[el])
        gloss, ev_kind = same_scenario_gloss(el, sentences, classics,
                                             head_sentences, want_luck=luck,
                                             exclude_keys=formula)
        # 组装层（**单点**：main 与冻结夹具共用，见 assemble_rule_fields 注释）
        _f = assemble_rule_fields(el, cov, ji[el], xiong[el], luck, gloss, ev_kind,
                                  ptype, syms)
        luck, gloss, ev_kind = _f["luck"], _f["gloss"], _f["ev_kind"]
        luck_basis, ptype, syms = _f["luck_basis"], _f["ptype"], _f["syms"]
        rules.append({
            "name": el, "match": match_str,
            "type": ptype, "symbols": syms,
            "tone": _f["tone"],
            "luck": luck, "luck_basis": luck_basis,
            "gloss": gloss, "coverage": cov,
            # M-3：覆盖量口径必须写进数据（两种口径不能混着看）
            "coverage_basis": "标题核心串含该元素的去重语料条数",
            # I-4 不变式用：该元素自己的裸条目数（「梦见X」词条）
            "head_entry_count": colloc_stats["head"].get(el, 0),
            "standalone_count": colloc_stats["standalone"].get(el, 0),
            "gloss_evidence": ev_kind,
            "symbols_basis": "corpus_cooccurrence",
            "match_branches": match_evidence,
            "match_guard": guard_evidence,
            "evidence": {"ji": ji[el], "xiong": xiong[el],
                         "sentences": len(sentences[el])},
            "source": "corpus_stats",
        })

    # 强制族（brief 明令必须覆盖）：**无论语料是否已生成同名条目，都以 brief
    # 口径的 match/type/tone 覆盖**——语料统计版会掺入分词碎片（实测「开车」拿到
    # 「朋友开车/天开车/友开车」这类 n-gram），而这一族是控制方实测梦例所在族，
    # 匹配面必须干净、可预期。覆盖量仍取真实统计值。
    by_name = {r["name"]: r for r in rules}
    for family, table in (("mandatory_driving_family", MANDATORY_DRIVING),
                          ("mandatory_time_pressure", MANDATORY_EXTRA)):
        for name, spec in table.items():
            if name in by_name:
                r = by_name[name]
                r["match"] = "|".join(re.escape(a) for a in spec["match"])
                r["type"] = spec["type"]
                r["tone"] = spec["tone"]
                # r2 I-2：吉凶**由 brief 口径指定**（交通/事故族＝中性或提醒类），
                # 不再由语料词频自动推——那些判词讲的是别的场景。
                r["luck"] = spec["luck"]
                r["coverage"] = max(r["coverage"], real_text_hits.get(name, 0))
                r["source"] = f"corpus_stats+{family}"
                r["coverage_basis"] = "标题核心串含该元素的去重语料条数（与真实梦境正文命中数取较大）"
                r["symbols_basis"] = "corpus_cooccurrence" if symbols_map.get(name) else "family_tone_derived"
                r["evidence"]["real_dream_text_hits"] = real_text_hits.get(name, 0)
                g, ev = mandatory_gloss(
                    name, sentences, classics, head_sentences,
                    "现实驾驶/事故类梦境是现代新增题材，古典梦书成书时无此类"
                    if family == "mandatory_driving_family"
                    else "时间压力类梦境没有对应的古典判词")
                r["gloss"], r["gloss_evidence"] = g, ev
                # M-3（r3 补齐）：强制族同样受「无方向档不得配极性引文」约束 ——
                # 词表扩充后「健康」等词会出极性，这里补上限定语（原先只覆盖通用路径）
                if r["luck"] in ("中性", "提醒类") and sentence_polarity(g):
                    r["gloss"] = g.replace("（语料", "（传统说法，不代表吉凶；语料")
                r["luck_basis"] = "brief 指定（交通/事故/时间压力族不做吉凶推断）"
                # 该族既然不做吉凶推断，核心象征里就不该出现吉凶语义词
                # （车祸原本带着「吉祥、财运」，与「中性」自相矛盾）
                r["symbols"] = drop_luck_semantic(r["symbols"]) or r["symbols"]
                continue
            cov = real_text_hits.get(name, 0)
            syms = [s for s in symbols_map.get(name, []) if s != name][:6]
            if not syms:
                if family == "mandatory_driving_family":
                    syms = [s for s in symbols_map.get("车", []) if s != "车"][:6]
                else:
                    # 时间压力族在语料里没有共现象征可用：直接由该族 tone 派生
                    # （tone 是 brief 口径给的），并标注 basis，避免读者误以为是统计值
                    syms = [w for w in re.split(r"[：与、]", spec["tone"]) if w][1:]
            gloss, ev_kind = mandatory_gloss(
                name, sentences, classics, head_sentences,
                "现实驾驶/事故类梦境是现代新增题材，古典梦书成书时无此类"
                if family == "mandatory_driving_family"
                else "时间压力类梦境没有对应的古典判词")
            syms = drop_luck_semantic(syms) or syms
            rules.append({
                "name": name, "match": "|".join(re.escape(a) for a in spec["match"]),
                "type": spec["type"], "symbols": syms, "tone": spec["tone"],
                "luck": spec["luck"],
                "luck_basis": "brief 指定（交通/事故/时间压力族不做吉凶推断）",
                "gloss": gloss, "coverage": cov,
                "coverage_basis": "真实梦境正文命中条数（该族在词典式语料里覆盖极低）",
                "head_entry_count": colloc_stats["head"].get(name, 0),
                "symbols_basis": "corpus_cooccurrence" if symbols_map.get(name) else "family_tone_derived",
                "gloss_evidence": ev_kind,
                # r7 Minor：family-only 规则的 `counts` 曾缺 `sentences` 键（schema 不一致）。
                # 在**生成器**补齐 —— 受审的规则表已冻结，不在产物上改；下次重生成自动带上。
                "evidence": {"ji": ji.get(name, 0), "xiong": xiong.get(name, 0),
                             "sentences": len(sentences.get(name, ())),
                             "real_dream_text_hits": cov},
                "source": f"corpus_stats+{family}",
            })

    # 去重（按 name）+ 排序（覆盖量降序，强制族置前）
    seen, uniq_rules = set(), []
    for r in rules:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        uniq_rules.append(r)
    uniq_rules.sort(key=lambda r: (r["name"] not in MANDATORY_DRIVING, -r["coverage"]))
    log(f"[rules] 生成规则 {len(uniq_rules)} 条（其中强制交通族 {len(MANDATORY_DRIVING)} 条）")

    # 写 CSV 清单
    with open(OUT_TABLE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        # M-3：coverage 有两种口径（标题核心串 / 真实梦境正文命中），
        # 必须在表内标注，不能让两种口径混着看。
        w.writerow(["name", "type", "coverage", "coverage_basis", "luck", "luck_basis",
                    "tone", "symbols", "symbols_basis", "match", "match_branches",
                    "head_entry_count",
                    "gloss", "gloss_evidence", "ji", "xiong"])
        for r in uniq_rules:
            luck_basis = r.get("luck_basis") or (
                "brief 指定（交通/事故/时间压力族不做吉凶推断）"
                if "mandatory" in r["source"] else "语料同场景判词词频")
            w.writerow([r["name"], r["type"], r["coverage"],
                        r.get("coverage_basis", "标题核心串含该元素的去重语料条数"),
                        r["luck"], luck_basis, r["tone"],
                        "、".join(r["symbols"]), r.get("symbols_basis", ""),
                        r["match"],
                        " ; ".join(f"{b['branch']}={b['coverage']}"
                                   for b in (r.get("match_branches") or [])),
                        r.get("head_entry_count", 0),
                        r["gloss"],
                        r.get("gloss_evidence", ""),
                        r["evidence"].get("ji", 0), r["evidence"].get("xiong", 0)])

    # 写 Python 模块
    lines = [
        '"""解梦规则层（**自动生成，勿手改**）。',
        "",
        f"由 scripts/k55_dream/build_rules.py 于 {now_iso()} 从真实语料统计生成：",
        "- 元素清单与覆盖量：reports/element_freq_top.csv（去重语料 "
        f"{len(uniq)} 条）",
        "- 核心象征：reports/element_symbols.json（语料共现实词）",
        "- 传统释义：clean/public_domain_quotes.jsonl（**古籍引文，转录未校勘**）",
        "  优先，且必须**同场景**；取不到走诚实兜底（gloss_evidence 字段标注来源档次）",
        "- 类型：站点自有分类（data/competitor_data 的 category 字段）优先",
        "- 吉凶：语料**同场景**判词词频统计；无同场景证据时取「中性」不做方向判断；",
        "  交通/事故/时间压力族由 brief 口径直接指定（中性/提醒类），不由词频推断",
        "- **进族判据（k58 r5 控制方裁决三，勿按「看起来都是车」扩族）**：族内成员是",
        "  「**现代**交通工具/情境，古籍成书时无对应意象 → 无传统依据可依 → 不做吉凶推断」",
        "  （立论源自 k55-r3 的 `车祸` 教训）。**有古籍依据的古代意象不入族**：`马车` 古代",
        "  本以车马为象（《敦煌本梦书》「梦见马，吉；乘行，大富」），故 `马车` **按语料统计走**",
        "  （当前 `吉多于凶`），不以「现代无依据」为由中性化 —— 理由不成立。",
        "- 覆盖量：字段 coverage 的口径见 coverage_basis（两种口径不可混看）",
        "",
        "约束：match 每个分支均 ≥2 字（k49 教训：单字条目会前缀误伤）；",
        "叙述性/体裁性元词不得成为规则条目（见 build_rules.RULE_STOPWORDS）。",
        '"""',
        "",
        "# Hall & Van de Castle 梦境内容常模（来源：dreams.ucsc.edu/Norms，",
        "# 男性 500 梦 / 女性 491 梦 / 合计 991 梦；本仓库 evidence 已存档该页）",
        "HVDC_NORMS = {",
        '    "source": "Hall & Van de Castle norms, dreams.ucsc.edu/Norms (991 dreams)",',
    ]
    for k, v in [
        ("male_female_percent", "57%"), ("familiarity_percent", "52%"),
        ("friends_percent", "35%"), ("animal_percent", "5%"),
        ("aggression_per_char", ".28"), ("friendliness_per_char", ".21"),
        ("sexuality_per_char", ".04"),
        ("dreams_with_aggression", "45%"), ("dreams_with_friendliness", "40%"),
        ("dreams_with_sexuality", "8%"), ("dreams_with_misfortune", "35%"),
        ("dreams_with_success", "11%"), ("dreams_with_failure", "12%"),
        ("indoor_setting_percent", "55%"), ("familiar_setting_percent", "69%"),
        ("negative_emotions_percent", "80%"),
    ]:
        lines.append(f'    "{k}": "{v}",')
    lines += [
        "}",
        "",
        "# 「现实投影」口径（产品层：传统释义与现代视角分层呈现）",
        "REALITY_PROJECTION = {",
        '    "continuity": "梦主要是清醒生活的延续与复现，多数梦境元素来自做梦者'
        '近期的现实经历、关切与情绪（连续性假设，Hall 的梦内容研究传统）",',
        '    "typical_dreams": "被追、坠落、考试、掉牙等『典型梦』在人群中的出现'
        '比例其实很低（约 1% 量级），大部分人并不会频繁梦到它们——所以不必因'
        '梦到某个传统『凶兆』而恐慌",',
        '    "negative_bias": "常模显示梦中负面情绪占比高达 80%（HVDC 常模），'
        '负面梦是常态而非预兆",',
        '    "norm_ref": "HVDC_NORMS",',
        "}",
        "",
        "# ── 规则条目（按覆盖量排序，交通驾驶族置前） ──────────────────",
        "DREAM_PATTERN_RULES = [",
    ]
    for r in uniq_rules:
        lines.append("    {")
        lines.append(f'        "name": {r["name"]!r},')
        lines.append(f'        "match": {r["match"]!r},')
        lines.append(f'        "type": {r["type"]!r},')
        lines.append(f'        "symbols": {r["symbols"]!r},')
        lines.append(f'        "tone": {r["tone"]!r},')
        lines.append(f'        "luck": {r["luck"]!r},')
        lines.append(f'        "gloss": {r["gloss"]!r},')
        lines.append(f'        "coverage": {r["coverage"]!r},')
        lines.append(f'        "coverage_basis": {r.get("coverage_basis", "")!r},')
        lines.append(f'        "gloss_evidence": {r.get("gloss_evidence", "")!r},')
        if r.get("match_branches"):
            lines.append(f'        "match_branches": {r["match_branches"]!r},')
            lines.append(f'        "match_guard": {r.get("match_guard", {})!r},')
        lines.append(f'        "symbols_basis": {r.get("symbols_basis", "")!r},')
        lines.append(f'        "luck_basis": {r.get("luck_basis", "")!r},')
        # 计数随模块落库：不变式（fallback ⟹ ji+xiong==0）可离线断言，
        # 不依赖 /mnt/d 的统计产物
        lines.append(f'        "counts": {dict(r["evidence"])!r},')
        lines.append(f'        "head_entry_count": {r.get("head_entry_count", 0)!r},')
        lines.append(f'        "standalone_count": {r.get("standalone_count", 0)!r},')
        lines.append(f'        "source": {r["source"]!r},')
        lines.append("    },")
    lines += ["]", "", f"RULE_COUNT = {len(uniq_rules)}", ""]
    OUT_MODULE.write_text("\n".join(lines), encoding="utf-8")
    log(f"[rules] 模块 → {OUT_MODULE}（{len(uniq_rules)} 条）")

    for r in uniq_rules[:15]:
        log(f"    {r['name']}\t{r['type']}\t覆盖{r['coverage']}\t{r['luck']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
