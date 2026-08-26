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


# ──────────────────── 批次 2 B3-26/27：档案守卫补词 + 时分 int 归一 ────────────────────

def _rq_with_person(tmp_path, birth):
    """建好默认命主（birth 键名 birth_year/birth_month/…，与原存档路径同口径）的 _rq。"""
    rq = _rq(tmp_path)
    rq.person_dao.create_person("u1", "测试", "自己", birth=birth)
    return rq


def test_archive_guard_new_words_nong_gao(tmp_path):
    """B3-26：'弄/搞' 补入档案动作守卫——祈使式建档/改档句不得被 _q_档案 档位 dump 劫持。

    修前"帮我弄下生日档案/搞一份档案"等口语动作句（_MEMBER_PAY_WORDS 早已封口
    弄/搞，本表未补）会命中"档案/生日"关键词 → 档位 dump 短路答非所问。
    """
    rq = _rq_with_person(tmp_path, {"birth_year": 1990, "birth_month": 5,
                                    "birth_day": 20, "gender": "男"})
    for q in ("帮我弄下生日档案", "帮我搞个档案", "把生日弄到档案里",
              "搞一份我的出生档案", "我的生日弄错了，帮我改下"):
        assert rq.direct_query("u1", q) is None, f"被档案直读劫持: {q}"


def test_archive_guard_new_words_pure_query_unaffected(tmp_path):
    """B3-26：纯查询句不落 弄/搞 → 档案直读照常秒回（守卫误伤面为 0）。"""
    rq = _rq_with_person(tmp_path, {"birth_year": 1990, "birth_month": 5,
                                    "birth_day": 20, "birth_hour": 15,
                                    "birth_minute": 0, "city": "北京", "gender": "男"})
    for q in ("我的生日是哪天", "我的出生年月日", "什么时候出生"):
        out = rq.direct_query("u1", q)
        assert out is not None and "1990年5月20日" in out, q


def _insert_dirty_person(tmp_path, birth_json: str):
    """绕过 DAO 归一（写路径必转 int），直插脏 birth_enc 模拟历史/手工脏库。"""
    import json as _json
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(tmp_path / "r.db"))
    conn.execute(
        "INSERT INTO persons (user_id, name, relation, is_default, birth_enc,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("u1", "测试", "自己", 1, birth_json, "2026-08-01", "2026-08-01"))
    conn.commit()
    conn.close()


def test_q_archive_dirty_minute_str_fail_open_formats(tmp_path):
    """B3-27：DB 脏数据（birth_hour='15'/birth_minute='5' 字符串）→ 归一 int 照常格式化。

    修前 f"{minute:02d}" 对 str 炸 TypeError，异常冒泡到 direct_query 的大 try
    把所有类目直读一并禁用（连坐）；农历问法追加也因 str 时分被 lunar-python
    拒掉（丢农历日期）。
    """
    import json as _json
    rq = _rq(tmp_path)
    _insert_dirty_person(tmp_path, _json.dumps(
        {"gender": "男", "birth_year": 1990, "birth_month": 5, "birth_day": 20,
         "birth_hour": "15", "birth_minute": "5", "calendar": "solar", "city": "北京"},
        ensure_ascii=False))
    out = rq._q_档案("u1")
    assert out is not None
    assert "1990年5月20日15:05" in out      # '5' → int 5 → :02d '05'
    # 农历问法：归一后的 int 时分让 lunar-python 转换成功（1990-05-20 → 四月廿六）
    out2 = rq._q_档案("u1", "农历")
    assert "· 农历四月廿六" in out2


def test_q_archive_dirty_garbage_minute_fail_open_none(tmp_path):
    """B3-27：分钟/小时为不可转数字脏数据（'未知'）→ fail-open 按「无时分」展示，不崩。"""
    import json as _json
    rq = _rq(tmp_path)
    _insert_dirty_person(tmp_path, _json.dumps(
        {"gender": "女", "birth_year": 2000, "birth_month": 1, "birth_day": 2,
         "birth_hour": "未知", "birth_minute": "未知", "calendar": "solar", "city": ""},
        ensure_ascii=False))
    out = rq._q_档案("u1")
    assert out is not None
    assert "2000年1月2日" in out and "时" not in out   # 无时分段，不炸
