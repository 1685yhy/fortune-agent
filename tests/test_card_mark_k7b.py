"""k7b 防 LLM 仿写卡尾（2026-09-05，k7 端到端重放实证的次生问题）：

k7 双份修复后用同一真实请求重放（session s_mtme8nc9afzo），落库定稿
id=30 出现三连环：LLM 从会话历史仿写卡尾（卡头行+假图行+假[/card]+
假页脚）→ 保底判定真图行"丢失"追加 → 装配层防重复包装被绕过。
机理：卡/图/页脚是渲染装饰，只由装配层注入一次；LLM 只该看到和产出
正文——本测试覆盖三层修复：
1. strip_card_decor_for_llm 纯函数（行级剥离，正文逐行保留+空行压缩）；
2. session_dao.get_context_for_llm 返回前对 assistant 消息只读清洗
   （库内原文不变——前端历史渲染仍读原文含卡）；
3. _polish_with_engine_draft 对 LLM 输出净化后再补图行/tail。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_card_mark_k7b.py -q
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.bot.card_mark import strip_card_decor_for_llm  # noqa: E402
from src.bot.handler import MessageHandler, _FEEDBACK_PROMPT  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

# ─────────────────────────── 实录样例（brief 附录，测试直接使用） ───────────────────────────

# id=28：18:47 定稿（干净，装饰独立成行，装配层唯一注入）
ID28_CARD = (
    '[card:paipan title="我的命盘"]\n'
    "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
    "\n"
    "【排盘信息】\n"
    "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
    "…\n"
    "你命里火土燥，水是你的解药，多补水少熬夜，别作。\n"
    "\n"
    "📊 命盘图片：http://124.221.233.214/charts/bazi_20260904_184706.png\n"
    "[/card]\n"
    "\n"
    "———\n"
    "这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
)

# id=28 剥离后应剩的正文（装饰行删除 + 尾部空行压缩）
ID28_BODY_EXPECTED = (
    "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
    "\n"
    "【排盘信息】\n"
    "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
    "…\n"
    "你命里火土燥，水是你的解药，多补水少熬夜，别作。"
)

# id=30：本次仿写（假图行无📊 两空格前缀 + 假闭合 + 假页脚后接真图行 + tail 页脚）
ID30_CARD = (
    '[card:paipan title="我的命盘"]\n'
    "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
    "…正文…\n"
    " 命盘图片：http://124.221.233.214/charts/bazi_20260904_184706.png\n"
    "[/card]\n"
    "\n"
    "———\n"
    "这个分析对你有帮助吗？可回复「准」或「不准」告诉我\n"
    "\n"
    "📊 命盘图片：http://124.221.233.214/charts/bazi_20260905_092308.png\n"
    "\n"
    "———\n"
    "这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
)

# id=30 剥离后应剩的正文（两处假图行/假闭合/假页脚/分隔行全部剥除）
ID30_BODY_EXPECTED = (
    "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
    "…正文…"
)


# ═══════════════════════════ 1) strip_card_decor_for_llm 纯函数 ═══════════════════════════

class TestStripCardDecorForLLM:
    def test_id28_clean_draft_only_body_left(self):
        """18:47 原定稿（干净样例）：只剩正文，正文行逐字保留、段落完好。"""
        out = strip_card_decor_for_llm(ID28_CARD)
        assert out == ID28_BODY_EXPECTED
        assert "[card:" not in out
        assert "[/card]" not in out
        assert "命盘图片" not in out
        assert "📊" not in out
        assert "可回复" not in out
        assert "———" not in out
        # 正文行逐字保留
        for line in ("行，我给你重新排一次。你核对下时辰和出生地有没有记错。",
                     "【排盘信息】",
                     "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）",
                     "你命里火土燥，水是你的解药，多补水少熬夜，别作。"):
            assert line in out
        # 段落间隔正常（原正文段落间 1 空行仍在；装饰区空行不残留）
        assert out == (
            "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
            "\n"
            "【排盘信息】\n"
            "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
            "…\n"
            "你命里火土燥，水是你的解药，多补水少熬夜，别作。")

    def test_id30_imitation_only_body_left(self):
        """本次 id=30 仿写样例：假卡头/两空格前缀假图行/假闭合/假页脚×2/
        真图行/分隔行×2 全剥，只剩正文两行。"""
        out = strip_card_decor_for_llm(ID30_CARD)
        assert out == ID30_BODY_EXPECTED
        assert "[card:" not in out
        assert "[/card]" not in out
        assert "命盘图片" not in out          # 真/假图行都剥（含无📊 两空格前缀形态）
        assert "📊" not in out
        assert "可回复" not in out
        assert "———" not in out
        assert "184706" not in out
        assert "092308" not in out
        assert "行，我给你重新排一次。你核对下时辰和出生地有没有记错。" in out

    def test_body_lookalikes_preserved(self):
        """误伤防护：正文行与装饰同形但不满足完整规则 → 原样保留。"""
        cases = [
            "你的命盘：乙木日主生于巳月，食伤当令而思虑细腻，做事讲究条理。",
            "命盘图片：明天整理好再发你。",          # 有「命盘图片：」但无 URL
            "今日卦象（📊）：宜静不宜动，财不入急门。",  # 含 📊 但无 http
            "你可以回复「准」告诉我你的感受。",      # 有「准」但无 可回复/「不准」
            "你的消息我已阅。可回复内容均为人工核对。",  # 有 可回复 但无 准/不准
            "不要自己加[card:paipan]标签——我讲人话就好。",  # [card: 不在行首
        ]
        for c in cases:
            assert strip_card_decor_for_llm(c) == c, f"误伤: {c!r}"

    def test_no_decor_identity(self):
        """无装饰文本 → 返回原文（strip 后恒等）。"""
        plain = "行，我给你重新排一次。你核对下时辰和出生地有没有记错。"
        assert strip_card_decor_for_llm(plain) == plain
        padded = "  己卯年 己巳月 乙丑日 辛巳时  \n"
        assert strip_card_decor_for_llm(padded) == padded.strip()

    def test_none_empty_guards(self):
        """空串 / None 防御。"""
        assert strip_card_decor_for_llm("") == ""
        assert strip_card_decor_for_llm(None) is None
        assert strip_card_decor_for_llm("   \n\n ") == ""

    def test_blank_lines_compressed_between_paragraphs(self):
        """装饰行夹在正文段落间 → 剥后连续空行压缩（≥3 个 \n → 2 个）。"""
        text = (
            "段落甲：今天财运稳步上升。\n"
            "\n"
            "📊 命盘图片：http://x.example/1.png\n"
            "\n"
            "[/card]\n"
            "\n"
            "段落乙：多补水少熬夜，别作。")
        out = strip_card_decor_for_llm(text)
        assert out == "段落甲：今天财运稳步上升。\n\n段落乙：多补水少熬夜，别作。"

    def test_isolated_decor_lines_removed(self):
        """各类装饰独立成行 → 整行剥除（含缩进/前缀空格形态）。"""
        text = (
            "  [card:paipan title=\"我的命盘\"]\n"      # 缩进卡头
            "正文行一。\n"
            "  [/card]  \n"                              # 缩进+尾随空格卡闭合
            "正文行二。\n"
            "\n"
            "   命盘图片：http://x.example/2.png\n"      # 前导空格假图行（无📊）
            "正文行三。\n"
            "———\n"
            "解读版本: v5.0.0 | 生成时间: 2026-08-23T23:20:29+08:00\n"  # 版本页脚行
            "正文行四。")
        out = strip_card_decor_for_llm(text)
        assert out == "正文行一。\n正文行二。\n\n正文行三。\n正文行四。"
        assert "card:" not in out
        assert "命盘图片" not in out
        assert "解读版本" not in out
        assert "———" not in out

    def test_dash_separator_and_emoji_image_line(self):
        """连字符分隔线与 📊 图行（URL 在 📊 后）整行剥除。"""
        text = ("正文甲\n"
                "----\n"
                "📊 命盘图片：http://x.example/3.png\n"
                "正文乙")
        out = strip_card_decor_for_llm(text)
        assert out == "正文甲\n正文乙"


# ═══════════════════════════ 2) DAO 层清洗（只读，不回写） ═══════════════════════════

class TestDaoStripCardDecorForLLM:
    def test_get_context_for_llm_strips_assistant_only_db_untouched(self, tmp_path):
        """get_context_for_llm 返回的 assistant content 已剥卡；user 消息原样；
        库内原文未变（get_history 仍返回含卡原文）。"""
        db = str(tmp_path / "k7b.db")
        init_db(db)
        dao = SessionDAO(db)
        uid = "u_k7b_strip"
        sid = "s_k7bstrip1"
        dao.add_message(uid, "user", "帮我重新排一下盘", session_id=sid)
        dao.add_message(uid, "assistant", ID28_CARD, session_id=sid)

        ctx = dao.get_context_for_llm(uid, history_limit=20, session_id=sid)
        assert [m["role"] for m in ctx] == ["user", "assistant"]
        assert ctx[0]["content"] == "帮我重新排一下盘"      # user 消息不清洗
        assert "[card:" not in ctx[1]["content"]
        assert "📊" not in ctx[1]["content"]
        assert "可回复" not in ctx[1]["content"]
        assert "行，我给你重新排一次。你核对下时辰和出生地有没有记错。" in ctx[1]["content"]
        assert "【排盘信息】" in ctx[1]["content"]          # 正文完整
        assert ctx[1]["content"] == ID28_BODY_EXPECTED

        # 库内原文未变（只读清洗，不回写）
        raw = dao.get_history(uid, session_id=sid)
        raw_asst = [h for h in raw if h["role"] == "assistant"][0]
        assert raw_asst["content"] == ID28_CARD

    def test_plain_user_text_untouched(self, tmp_path):
        """历史里全是普通对话（无卡装饰）→ 上下文原样，无副作用。"""
        db = str(tmp_path / "k7b2.db")
        init_db(db)
        dao = SessionDAO(db)
        uid = "u_k7b_plain"
        sid = "s_k7bplain2"
        plain = "行，我给你重新排一次。你核对下时辰和出生地有没有记错。"
        dao.add_message(uid, "assistant", plain, session_id=sid)
        ctx = dao.get_context_for_llm(uid, session_id=sid)
        assert ctx[0]["content"] == plain


# ═══════════════════════════ 3) polish 输出净化（保底） ═══════════════════════════

def _polish_harness():
    """_polish_with_engine_draft 最小装配（参照 test_handler_qa_fix 既有风格）。"""
    h = object.__new__(MessageHandler)
    h.llm = SimpleNamespace(api_key="test-key", model="test-model")
    h._citations = {"u1": []}
    h.session_dao = None
    return h


class TestPolishStripImitationCardTail:
    def test_polish_strips_imitation_then_reattach_chart_and_tail(self):
        """Mock LLM 仿写整卡输出 → 净化后 = 正文 + 真图行（恰一次）+ tail；
        旧假图 URL 不出现、假卡结构不落库。"""
        h = _polish_harness()
        true_url = "http://124.221.233.214/charts/bazi_20260905_092308.png"
        draft = (
            "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
            "\n"
            "【排盘信息】\n"
            "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
            "\n"
            f"📊 命盘图片：{true_url}\n"
            "\n"
            + _FEEDBACK_PROMPT)
        # LLM 仿写：自造卡头行 + 正文 + 假图行（两空格前缀无📊，URL 幻觉抄 18:47 旧图）
        # + 假 [/card] + 假页脚 —— 即 id=30 的仿写形态（正文换为真实内容）
        imitation = (
            '[card:paipan title="我的命盘"]\n'
            "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
            "\n"
            "【排盘信息】\n"
            "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
            " 命盘图片：http://124.221.233.214/charts/bazi_20260904_184706.png\n"
            "[/card]\n"
            "\n"
            "———\n"
            "这个分析对你有帮助吗？可回复「准」或「不准」告诉我")
        body_expected = (
            "行，我给你重新排一次。你核对下时辰和出生地有没有记错。\n"
            "\n"
            "【排盘信息】\n"
            "出生时间：己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）")
        with patch("src.llm.client.deepseek_anthropic_completion",
                   return_value=imitation):
            out = h._polish_with_engine_draft("帮我重新排一下盘", "u1", draft)

        assert out == (body_expected + "\n\n"
                       + f"📊 命盘图片：{true_url}\n\n"
                       + _FEEDBACK_PROMPT)
        assert "[card:" not in out
        assert "[/card]" not in out
        assert out.count("命盘图片") == 1           # 假图行剥净，真图行恰一次
        assert "184706" not in out                  # 旧假图 URL 幻觉不落库
        assert out.count(true_url) == 1
        assert out.count("有帮助吗") == 1           # tail 恰一次
        assert out.endswith("告诉我")

    def test_polish_clean_output_untouched(self):
        """LLM 正常输出（无仿写）→ 行为不变：正文 + 真图行 + tail。"""
        h = _polish_harness()
        true_url = "http://124.221.233.214/charts/bazi_20260905_092308.png"
        draft = (
            "己卯年 己巳月 乙丑日 辛巳时（1999年5月13日9时，吉林长春，男）\n"
            f"📊 命盘图片：{true_url}\n"
            "\n"
            + _FEEDBACK_PROMPT)
        clean = "行，我给你重新排一次。你核对下时辰和出生地有没有记错。"
        with patch("src.llm.client.deepseek_anthropic_completion",
                   return_value=clean):
            out = h._polish_with_engine_draft("帮我重新排一下盘", "u1", draft)
        assert out == (clean + "\n\n"
                       + f"📊 命盘图片：{true_url}\n\n"
                       + _FEEDBACK_PROMPT)
        assert out.count("命盘图片") == 1
        assert out.count("有帮助吗") == 1
