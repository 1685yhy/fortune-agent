"""B3-2-C：回复底部技术性 footer（解读版本/生成时间/同一八字）移除锁定。

用户问题（第 3 项）：「最底下还会显示同一八字什么结果一样啊…然后还有什么版本…
这个为什么显示它呢？」——footer 对普通用户无意义且突兀。

行为变化（预期，非破坏）：
- 排盘/测算回复不再追加 get_version_footer 页脚（主路径 _do_bazi_analysis
  与降级链 _do_bazi_lite 两处 append 点随函数一并移除）
- 前端顶部静态免责行「内容由 AI 生成 · 仅供娱乐参考」（chat.wxml chat-disclaimer）
  保留，不受本改动影响
- 服务端 polish 页脚剥离正则 / card_mark.split_tail 页脚模式保留为防御性
  no-op（存量消息仍可正确切分，机制测试零改动通过）
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from src.bot.handler import MessageHandler, _FEEDBACK_PROMPT_EMOJI  # noqa: E402


def test_version_footer_module_removed():
    """reading_version 模块整体移除（仅 handler 两处 append 点引用，无他用）。

    防复活：任何人恢复页脚须同时恢复模块与两处调用——本断言先行拦截。
    """
    assert importlib.util.find_spec("src.reading_version") is None


def _lite_harness():
    """_do_bazi_lite 最小装配：落库/引用桩替身，正文链路真实执行。"""
    h = object.__new__(MessageHandler)
    h.memory = None
    h.session_dao = None
    h.chart_dao = None
    h.preference_dao = None
    h._analysis_facts = {}
    h._card_turn = None
    h._citations = {}
    h._alloc_citations = lambda uid, n: 0
    h._append_citations = lambda *a, **k: None
    h._save_bazi_records = lambda *a, **k: None
    return h


def test_bazi_lite_reply_has_no_version_footer():
    """降级链排盘回复：末尾为反馈提示，无「解读版本/同一八字/生成时间」页脚。"""
    h = _lite_harness()
    result = SimpleNamespace(
        bazi=["庚", "午", "辛", "巳", "乙", "酉", "甲", "申"],
        day_master="乙", geju="", wuxing={}, shensha=[], yongshen="",
    )
    birth = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
             "city": "北京", "gender": "男"}
    out = h._do_bazi_lite(result, birth, "今年财运如何", "u1")
    assert out
    assert "解读版本" not in out
    assert "同一八字" not in out
    assert "生成时间" not in out
    assert "---" not in out.rsplit("\n\n", 1)[-1]  # 尾部不再是「---\n页脚」结构
    # 反馈提示仍在文末（移除页脚不影响反馈尾）
    assert out.rstrip().endswith(_FEEDBACK_PROMPT_EMOJI)
