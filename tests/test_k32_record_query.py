"""k32 数据链欠账批：灵签收藏直读按签种取诗（A2）+ 会员支付守卫词加固（A17）。

A2（`src/bot/record_query.py` `_q_灵签`）：qian_saves 行含 kind，直读此前只按
`QIAN_BY_NO`（原版 8 支）反查 → 观音/关帝/玄武山收藏显示原版同号签诗（串诗）。
本文件锁住「按签种取诗 + 同号异 kind 不串」。
A17（`_MEMBER_PAY_WORDS`）：补 打折/涨价/降价/促销/优惠券/退/送/拼/抢 穿透矩阵，
纯查询仍直读（守卫只拦「含会员 且 含支付词」，误伤仅降级 LLM）。

隔离：tmp_path 独立 sqlite；无网络/无 LLM。
"""
import os
import sqlite3
import sys

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


def _qian(tmp_path, name="q.db"):
    from src.storage.qian_dao import QianDAO
    return QianDAO(sqlite3.connect(str(tmp_path / name)))


def _card(kind: str, no: int) -> dict:
    """权威签卡（与被测实现同源，避免测试硬编码手抄）。"""
    from src.api.qian import QIAN_KINDS
    return next(c for c in QIAN_KINDS[kind] if c["no"] == no)


# ──────────────────────── A2 灵签直读按签种取诗 ────────────────────────

def test_a2_qian_read_single_kind_no_cross_poem(tmp_path):
    """只收藏观音第 7 签 → 显示观音第 7 签诗；原版第 7 签诗绝不出现（同号不串）。

    旧代码（只查 QIAN_BY_NO）必失败：显示的是原版「枯木逢春再发花」。
    """
    q = _qian(tmp_path)
    q.save("u1", 7, "guanyin")
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我抽的灵签是哪一支？")
    assert out and "第7签" in out
    gy7, orig7 = _card("guanyin", 7), _card("original", 7)
    assert gy7["poem"][0] in out, "须显示观音签诗（对应签种）"
    assert gy7["jx"] in out
    assert orig7["poem"][0] not in out, "同号原版签诗不得串（旧代码必失败）"


def test_a2_qian_read_three_kinds_each_own_poem(tmp_path):
    """观音/关帝/玄武山三种收藏 → 各自签诗；同号异 kind 逐条不串。"""
    q = _qian(tmp_path)
    pairs = [("guanyin", 7), ("guandi", 7), ("xuanwushan", 7),
             ("guandi", 1), ("xuanwushan", 51)]
    for kind, no in pairs:
        assert q.save("u1", no, kind) == (True, False)
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我摇过什么签？")
    assert out
    for kind, no in pairs:
        card = _card(kind, no)
        assert card["poem"][0] in out, f"{kind} 第{no}签签诗缺失"


def test_a2_qian_read_same_no_two_kinds_both_shown(tmp_path):
    """同号异 kind 同时收藏（guanyin#7 + original#7）→ 两首签诗都在，互不覆盖。"""
    q = _qian(tmp_path)
    q.save("u1", 7, "guanyin")
    q.save("u1", 7, "original")
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我抽的签是哪些")
    assert _card("guanyin", 7)["poem"][0] in out
    assert _card("original", 7)["poem"][0] in out


def test_a2_qian_read_unknown_kind_legacy_row_no_crash(tmp_path):
    """历史/脏 kind（如手工直写 'legacy'）→ 不崩、不丢条数，回退原版签卡。"""
    q = _qian(tmp_path)
    q.conn.execute(
        "INSERT INTO qian_saves (user_id, no, kind, drawn_at) VALUES (?,?,?,?)",
        ("u1", 3, "legacy_kind", 1.0))
    q.conn.commit()
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我的签是哪几支")
    assert out and "第3签" in out
    assert _card("original", 3)["poem"][0] in out, "未知签种回退原版签卡"


def test_a2_qian_read_original_kind_unchanged(tmp_path):
    """原版签直读输出零变化（不带签种后缀，锁定既有口径）。"""
    q = _qian(tmp_path)
    q.save("u1", 3, "original")
    rq = _rq(tmp_path, qian_dao=q)
    out = rq.direct_query("u1", "我抽的签是哪支")
    assert "第3签（中平签）「山径独行莫问程一程烟雨一程晴他年若到桃源口再看落花听水声」" in out


# ──────────────────────── A17 支付守卫词穿透矩阵 ────────────────────────

def _rq_with_member(tmp_path, plan="basic"):
    from src.storage.member_dao import MemberDAO
    m = MemberDAO(str(tmp_path / "m.db"))
    m.create_membership("u1", plan, queries_limit=999)
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    conn.execute("UPDATE memberships SET queries_used = 2 WHERE user_id = 'u1'")
    conn.commit()
    conn.close()
    return _rq(tmp_path, member_dao=m)


def test_a17_new_pay_words_in_table():
    """8 词全部进守卫表（结构化断言，锁定补词不回退）。"""
    from src.bot.record_query import _MEMBER_PAY_WORDS
    for w in ("打折", "涨价", "降价", "促销", "优惠券", "退", "送", "拼", "抢"):
        assert w in _MEMBER_PAY_WORDS, w


def test_a17_new_pay_words_blocked(tmp_path):
    """新增词穿透矩阵全锁 None（有会员记录也不直读档位 dump，走支付引导全流程）。

    句例刻意只命中新词（不含既有支付词），保证「旧代码必失败」的可区分性；
    优惠券一词被既有「优惠」子串覆盖（无独立可区分句例），由上方结构化断言锁。
    """
    rq = _rq_with_member(tmp_path)
    for pay_sent in ("会员打折吗", "会员什么时候涨价", "会员降价了吗",
                     "会员有促销活动吗", "会员怎么退", "会员能送人吗",
                     "会员拼团怎么拼", "会员秒杀怎么抢"):
        assert rq.direct_query("u1", pay_sent) is None, pay_sent


def test_a17_pure_query_still_direct(tmp_path):
    """纯账户查询不受新增词误伤：仍直读档位秒回。"""
    rq = _rq_with_member(tmp_path)
    for pure in ("我的会员是什么", "我花了多少钱", "我的会员等级是啥"):
        out = rq.direct_query("u1", pure)
        assert out and "会员档位" in out, pure
