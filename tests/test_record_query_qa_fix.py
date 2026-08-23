"""QA 修复回归（qa-12-report）：D1 收藏直读 / D3 晨笺直读 / D4 灵签·名笺直读。

- D1 AC-CHAT-011：'我收藏了什么' 曾因关键词漏配 → 直读 miss → LLM 答
  '收藏夹是空的'；修复：扩收藏关键词。
- D3 AC-CHAT-010：jian_cards 仅推送时刻落库（本机空表）→ 直读 None →
  LLM 重新生成宜忌/私语并编造'幸运提示'；修复：_q_晨笺 空表按当日确定性
  内容现算现存（与 /api/jian/today 同一生成源），输出完整卡内容。
- D4 AC-CONS-007：'抽的灵签/保存过的名笺' 关键词 miss → LLM 编造
  '第42签「风雷益」'/'「青木笺」'；修复：扩关键词 + 签诗按 QIAN_BY_NO 反查。
"""
import os
import sqlite3
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot.record_query import RecordQuery  # noqa: E402


def _rq(tmp_path, **kw):
    from src.storage.chart_dao import ChartDAO
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    from src.storage.session_dao import SessionDAO
    db = str(tmp_path / "r.db")
    return RecordQuery(UserDAO(db), PersonDAO(db), SessionDAO(db),
                       ChartDAO(db), **kw)


# ──────────────────────────────── D1 收藏直读 ────────────────────────────────

def test_favorites_direct_read_qa_phrase(tmp_path):
    """'我收藏了什么'（QA 原句）及口语变体 → 直读返回已存收藏列表。"""
    from src.storage.favorite_dao import FavoriteDAO
    rq = _rq(tmp_path, fav_dao=FavoriteDAO(str(tmp_path / "f.db")))
    rq.fav_dao.add("u1", "chat", "1477", "我的八字排盘分析")
    rq.fav_dao.add("u1", "jian", "2026-08-23", "晨笺 2026-08-23 己亥日")
    for q in ("我收藏了什么", "我收藏了什么内容？", "我的收藏夹",
              "我收藏了啥", "看看我的收藏"):
        out = rq.direct_query("u1", q)
        assert out is not None, f"miss: {q}"
        assert "我的八字排盘分析" in out, f"content: {q}"
        assert "你收藏了 2 条内容" in out, f"count: {q}"


def test_favorites_direct_read_type_labels(tmp_path):
    """收藏 type 输出中文标签（对话/晨笺），可读性（AC-CHAT-011 期望列出）。"""
    from src.storage.favorite_dao import FavoriteDAO
    rq = _rq(tmp_path, fav_dao=FavoriteDAO(str(tmp_path / "f.db")))
    rq.fav_dao.add("u1", "chat", "1477", "我的八字排盘分析")
    rq.fav_dao.add("u1", "jian", "2026-08-23", "晨笺 2026-08-23 己亥日")
    out = rq.direct_query("u1", "我收藏了什么")
    assert "对话: 我的八字排盘分析" in out
    assert "晨笺: 晨笺 2026-08-23 己亥日" in out


def test_favorites_direct_read_no_data_returns_none(tmp_path):
    """无收藏 → 返回 None（miss 兜底走正常流程，不答'空的'不算直读缺陷）。"""
    from src.storage.favorite_dao import FavoriteDAO
    rq = _rq(tmp_path, fav_dao=FavoriteDAO(str(tmp_path / "f.db")))
    assert rq.direct_query("u1", "我收藏了什么") is None


# ──────────────────────────────── D3 晨笺直读 ────────────────────────────────

def test_jian_card_direct_read_full_content(tmp_path):
    """已落库晨笺 → 直读返回 干支/宜/忌/私语/金句 完整内容（AC-CHAT-010）。

    卡内容与 QA 落库卡逐字一致（宜[保持好心情/与朋友交流/适度运动]、
    忌[冲动决策/过度消费]、私语'今日诸事,宜缓不宜急'）。
    """
    from src.storage.jian_dao import JianPrefDAO
    jd = JianPrefDAO(sqlite3.connect(str(tmp_path / "j.db")))
    jd.save_card("u1", "2026-08-23", {
        "date": "2026-08-23", "day_ganzhi": "己亥",
        "suitable": ["保持好心情", "与朋友交流", "适度运动"],
        "unsuitable": ["冲动决策", "过度消费"],
        "quote": "静水流深", "book": "《周易》",
        "private_line": "今日诸事,宜缓不宜急。",
    })
    rq = _rq(tmp_path, jian_dao=jd)
    out = rq.direct_query("u1", "我今天收到的晨笺是什么内容？")
    assert out and "己亥" in out
    assert "宜：保持好心情、与朋友交流、适度运动" in out
    assert "忌：冲动决策、过度消费" in out
    assert "私语：今日诸事,宜缓不宜急。" in out
    assert "金句：静水流深（《周易》）" in out
    # 不得含 LLM 编造内容（QA 曾出现'幸运提示'）
    assert "幸运提示" not in out


def test_jian_card_empty_table_builds_deterministically(tmp_path):
    """jian_cards 空表（本机无推送）→ 按当日确定性内容现算现存再直读。

    _q_晨笺 懒加载 src.main._precompute_jian_for 与
    src.engines.jian_private.generate_private_line —— 用假模块替换验证
    '同一生成源现算'路径：内容与卡一致、且落库后可再次直读（不再现算）。
    """
    from src.storage.jian_dao import JianPrefDAO
    jd = JianPrefDAO(sqlite3.connect(str(tmp_path / "j.db")))
    rq = _rq(tmp_path, jian_dao=jd)
    assert jd.get_card("u1") is None  # 空表前提

    fake_main = types.ModuleType("src.main")
    fake_main._precompute_jian_for = lambda d: {
        "date": d, "day_ganzhi": "己亥",
        "suitable": ["保持好心情", "与朋友交流", "适度运动"],
        "unsuitable": ["冲动决策", "过度消费"],
        "quote": "静水流深", "book": "《周易》",
    }
    fake_private = types.ModuleType("src.engines.jian_private")
    fake_private.generate_private_line = lambda uid, category="": "今日诸事,宜缓不宜急。"
    with mock.patch.dict(sys.modules, {"src.main": fake_main,
                                       "src.engines.jian_private": fake_private}):
        out = rq.direct_query("u1", "我今天收到的晨笺是什么内容？")
    assert out and "己亥" in out and "宜：保持好心情" in out
    assert "私语：今日诸事,宜缓不宜急。" in out
    # 已落库：get_card 可读（与 /api/jian/today 同一张卡）
    card = jd.get_card("u1")
    assert card is not None and card["card_json"]["day_ganzhi"] == "己亥"


# ──────────────────────────────── D4 灵签/名笺直读 ────────────────────────────────

def test_qian_direct_read_qa_phrase_with_poem(tmp_path):
    """'我最近抽的灵签是哪一支'（QA 原句）→ 直读 no.3 签号/吉凶/签诗。

    qian_saves 只存 no，签诗按 QIAN_BY_NO 反查（QA 落库 no.3：
    「山径独行莫问程」中平签——不得编造'第42签风雷益'）。
    """
    from src.storage.qian_dao import QianDAO
    q = QianDAO(sqlite3.connect(str(tmp_path / "q.db")))
    q.save("u1", 3)
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我最近抽的灵签是哪一支？")
    assert out and "第3签" in out
    assert "山径独行莫问程" in out  # no.3 签诗首行（QIAN_BY_NO 原文）
    assert "中平签" in out
    assert "风雷益" not in out      # 不得编造不存在的签


def test_ming_direct_read_qa_phrase(tmp_path):
    """'我保存过的名笺是哪个名字'（QA 原句）→ 直读返回已存名笺。"""
    from src.storage.ming_dao import MingDAO
    m = MingDAO(sqlite3.connect(str(tmp_path / "m.db")))
    m.save("u1", "李", "泓霖", "男", 82)
    rq = _rq(tmp_path, ming_dao=m)
    out = rq.direct_query("u1", "我保存过的名笺是哪个名字？")
    assert out and "李泓霖" in out and "82分" in out
    assert "青木笺" not in out  # QA 曾编造的内容


def test_qian_draw_request_not_hijacked(tmp_path):
    """祈使式抽签请求（帮我抽一支灵签）不得被直读劫持（防过度修复）。"""
    from src.storage.qian_dao import QianDAO
    q = QianDAO(sqlite3.connect(str(tmp_path / "q.db")))
    q.save("u1", 3)
    rq = _rq(tmp_path, qian_dao=q)
    for draw in ("帮我抽一支灵签", "帮我摇个签", "帮我求签"):
        assert rq.direct_query("u1", draw) is None, draw


def test_ming_create_request_not_hijacked(tmp_path):
    """取名动作请求（帮我起个名字）不得被直读劫持（防过度修复）。"""
    from src.storage.ming_dao import MingDAO
    m = MingDAO(sqlite3.connect(str(tmp_path / "m.db")))
    m.save("u1", "李", "泓霖", "男", 82)
    rq = _rq(tmp_path, ming_dao=m)
    for create in ("帮我起个名字", "帮我取个名"):
        assert rq.direct_query("u1", create) is None, create
