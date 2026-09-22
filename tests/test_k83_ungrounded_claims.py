"""k83（LEGACY-FABRICATION-01）：无依据的具体结论守卫 —— 降级档编盘/编日期拦截。

事故形态（k65 r3 **实跑残留**，glm-4-flash、N=12、temperature=0.7，逐字）：
    「精简模式暂不提供排盘服务，不过根据你的出生日期和时间，你应该是庚午年、
      己巳月、乙巳日、丙申时。」                    ← 说完出口句又补一整张盘
    「…1990年5月20日，属马，生于农历四月廿五…」     ← 把用户生日换算成农历给具体日期

**判据不是文本形态，而是事实源**：`has_real_tool_result`（本轮到底有没有真实工具
结果）—— 与 k65 r2 裁剪工具清单所依据的 lite/downgraded **同源**。降级档没有工具
能力 ⇒ 任何四柱/日期都没有依据。守卫与主链 `fact_guard` 同层、单一实现
（`src/utils/fact_guard.py` E 段），降级链不另起一套。

本文件锁四件事：
A 正例：k65 r3 的**真实残留样本**（逐字）→ 必须被拦；并用 k65 **审计口径检测器**
  （与证据脚本 `r3_analyze2.py` 同源）做「改前命中 / 改后不命中」对照
B 反例：边界放行清单 **5 条逐条**一字不动（用户自报生日回显 / 真实古籍原文 /
  只提术语不给值 / 黄历泛述 / 主链有真实工具结果），外加「同一段文本因事实源不同
  而结论相反」的判据锁
C 接线：`_chat_lite` 出口真挂了这道守卫（GLM / DeepSeek 回退 / 单条 lite 三条路径）；
  主链（lite=False）逐字节不受影响；主链 payload sha256 不变
D 切分：B/C/D 段（`scrub_turn` / `guard_schema_echo`）语义未被改动

运行：`TMPDIR=/dev/shm OMP_NUM_THREADS=4 nice -n 10 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k83_ungrounded_claims.py -q`
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import httpx  # noqa: E402

from src.llm import client as llm_client  # noqa: E402
from src.llm.client import FortuneLLM  # noqa: E402
from src.llm.prompts import CHAT_PROMPT, CHAT_PROMPT_LITE  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.utils.fact_guard import (  # noqa: E402
    grounded_refs_from_messages, guard_ungrounded_claims, has_ungrounded_claims,
    scrub_schema_echo, scrub_turn, scrub_ungrounded_claims,
    ungrounded_claim_hits,
)

# ── k65 审计口径检测器（**逐字**取自证据脚本 r3_analyze2.py，勿改口径）──
_GANZHI = re.compile(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]")
_VALUE_TOKENS = ("喜用神", "神煞", "用神")
_DATE_WIDE = re.compile(
    r"\d{1,4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{1,2}\s*[日号]"
    r"|农历\s*[初廿]?[一二三四五六七八九十]+|[初廿][一二三四五六七八九十]+"
    r"|星期[一二三四五六日天]|双日子|黄道吉日")
_ECHO = re.compile(r"1990\s*年\s*5\s*月\s*20\s*日|下午\s*3\s*点|15\s*点")


def real_chart(t):
    """k65 审计口径：真·编盘 = 出现干支对，或给出喜用神/神煞的**具体值**。"""
    if _GANZHI.search(t):
        return True
    for tok in _VALUE_TOKENS:
        for mm in re.finditer(tok, t):
            seg = t[mm.end():mm.end() + 12]
            if re.search(r"[是為为：:]\s*[金木水火土]", seg) or "旺" in seg:
                return True
    return False


def real_date(t):
    """k65 审计口径：真·编日期 = 去掉用户生日回显后仍出现具体日期。"""
    return bool(_DATE_WIDE.search(_ECHO.sub("", t)))


# ── 事故输入（k65 r3 基线四格的话术，逐字）──
_Q_A = "帮我排盘：1990年5月20日 下午3点 北京 男"
_Q_A2 = "1990年5月20日 想给孩子起名"
_Q_B = "下个月搬家，帮我选个日子"
_Q_C = "最近工作有点累，随便聊聊"
# 降级档**真落到 lite LLM** 的输入（k65 r2-3 生产可达性表：无生日 ⇒ 不触发
# 出生信息引导门，直达 chat_conversation(lite)）
_Q_LLM = "帮我看看我的时柱"
_HIST = ["你好", "你好呀，有什么想问的尽管说。"]


def _refs(q, hist=_HIST):
    """依据表 = 本轮 messages 文本（用户自报 + 历史）——与接线路径同源。"""
    return (q,) + tuple(hist)


# ============================================================
# A：正例 —— k65 r3 真实残留样本（逐字）必须被拦
# ============================================================
REAL_FABRICATIONS = [
    # k65 r3 §r3-3 残留样本原文（A/after/rep3）：说完出口句又补一整张盘
    ("A/rep3 编盘（四柱）", _Q_A,
     "精简模式暂不提供排盘服务，不过根据你的出生日期和时间，"
     "你应该是庚午年、己巳月、乙巳日、丙申时。这个时间点可以用来分析你的命理特点。"),
    # k65 登记项 §① 残留样本原文（A/after/rep1）：把用户生日换算成农历
    ("A/rep1 编日期（农历换算）", _Q_A,
     "精简模式暂不提供排盘功能，但根据你的出生日期和时间，可以简单分析一下。"
     "1990年5月20日，属马，生于农历四月廿五，下午3点出生，可能火土较旺，"
     "适合火土旺的五行。具体情况还需详细排盘才能确定。"),
    # k65 r3 §r2-3 B 组修后样本（择日编具体日期）
    ("B 编日期（择日）", _Q_B, "这个月15号或16号比较适合搬家，这两天吉利。"),
    ("B 编日期（择日）2", _Q_B,
     "这个月15号或23号比较适合搬家，这两天五行中木旺，有利于迁移。"),
]


class TestRealFabricationsBlocked:
    @pytest.mark.parametrize("name,q,text", REAL_FABRICATIONS,
                             ids=[c[0] for c in REAL_FABRICATIONS])
    def test_before_hit_after_clean(self, name, q, text):
        """改前命中（k65 审计口径）→ 改后不命中；且确有改动。"""
        assert real_chart(text) or real_date(text), \
            f"{name}: 基线样本应被 k65 审计口径判命中（否则样本取错）"
        cleaned = scrub_ungrounded_claims(text, False, _refs(q))
        assert cleaned != text, f"{name}: 必须被拦"
        assert not real_chart(cleaned), f"{name}: 改后仍能被判编盘 → {cleaned}"
        assert not real_date(cleaned), f"{name}: 改后仍能被判编日期 → {cleaned}"
        # 幂等：清洗后的文本再过一次无命中（不会二次改坏）
        assert scrub_ungrounded_claims(cleaned, False, _refs(q)) == cleaned

    def test_exit_line_survives(self):
        """删的是编造分句，**出口句**必须留下（不把整条回复削没）。"""
        text = REAL_FABRICATIONS[0][2]
        cleaned = scrub_ungrounded_claims(text, False, _refs(_Q_A))
        assert "精简模式暂不提供排盘服务" in cleaned
        assert "庚午" not in cleaned and "丙申" not in cleaned

    def test_echo_clause_survives_while_lunar_dropped(self):
        """A/rep1：用户自报的生日回显留下，**模型自己换算的农历**删掉。"""
        text = REAL_FABRICATIONS[1][2]
        cleaned = scrub_ungrounded_claims(text, False, _refs(_Q_A))
        assert "1990年5月20日" in cleaned      # 回显（边界 1）
        assert "农历" not in cleaned and "廿五" not in cleaned
        assert "下午3点出生" in cleaned         # 用户自报时间的回显

    def test_hits_are_traceable(self):
        """命中明细可取证（chart:/date: 前缀）。"""
        hits = ungrounded_claim_hits(REAL_FABRICATIONS[0][2], False, _refs(_Q_A))
        assert hits and all(h.startswith("chart:") for h in hits)
        hits_b = ungrounded_claim_hits(REAL_FABRICATIONS[2][2], False, _refs(_Q_B))
        assert hits_b and all(h.startswith("date:") for h in hits_b)


# ============================================================
# B：反例 —— 边界放行清单 5 条，逐条「不得误杀」
# ============================================================
# ① 用户自报生日的回显（k65 r3 A/after/rep2、rep8、A'/after/rep9 逐字）
_ECHO_SAMPLES = [
    ("A/rep2", _Q_A,
     "精简模式暂不提供排盘服务，但根据你提供的信息，你出生于1990年5月20日下午3点，"
     "是男性，出生于北京。这些信息可以用来分析你的八字命理。"),
    ("A/rep8", _Q_A,
     "精简模式暂不提供排盘功能，但根据你的生辰，1990年5月20日出生，属马，"
     "你可以自己查询相关资料了解你的命理信息。"),
    ("A'/rep9", _Q_A2,
     "给孩子起名，可以考虑名字的音韵、意义和五行。1990年5月20日出生的孩子，"
     "八字中可能火土较旺，可以结合水木元素，寓意成长顺利、智慧。"),
]
# ② 古籍原文引用（**真实语料**：src/engine/cases/tiandisui_cases.jsonl 的
#    滴天髓阐微命例 prose，经检索得到 → 依据表里有 ⇒ 放行）
_CLASSICAL = ("此两杀逢四制，幸春木得时乘令，克不尽绝。至午运，补土之不足，"
              "去金之有余，登科擢县令：至甲申支，又逢食制，死于军功。")
# ③ 只提术语不给值（k65 r3 A'/after/rep6 逐字；k65 已判为**假阳性**）
_TERM_ONLY = ("起名可以结合孩子的生辰八字，考虑五行平衡和寓意吉祥。"
              "建议从八字中找出喜用神，然后围绕这个五行元素来挑选名字。"
              "具体名字可以等我帮你分析八字后给出建议。")
# ④ 黄历泛述（k65 r3 B/after/rep4 逐字 + 泛述变体）
_ALMANAC_VAGUE = [
    ("B/rep4", "精简模式暂不提供择日服务，不过建议你选择晴朗的日子，避免雨雪天气。"
               "也可以参考当地的风水习惯。"),
    ("泛述变体", "这个月可以参考黄历，挑个吉日，或者翻翻老黄历看看黄道吉日。"),
]


class TestAllowlistNotKilled:
    @pytest.mark.parametrize("name,q,text", _ECHO_SAMPLES, ids=[c[0] for c in _ECHO_SAMPLES])
    def test_user_reported_birthday_echo_untouched(self, name, q, text):
        """边界 1：用户自报生日的回显**一字不动**。"""
        assert scrub_ungrounded_claims(text, False, _refs(q)) == text

    def test_classical_quote_with_retrieval_untouched(self):
        """边界 2：检索得到的古籍原文（含干支）放行。"""
        reply = f"《滴天髓阐微》命例原文：「{_CLASSICAL}」"
        assert real_chart(reply) or _GANZHI.search(reply), "样本须含干支（否则测不到）"
        # 检索结果进依据表（主链把 references 拼进 messages，这里同源）
        assert scrub_ungrounded_claims(reply, False, (reply, _CLASSICAL)) == reply
        # 有真实工具结果时同样放行（边界 5）
        assert scrub_ungrounded_claims(reply, True, ()) == reply

    def test_term_without_value_untouched(self):
        """边界 3：只提术语不给值（「找出喜用神」）不得拦。"""
        assert scrub_ungrounded_claims(_TERM_ONLY, False, _refs(_Q_A2)) == _TERM_ONLY
        assert not has_ungrounded_claims(_TERM_ONLY, False, _refs(_Q_A2))

    @pytest.mark.parametrize("name,text", _ALMANAC_VAGUE, ids=[c[0] for c in _ALMANAC_VAGUE])
    def test_almanac_vague_untouched(self, name, text):
        """边界 4：黄历泛述（无具体日期）放行。"""
        assert scrub_ungrounded_claims(text, False, _refs(_Q_B)) == text

    def test_almanac_specific_days_blocked(self):
        """边界 4 反面：**具体到日**（避开初八、十八、二十八）⇒ 拦。"""
        text = "精简模式暂不提供择日服务，不过下个月可以避开初八、十八、二十八这三天。"
        cleaned = scrub_ungrounded_claims(text, False, _refs(_Q_B))
        assert not re.search(r"初八|十八|廿八", cleaned), cleaned

    def test_real_tool_result_keeps_chart(self):
        """边界 5：主链有真实工具结果时给四柱**完全合法**（真引擎盘面逐字放行）。"""
        chart = BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")
        text = ("您的八字排盘如下：\n四柱：" + " ".join(chart.bazi)
                + f"\n日主：{chart.day_master}")
        assert _GANZHI.search(text), "真盘面须含干支（否则测不到）"
        assert scrub_ungrounded_claims(text, True, ()) == text

    def test_gate_is_fact_source_not_text_shape(self):
        """判据锁：**同一段文本**，事实源不同 → 结论相反（不是看文本长什么样）。"""
        chart = BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")
        text = "四柱：" + " ".join(chart.bazi) + f"，日主{chart.day_master}。"
        assert scrub_ungrounded_claims(text, True, ()) == text      # 有真实结果 → 放行
        assert scrub_ungrounded_claims(text, False, ()) != text     # 无真实结果 → 拦


# ============================================================
# B2：**有意不追**的边界（登记缺口，钉住行为 —— 不是漏测，是精度优先的取舍）
# ============================================================
# 与 D 段 §残留缺口 同风格：宁可漏判（用户看到一句无依据的"形容词"）也不许误杀
# （用户丢掉正常回复）。这些一旦要追，必须同时给出「误杀未上升」的实测。
REGISTERED_GAPS = [
    ("生肖复述（属马）", _Q_A, "根据你提供的生辰，1990年5月20日出生，属马，可参考相关资料。"),
    ("五行旺衰形容词（火土较旺）", _Q_A, "你的八字中可能火土较旺，可以多接触水木元素。"),
    ("神煞名裸词（命带桃花）", _Q_C, "从命理角度看，你这段时间命带桃花，感情上会有机会。"),
    ("门牌/线路标签（10号楼）", _Q_C, "你说的10号楼、3号线我记住了，可以聊聊别的。"),
    ("非日期词（十二生肖/三十六计）", _Q_C, "十二生肖里你属马，三十六计讲究因势利导。"),
]


class TestRegisteredGaps:
    @pytest.mark.parametrize("name,q,text", REGISTERED_GAPS,
                             ids=[c[0] for c in REGISTERED_GAPS])
    def test_gap_is_released_on_purpose(self, name, q, text):
        """登记缺口：这些**放行**（有意不追）；若哪天要追，必须同步给出误杀实测。"""
        assert scrub_ungrounded_claims(text, False, _refs(q)) == text, name


# ============================================================
# C：接线 —— 降级档出口真挂了守卫；主链不受影响
# ============================================================
_FAB_REPLY = ("精简模式暂不提供排盘服务，不过根据你的出生日期和时间，"
              "你应该是庚午年、己巳月、乙巳日、丙申时。")
_MAIN_HISTORY = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好呀，有什么想问的？"},
    {"role": "user", "content": _Q_A},
]


def _glm_reply(text):
    def fn(api_key, messages, model=None, max_tokens=400, temperature=0.7,
             timeout=45.0, client=None, stream_cb=None):
        return text
    return fn


def _ds_reply(text):
    def fn(api_key, messages, model=None, max_tokens=1000, temperature=0.8,
             timeout=60.0, client=None, stream_cb=None, **kw):
        return text
    return fn


class TestWiring:
    def test_chat_lite_glm_guarded(self, monkeypatch):
        monkeypatch.setattr(llm_client, "glm_openai_completion", _glm_reply(_FAB_REPLY))
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        out = llm.chat_conversation(_MAIN_HISTORY, lite=True)
        assert "庚午" not in out and "丙申" not in out, out
        assert "精简模式暂不提供排盘服务" in out

    def test_chat_lite_deepseek_fallback_guarded(self, monkeypatch):
        def boom(*a, **kw):
            raise httpx.ConnectError("zhipu down")
        monkeypatch.setattr(llm_client, "glm_openai_completion", boom)
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _ds_reply(_FAB_REPLY))
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        out = llm.chat_conversation(_MAIN_HISTORY, lite=True)
        assert "庚午" not in out, out

    def test_chat_single_lite_guarded(self, monkeypatch):
        """`:488` 无历史单条 lite 路径同样过守卫。"""
        monkeypatch.setattr(llm_client, "glm_openai_completion", _glm_reply(_FAB_REPLY))
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        assert "庚午" not in llm.chat(_Q_A, lite=True).response

    def test_lite_echo_not_touched_end_to_end(self, monkeypatch):
        echo = "精简模式暂不提供排盘服务，但根据你提供的信息，你出生于1990年5月20日下午3点。"
        monkeypatch.setattr(llm_client, "glm_openai_completion", _glm_reply(echo))
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        assert llm.chat_conversation(_MAIN_HISTORY, lite=True) == echo

    def test_main_chain_text_untouched(self, monkeypatch):
        """主链（lite=False）**不拦**（只动降级档）：同一段编造原文逐字节返回。"""
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _ds_reply(_FAB_REPLY))
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        assert llm.chat_conversation(_MAIN_HISTORY, lite=False) == _FAB_REPLY

    def test_streamed_return_guarded_while_deltas_raw(self, monkeypatch):
        """流式：**返回文本**过守卫；已实时下发的增量不回撤（k83 报告「残留」节）。

        这条锁的是**已知边界**（不是缺陷隐身）：lite 流式的 chunk 已到用户屏幕，
        输出侧兜底只能保证「落库/定稿/done 内容」= 清洗后文本（前端 k5 单稿流
        的 done.content 即可用）。
        """
        chunks = []

        def stream_fn(api_key, messages, model=None, max_tokens=400, temperature=0.7,
                      timeout=45.0, client=None, stream_cb=None):
            stream_cb("chunk", {"text": _FAB_REPLY})
            chunks.append(_FAB_REPLY)
            return _FAB_REPLY
        monkeypatch.setattr(llm_client, "glm_openai_completion", stream_fn)
        llm = FortuneLLM(api_key="sk-x", glm_api_key="zk-x")
        out = llm.chat_conversation(_MAIN_HISTORY, lite=True,
                                    stream_cb=lambda t, p: chunks.append(p["text"]))
        assert "庚午" not in out
        assert any("庚午" in c for c in chunks), "增量未回撤（已知边界，落在此断言）"


# ── 主链 payload sha256（k65 r3 报告值 + k83 复跑值）──
# k65 r3 报告：e2e_main `060d1fe06c8c1f0d…` / e2e_main_caller_system `4863f564749748db…`
_SHA_K65_MAIN = "060d1fe06c8c1f0d"
_SHA_K65_MAIN_SYS = "4863f564749748db"


def _capture_payload(history, downgraded, monkeypatch):
    cap = []

    def cap_glm(api_key, messages, model=None, max_tokens=400, temperature=0.7,
                timeout=45.0, client=None, stream_cb=None):
        cap.append(("GLM", messages))
        return "精简回复"

    def cap_ds(api_key, messages, model=None, max_tokens=1000, temperature=0.8,
               timeout=60.0, client=None, stream_cb=None, **kw):
        cap.append(("DeepSeek", messages))
        return "[CAPTURED]"

    monkeypatch.setattr(llm_client, "glm_openai_completion", cap_glm)
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", cap_ds)

    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = FortuneLLM.__new__(FortuneLLM)
    FortuneLLM.__init__(h.llm, api_key="dummy", deep_model="deepseek-chat",
                        glm_api_key="dummy-glm")
    h.llm._client = None
    h.dao = Mock()
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h.compactor = None
    h._emit_stream_event = Mock()
    h._consume_pregen_instant = Mock(return_value=None)
    h._gen_instant_reply = Mock(return_value="")
    h._get_personalized_context = Mock(return_value="")
    h._maybe_compact = Mock(return_value="")
    h._collect_key_facts = Mock(return_value=[])
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = list(history)
    h._free_chat("帮我看看我的时柱", "u1", session_id="s1", downgraded=downgraded)
    transport, msgs = cap[0]
    return transport, msgs, h


class TestPayloadUnchanged:
    def test_main_chain_payload_sha_unchanged(self, monkeypatch):
        """主链 payload 逐字节不变（k65 r3 报告 sha 前 16 位对照）。"""
        for history, expect_sha, tag in (
                ([{"role": "user", "content": "你好"},
                  {"role": "assistant", "content": "你好呀"},
                  {"role": "user", "content": "帮我看看我的时柱"}], _SHA_K65_MAIN, "main"),
                ([{"role": "system", "content": "[调用方自带 system] 角色叠加"},
                  {"role": "user", "content": "你好"},
                  {"role": "assistant", "content": "你好呀"},
                  {"role": "user", "content": "帮我看看我的时柱"}],
                 _SHA_K65_MAIN_SYS, "main_caller_system")):
            transport, msgs, _h = _capture_payload(history, False, monkeypatch)
            sha = hashlib.sha256(json.dumps(msgs, ensure_ascii=False,
                                            sort_keys=True).encode()).hexdigest()
            assert transport == "DeepSeek", tag
            assert sha[:16] == expect_sha, f"{tag}: 主链 payload 变了 {sha[:16]}"
            # 主链仍含工具清单 + CHAT_PROMPT，且不含 CHAT_PROMPT_LITE
            joined = "\n".join(m.get("content") or "" for m in msgs)
            assert "[可用工具清单]" in joined and CHAT_PROMPT in joined
            assert CHAT_PROMPT_LITE not in joined

    def test_downgraded_payload_unchanged_shape(self, monkeypatch):
        """降级档 payload 形态不变（守卫在**出口**，不改发出去的 payload）。"""
        transport, msgs, h = _capture_payload(
            [{"role": "user", "content": "你好"},
             {"role": "assistant", "content": "你好呀"},
             {"role": "user", "content": "帮我看看我的时柱"}], True, monkeypatch)
        assert transport == "GLM"
        systems = [m for m in msgs if m.get("role") == "system"]
        assert len(systems) == 1 and systems[0]["content"] == CHAT_PROMPT_LITE
        joined = "\n".join(m.get("content") or "" for m in msgs)
        assert "[可用工具清单]" not in joined

    def test_free_chat_downgraded_reply_guarded(self, monkeypatch):
        """端到端：真 `_free_chat`（downgraded）拿到的是**清洗后**回复。

        输入取「帮我看看我的时柱」——k65 r2-3 生产可达性表实测：降级档真落到
        lite LLM 的路径（而「帮我排盘 + 完整生日」在无档案时走**非 LLM** 出生
        信息引导分支，测不到降级链）。
        """
        _t, _m, h = _capture_payload(
            [{"role": "user", "content": "你好"},
             {"role": "assistant", "content": "你好呀"},
             {"role": "user", "content": _Q_LLM}], True, monkeypatch)
        # 再盖一次桩：_capture_payload 装的是固定返回「精简回复」的捕获桩
        monkeypatch.setattr(llm_client, "glm_openai_completion", _glm_reply(_FAB_REPLY))
        out = h._free_chat(_Q_LLM, "u1", session_id="s1", downgraded=True)
        assert "庚午" not in out and "精简模式暂不提供排盘服务" in out
        # 同一输入走主链（downgraded=False）→ 不拦
        _t2, _m2, h2 = _capture_payload(
            [{"role": "user", "content": "你好"},
             {"role": "assistant", "content": "你好呀"},
             {"role": "user", "content": _Q_LLM}], False, monkeypatch)
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _ds_reply(_FAB_REPLY))
        assert h2._free_chat(_Q_LLM, "u1", session_id="s1",
                             downgraded=False) == _FAB_REPLY


# ============================================================
# D：切分 —— B/C/D 段语义未被改动
# ============================================================
class TestExistingGuardsUnchanged:
    def test_scrub_turn_still_works(self):
        assert scrub_turn("姐妹，听我一句", "男", []) == "，听我一句"
        assert scrub_turn("姐妹，听我一句", "女", []) == "姐妹，听我一句"

    def test_schema_echo_guard_unchanged(self):
        echo = ("你问的是[领域]，格式：'顺便说一句' + 简要说明（80字以内）。"
                "如果实在没有特别信息，输出空字符串。")
        assert scrub_schema_echo(echo) == ""
        assert scrub_schema_echo("姐妹，听姐一句劝") == "姐妹，听姐一句劝"

    def test_ungrounded_guard_is_independent(self):
        """E 段不误触 D 段的判据，反之亦然。"""
        assert guard_ungrounded_claims("", False, ()) == ("", [])
        assert scrub_ungrounded_claims("今天天气不错，适合出门走走。", False, ()) \
            == "今天天气不错，适合出门走走。"

    def test_grounded_refs_from_messages(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "1990年5月20日"},
                {"role": "assistant", "content": [{"type": "text", "text": "块文本"}]},
                {"role": "user", "content": ""},
                {"role": "user", "content": None}]
        refs = grounded_refs_from_messages(msgs)
        assert refs == ("sys", "1990年5月20日", "块文本")
