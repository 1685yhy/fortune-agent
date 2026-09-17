"""K52-4 合规扫描（防复发）：用户可见文案面 + 面向输出的提示词面，命中高风险词表即失败。

背景：微信**没有命理/占卜类目**，付费命理在「尚未开放的服务类目」红线边；审核看的是
**实质内容**（用户可见文案 + AI 说给用户听的话）。本测试把「哪些词不许出现在这两面上」
固化成可执行断言，防止后续迭代把它们写回来。

── 扫描面（只许加不许减） ───────────────────────────────────────────────
① 用户可见文案面
   - miniprogram/pages/**/*.wxml      → 去 HTML 注释后的全部文本（标签文本/属性值都算）
   - miniprogram/pages/**/*.js        → **字符串字面量**（含模板串；保守起见内部键值也一起扫，
                                        宁可多报不漏报；注释不算用户可见 → 剥离）
   - miniprogram/utils/**/*.js        → 同上
② 面向输出的提示词面
   - src/**/*prompt*.py               → 当前 = src/llm/prompts.py + src/llm/report_prompts.py
   - src/bot/handler.py               → 全部字符串字面量（**含 docstring**）：
                                        提示词/用户可见回复/内部关键词表都在此文件里，
                                        统一扫、逐条白名单，不做「按类型跳过」以免漏面

── 红线（本文件的任何修改都不得违反） ────────────────────────────────────
1. **不得为了让扫描通过而改宽扫描范围**（如排除 pages、跳过 utils、只扫部分词），
   也不得靠删词让测试变绿：词表只许加不许减（见 HIGH_RISK_WORDS 与
   test_scan_surface_and_wordlist_never_shrink）。
2. 白名单只许用于**确需保留**处，且每条必须写明理由（WHITELIST[*]["reason"]）；
   白名单条目必须真的在压制命中，否则视为过期条目（见 test_whitelist_entries_have_reasons_and_are_used）。

── 已知子串假阳性（需靠白名单逐条说明，不得靠放宽规则） ──────────────────
- 「米大师」= 微信虚拟支付产品名（MIDAS），含子串「大师」；
- 「格式化解梦」= 格式+化解梦，含子串「化解」；
- 「计算命宫」= 计算+命宫，含子串「算命」。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ── 高风险词表（brief 指定 18 词为基线；只许加不许减） ──
HIGH_RISK_WORDS = (
    "占卜", "算命", "卜卦", "改运", "转运", "破财", "血光", "消灾", "化解",
    "法事", "开光", "辟邪", "驱邪", "招财", "旺财", "灵验", "大师", "改命",
    # k52 追加（词表只增不减）
    "化灾",
)
# 基线词表（brief 明示）——独立列出，防未来有人「顺手删词」把测试改绿
BASELINE_WORDS = (
    "占卜", "算命", "卜卦", "改运", "转运", "破财", "血光", "消灾", "化解",
    "法事", "开光", "辟邪", "驱邪", "招财", "旺财", "灵验", "大师", "改命",
)

# ── 扫描面定义 ──
WXML_GLOB = "miniprogram/pages/**/*.wxml"
PAGE_JS_GLOB = "miniprogram/pages/**/*.js"
UTIL_JS_GLOB = "miniprogram/utils/**/*.js"
PROMPT_PY_GLOB = "src/**/*prompt*.py"
PROMPT_PY_REQUIRED = {"src/llm/prompts.py", "src/llm/report_prompts.py"}
HANDLER_PY = "src/bot/handler.py"

# ── 白名单：确需保留处 + 理由（键 = 文件 + 行内含标记；标记取足够独特的片段） ──
WHITELIST = [
    {
        "file": HANDLER_PY,
        "line_contains": '"keywords": ("身弱", "身强", "变强", "调理", "转运", "改运", "增运"',
        "words": {"转运", "改运"},
        "reason": "内部关键词表（命中即出该条知识卡的路由键），属功能逻辑而非文案："
                  "删词会改变意图匹配行为；k52 明示「内部关键词表/路由词本次不动」，"
                  "故登记保留。（用户看到的只有 content 文案，content 内已无高风险词）",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"八字", "紫微", "斗数", "占卜", "命理", "排盘", "塔罗", "看相",',
        "words": {"占卜"},
        "reason": "简单意图守卫词表：防「你好，帮我算八字」被误判为纯寒暄而掐掉主链，"
                  "属防御性逻辑；仅参与判定，不回显给用户。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": 'for kw in ["八字", "排盘", "命理", "帮我", "看命", "算命", "看看"]',
        "words": {"算命"},
        "reason": "工具/命理意图的入参关键词匹配表（内部路由），不构成用户可见文案。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"六爻", "占卜", "起卦", "起一卦", "卜卦", "算卦", "看看", "帮我",',
        "words": {"占卜", "卜卦"},
        "reason": "六爻提问提取用的关键词表（内部路由）；删除会导致「占卜」类表述不再被"
                  "识别为六爻而走错分支。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"""处理六爻占卜请求"""',
        "words": {"占卜"},
        "reason": "函数文档字符串（代码注释），既不入提示词也不进用户可见输出；"
                  "改动它对本批合规目标零收益，登记保留。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"""执行六爻占卜"""',
        "words": {"占卜"},
        "reason": "函数文档字符串（代码注释）：既不入提示词也不进用户可见输出，改动对本批合规目标零收益，登记保留。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"""格式化六爻占卜结果为文本"""',
        "words": {"占卜"},
        "reason": "函数文档字符串（代码注释）：非面向输出，登记保留。注意其**产出的图表串**已改为「六爻推演结果：」（该串会进提示词）。",
    },
    {
        "file": HANDLER_PY,
        "line_contains": '"""格式化解梦信息供LLM分析"""',
        "words": {"化解"},
        "reason": "函数文档字符串 + 子串假阳性：「格式化」+「解梦」相连，"
                  "并非「化解」语义（同「米大师」含「大师」）。",
    },
]


# ══════════════════════════ 文本提取（保留行号） ══════════════════════════

def strip_wxml_comments(src: str) -> str:
    """去 HTML 注释（保留换行数 → 行号不漂移）。注释不是用户可见内容。"""
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)


def js_string_literals(src: str) -> list[tuple[int, str]]:
    """提取 JS 字符串字面量 [(行号, 内容)]，跳过注释。

    轻量词法器（不引第三方解析器）：处理 ' " ` 三种引号、反斜杠转义、// 与 /* */ 注释。
    保守取向：模板串整体取用（含 ${} 内文本），宁可多报不漏报。
    """
    out: list[tuple[int, str]] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in "\"'`":
            quote = c
            start_line = line
            i += 1
            buf: list[str] = []
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(src[i:i + 2])
                    i += 2
                    continue
                if ch == "\n":
                    line += 1
                if ch == quote:
                    i += 1
                    break
                buf.append(ch)
                i += 1
            out.append((start_line, "".join(buf)))
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
        else:
            i += 1
    return out


def py_string_literals(src: str) -> list[tuple[int, str]]:
    """提取 Python 字符串字面量 [(行号, 内容)]（AST；**含 docstring**，不做类型跳过）。"""
    tree = ast.parse(src)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


def find_hits(text: str) -> list[str]:
    """一段文本命中的高风险词（去重、按词表顺序）。"""
    return [w for w in HIGH_RISK_WORDS if w in text]


# ══════════════════════════ 扫描器 ══════════════════════════

class Hit:
    __slots__ = ("path", "lineno", "text", "word", "line")

    def __init__(self, path: str, lineno: int, text: str, word: str, line: str):
        self.path, self.lineno, self.text, self.word, self.line = path, lineno, text, word, line

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} [{self.word}] {self.text.strip()[:80]}"


def _collect() -> tuple[list[Hit], dict[str, int]]:
    """扫描全部面，返回 (命中列表, 各面文件数)。"""
    hits: list[Hit] = []
    counts: dict[str, int] = {}

    def add(rel: str, lineno: int, text: str, src_lines: list[str]) -> None:
        line = src_lines[lineno - 1] if 0 < lineno <= len(src_lines) else text
        for w in find_hits(text):
            hits.append(Hit(rel, lineno, text, w, line))

    for pattern, mode in ((WXML_GLOB, "wxml"), (PAGE_JS_GLOB, "js"), (UTIL_JS_GLOB, "js")):
        files = sorted(ROOT.glob(pattern))
        counts[pattern] = len(files)
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            src = f.read_text(encoding="utf-8")
            src_lines = src.splitlines()
            if mode == "wxml":
                stripped = strip_wxml_comments(src)
                for i, text in enumerate(stripped.split("\n"), 1):
                    add(rel, i, text, src_lines)
            else:
                for lineno, text in js_string_literals(src):
                    add(rel, lineno, text, src_lines)

    prompt_files = sorted(ROOT.glob(PROMPT_PY_GLOB))
    counts[PROMPT_PY_GLOB] = len(prompt_files)
    for f in prompt_files:
        rel = f.relative_to(ROOT).as_posix()
        src = f.read_text(encoding="utf-8")
        src_lines = src.splitlines()
        for lineno, text in py_string_literals(src):
            add(rel, lineno, text, src_lines)

    handler = ROOT / HANDLER_PY
    assert handler.is_file(), f"扫描面缺失：{HANDLER_PY}"
    src = handler.read_text(encoding="utf-8")
    src_lines = src.splitlines()
    for lineno, text in py_string_literals(src):
        add(HANDLER_PY, lineno, text, src_lines)

    return hits, counts


def _is_whitelisted(hit: Hit) -> bool:
    for entry in WHITELIST:
        if entry["file"] == hit.path and entry["line_contains"] in hit.line:
            return True
    return False


# ══════════════════════════ 测试 ══════════════════════════

def test_scan_surface_and_wordlist_never_shrink():
    """红线自检：扫描面必须真有文件；词表必须含 brief 基线 18 词。"""
    _, counts = _collect()
    assert counts[WXML_GLOB] >= 30, f"wxml 面文件数异常（{counts}）：不得排除 pages"
    assert counts[PAGE_JS_GLOB] >= 30, f"pages js 面文件数异常（{counts}）"
    assert counts[UTIL_JS_GLOB] >= 10, f"utils 面文件数异常（{counts}）"
    assert counts[PROMPT_PY_GLOB] >= 2, f"提示词面文件数异常（{counts}）"
    prompt_files = {f.relative_to(ROOT).as_posix() for f in ROOT.glob(PROMPT_PY_GLOB)}
    assert PROMPT_PY_REQUIRED <= prompt_files, f"提示词面文件缺失：{PROMPT_PY_REQUIRED - prompt_files}"
    missing = [w for w in BASELINE_WORDS if w not in HIGH_RISK_WORDS]
    assert not missing, f"高风险词表被削：{missing}（词表只许加不许减）"


def test_scanner_is_not_vacuous():
    """机制自检：扫描器必须真能抓到违规样本（防「规则写错 → 恒绿」）。"""
    assert find_hits("这里写了一句开始占卜") == ["占卜"]
    assert find_hits("大师加持") == ["大师"]
    assert find_hits("干净的一句话") == []
    # WXML 注释不算用户可见 → 剥离后不再命中
    assert find_hits(strip_wxml_comments("<!-- 占卜 --><text>推演</text>")) == []
    assert find_hits(strip_wxml_comments("<text>占卜</text>")) == ["占卜"]
    # JS 注释不算，字符串算
    js = "// 占卜\nconst a = '占卜';\n/* 算命 */\nconst b = 'ok';"
    got = [t for _, t in js_string_literals(js)]
    assert "占卜" in got and "算命" not in got, got
    # Python：docstring 也在扫描面内（本批取「宁可多报」）
    py = '"""含 占卜 的文档串"""\nx = "含 算命 的字面量"'
    texts = [t for _, t in py_string_literals(py)]
    assert any("占卜" in t for t in texts) and any("算命" in t for t in texts), texts


def test_user_visible_copy_has_no_high_risk_words():
    """① 用户可见文案面（pages wxml / pages js / utils js）：零命中。"""
    hits, _ = _collect()
    bad = [h for h in hits if h.path.startswith("miniprogram/") and not _is_whitelisted(h)]
    assert not bad, "用户可见文案命中高风险词：\n" + "\n".join(str(h) for h in bad)


def test_output_facing_prompts_has_no_high_risk_words():
    """② 面向输出的提示词面（prompts 模块 + handler.py 全部字符串）：零命中，白名单除外。"""
    hits, _ = _collect()
    bad = [h for h in hits if not h.path.startswith("miniprogram/") and not _is_whitelisted(h)]
    assert not bad, "提示词/输出面命中高风险词：\n" + "\n".join(str(h) for h in bad)


def test_whitelist_entries_have_reasons_and_are_used():
    """白名单卫生：每条必须有理由、文件必须存在、且确实在压制命中（无过期条目）。"""
    for entry in WHITELIST:
        assert entry.get("reason", "").strip(), f"白名单条目缺理由：{entry}"
        assert len(entry["reason"]) >= 20, f"白名单理由过于简略（需说明为何保留）：{entry}"
        assert (ROOT / entry["file"]).is_file(), f"白名单指向不存在的文件：{entry['file']}"
        assert set(entry["words"]) <= set(HIGH_RISK_WORDS), f"白名单词不在词表内：{entry}"
    hits, _ = _collect()
    used = {id(e) for h in hits for e in WHITELIST
            if e["file"] == h.path and e["line_contains"] in h.line}
    stale = [e["line_contains"] for e in WHITELIST if id(e) not in used]
    assert not stale, f"白名单条目已失效（对应命中已消除 → 请删除该条目）：{stale}"


def test_no_deprecated_platform_api_in_payment():
    """k52-1 伴随红线：支付模块不得引入已废弃的平台 API（平台判定用新 API）。"""
    src = (ROOT / "miniprogram/utils/payment.js").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"//.*$", "", line) for line in re.sub(r"/\*.*?\*/", "", src, flags=re.S).split("\n"))
    assert "getSystemInfoSync" not in code and "getSystemInfo(" not in code
    assert "getDeviceInfo" in code, "平台判定应使用 wx.getDeviceInfo（新 API）"
    # iOS 屏蔽必须落在「发起购买」的三个入口上
    for marker in ("function tryVirtualPay", "async function purchase", "async function subscribeMember"):
        assert marker in src, f"支付入口缺失：{marker}"
    assert src.count("isPurchaseBlocked()") >= 3, "三个购买入口都要过平台闸门"


@pytest.mark.parametrize("word", BASELINE_WORDS)
def test_word_in_scan_list(word):
    """参数化兜底：基线词逐个在表（失败时一眼看出被删的是哪个词）。"""
    assert word in HIGH_RISK_WORDS


# ══════════════════ k52-3 AI 输出采样自查（真实链路产出，不打真实 LLM） ══════════════════
# 扫描面只证明「源码字面量干净」；本节把**真实代码路径组装出来的**文本抓出来再扫一遍：
# 兜底话术（知识卡/欢迎语/六爻图表串/解梦提示词/合盘建议/付费墙悬念半句）。
# 注：handler.py 的 `_help_message` 是 `_get_welcome_message` 内的嵌套函数（无调用点·死代码），
# 不进入采样集；其文案亦已同步改词（登记于报告）。
# 说明（诚实披露）：**LLM 自己生成的那一段**无法在此断言（本节全为确定性文本，
# 不依赖网络与模型）；模型侧采样见 .superpowers/sdd/task-k52-report.md 的自查记录。

def _sample_output_texts() -> list[tuple[str, str]]:
    """真实链路产出文本采样：[(来源说明, 文本)]。"""
    from unittest.mock import Mock

    from src.bot import handler as handler_mod
    from src.engines.dream import DreamEngine, format_dream_prompt
    from src.engines.hehun import HehunEngine
    from src.engines.yuan_quote import generate_yuan_quote

    samples: list[tuple[str, str]] = []

    # ① 欢迎语 + 帮助语（模块级/方法级确定性文案，直接发给用户）
    samples.append(("_get_welcome_message()", handler_mod._get_welcome_message()))
    mock_dao = Mock()
    mock_dao.db_path = "/tmp/k52-scan.db"
    mock_dao.get_user_bazi.return_value = None
    h = handler_mod.MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=Mock(), dao=mock_dao, session_dao=Mock(),
    )

    # ② 通用命理知识卡全文（D9 无档案问事的确定性兜底，逐条用户可见）
    for i, entry in enumerate(handler_mod._BAZI_GENERAL_KNOWLEDGE):
        samples.append((f"_BAZI_GENERAL_KNOWLEDGE[{i}].content", entry["content"]))
    samples.append(("_BAZI_GENERAL_KNOWLEDGE_FALLBACK", handler_mod._BAZI_GENERAL_KNOWLEDGE_FALLBACK))

    # ③ 六爻图表串（进入提示词 → 直接塑造模型输出）
    r = Mock()
    r.question = "这次跳槽能成吗"
    r.original_hexagram = "天地否"
    r.changed_hexagram = "风地观"
    r.palace = "乾"
    r.palace_wuxing = "金"
    r.changing_lines = [2, 4]
    r.lines = [{"type": "少阴", "yao_type": "应", "liuqin": "妻财", "dizhi": "卯"}] * 6
    samples.append(("MessageHandler._format_liuyao_chart()", h._format_liuyao_chart(r)))

    # ④ 解梦提示词（真实引擎 + 真实格式化）
    dream_engine = DreamEngine()
    _retr = Mock()
    _retr.search.return_value = []          # 空召回：走确定性兜底，不触网络
    dream_result = dream_engine.analyze("梦见大水冲进了家里，很害怕", _retr)
    samples.append(("format_dream_prompt()", format_dream_prompt(
        "梦见大水冲进了家里，很害怕", dream_result, "最近换工作", {"day_master": "甲木"})))

    # ⑤ 合盘综合建议（各分数档 + 弱项分支全走一遍）
    hehun = HehunEngine()
    weak = {"score": 10}
    for score in (85, 70, 55, 40, 20):
        samples.append((f"HehunEngine._generate_advice({score})",
                        hehun._generate_advice(score, weak, weak, weak)))

    # ⑥ 付费墙悬念半句（免费档可见）
    quote = generate_yuan_quote("上等", ["五行相克"], "恋人", cliffhanger=True)
    samples.append(("generate_yuan_quote(cliffhanger=True)", str(quote)))

    return samples


def test_sampling_real_path_output_has_no_high_risk_words():
    """k52-3 采样自查：真实链路产出的确定性文案，零高风险词命中。"""
    bad = []
    for source, text in _sample_output_texts():
        hit = find_hits(text or "")
        if hit:
            bad.append(f"{source} → {hit}\n  {(text or '')[:200]}")
    assert not bad, "采样到高风险词：\n" + "\n".join(bad)


def test_sampling_sample_set_is_not_empty_and_covers_key_surfaces():
    """采样集自检：样本数与覆盖面（防采样悄悄空跑变恒绿）。"""
    samples = _sample_output_texts()
    assert len(samples) >= 12, f"采样样本过少：{len(samples)}"
    joined = "\n".join(s[0] for s in samples)
    for marker in ("_get_welcome_message", "GENERAL_KNOWLEDGE",
                   "_format_liuyao_chart", "format_dream_prompt", "_generate_advice",
                   "generate_yuan_quote"):
        assert marker in joined, f"采样缺失关键面：{marker}"
    # 六爻图表串必须已改名（旧「六爻占卜结果：」会进提示词）
    liuyao = dict(samples)["MessageHandler._format_liuyao_chart()"]
    assert liuyao.startswith("六爻推演结果："), liuyao[:40]
