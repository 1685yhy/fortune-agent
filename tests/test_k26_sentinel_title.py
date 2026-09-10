# -*- coding: utf-8 -*-
"""k26 哨兵漏面 + 引用标题兜底（k23/k24/k25 审查 Important/Minor 收口）。

依据：
- `.superpowers/sdd/task-k23k24-review.md` I-2（237 条空 title 语料 →《未知》）、
  I-3(a)（万年历面漏过滤 `无` 哨兵）、M-1（计划路径卡片未过滤口径分裂）、
  M-2（忌为空 → 渲染悬空的「忌：」）
- `.superpowers/sdd/task-k25-review.md` I-1（`set_default` 未接写侧镜像漏斗）

真实语料形态取自 `fortune_books_v2` 只读副本（27,115 条）实测：
- 空 title 237 条，与「document 以 ': ' 开头」237 条**一一对应**（入库时
  title + ": " + 正文，title 空 ⇒ 悬空冒号）；元数据键集 = category/title/
  verified，**无 source 键**；命中池 = 奇门 12 类目 + 手相细分 + 混合类目。
- 2026-11-07 `getDayJi()` 第 4 项即哨兵 `无`（任何 4 项截断都躲不过）；
  2026-02-10 zeri 侧忌列表为空（`ji==[]`，M-2 的悬空「忌：」实例）。

隔离：零网络、零 LLM、零生产库、零真实 chroma/bge-m3；chroma 用内存 fake
集合 + stub embedder，其余用真实 lunar-python 与真实 SQLite（tmp_path）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import calendar as _cal  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402

from src.book_categories import ref_text, ref_title  # noqa: E402
from src.rag.retriever import ChunkResult  # noqa: E402

# ── 真实语料形态常量（只读副本实测，勿改）──────────────────────────────
CORPUS_DOC_EMPTY_TITLE = (
    ": 生命线长、深、红润者，生命力强，对疾病抵抗力强；反之，纹浅、弱则体质较弱。"
)
CORPUS_META_EMPTY_TITLE = {"category": "三大主线", "title": "", "verified": True}


def _ymd(ds):
    return tuple(int(x) for x in ds.split("-"))


# ================================================================
# 1. 【I-2】引用标题兜底 —— 空 title 不得伪造成字面书名
# ================================================================

class _StubEmbedder:
    """不加载模型的 embedder（与 tests/test_rag.py 同款）。"""
    dimension = 1024

    def encode(self, texts):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


class _FakeCorpusCollection:
    """chroma 集合最小替身：返回真实语料形态（空 title + 无 source 键）。"""

    def __init__(self, metadatas, documents, ids, scores=(0.8,)):
        self._metas, self._docs, self._ids = list(metadatas), list(documents), list(ids)
        self._scores = list(scores)

    def query(self, query_embeddings=None, n_results=5, where=None, include=None):
        return {
            "ids": [self._ids],
            "distances": [[1 - s for s in self._scores]],
            "metadatas": [self._metas],
            "documents": [self._docs],
        }

    def get(self, where=None, include=None):
        return {"ids": self._ids, "metadatas": self._metas, "documents": self._docs}

    def count(self):
        return len(self._ids)


def _retriever_with(collection):
    """挂 fake 集合的 Retriever（`_collection_checked` 预置 → 不探测真实 chroma）。"""
    from src.rag.retriever import Retriever
    r = Retriever("/tmp", _StubEmbedder())
    r._collection_checked = True
    r._collection = collection
    return r


class TestI2RetrieverNeverFabricatesUnknown:
    """检索器不得把缺失出处伪造成字面量「未知」（I-2 根因）。"""

    def _one_hit_retriever(self, meta=None):
        return _retriever_with(_FakeCorpusCollection(
            [meta if meta is not None else dict(CORPUS_META_EMPTY_TITLE)],
            [CORPUS_DOC_EMPTY_TITLE], ["c1"],
        ))

    def test_vector_search_empty_title_source_not_unknown(self):
        """向量检索：title=``''`` + 库内无 source 键 → source 不得是「未知」。

        旧行为：`meta.get("source","") or title or "未知"` → source="未知" →
        ref_title 返回「未知」→ 引用抽屉《未知》。"""
        out = self._one_hit_retriever()._vector_search("生命线", ("三大主线",), 3, 0.3)
        assert out, "测试前提：fake 集合应有命中"
        assert out[0].source != "未知", f"不得伪造字面书名: {out[0].source!r}"
        assert ref_title(out[0]) == "古籍"

    def test_keyword_search_both_paths_source_not_unknown(self, monkeypatch):
        """关键词检索两条分路（jieba / ImportError 兜底）同样不得注入「未知」。"""
        r = self._one_hit_retriever()
        out = r._keyword_search("生命线", ("三大主线",), 3)
        assert out, "测试前提：jieba 分路应有命中"
        assert all(x.source != "未知" for x in out), f"jieba 分路泄漏: {[x.source for x in out]}"

        monkeypatch.setitem(sys.modules, "jieba", None)   # import jieba → ImportError
        out2 = r._keyword_search("生命线", ("三大主线",), 3)
        assert out2, "测试前提：兜底分路应有命中"
        assert all(x.source != "未知" for x in out2), f"兜底分路泄漏: {[x.source for x in out2]}"

    def test_search_end_to_end_palm_query(self):
        """端到端（手相检索路径）：不再《未知》、正文无悬空冒号。"""
        r = self._one_hit_retriever()
        out = r.search("手相 生命线", category="mianxiang", top_k=3)
        assert out, "测试前提：类目映射后应有命中"
        assert ref_title(out[0]) == "古籍"
        assert not ref_text(out[0]).startswith((":", "：")), (
            f"正文不得以悬空冒号开头: {ref_text(out[0])[:20]!r}")

    def test_real_source_value_still_wins(self):
        """真实存在的 source 不得被本批改动波及（改为空串前先取真值）。"""
        out = self._one_hit_retriever(
            {"category": "三大主线", "title": "", "source": "麻衣神相"})._vector_search(
            "生命线", ("三大主线",), 3, 0.3)
        assert out[0].source == "麻衣神相"
        assert ref_title(out[0]) == "麻衣神相"


class TestI2ContractNormalizesPlaceholder:
    """读取契约把「未知」视同无出处（生产方兜底，防同类生产者复发）。"""

    def _ref(self, title, source):
        return ChunkResult(text="正文", source=source, score=0.8, chunk_id="c",
                           category="三大主线", title=title)

    def test_unknown_literal_treated_as_no_source(self):
        assert ref_title(self._ref("", "未知")) == "古籍"
        assert ref_title(self._ref("未知", "麻衣神相")) == "麻衣神相"
        assert ref_title(self._ref("未知", "未知")) == "古籍"

    def test_dict_and_object_forms_agree(self):
        assert ref_title({"text": "正文", "title": "", "source": "未知"}) == "古籍"
        assert ref_title({"text": "正文"}) == "古籍"

    def test_leading_separator_stripped_only_at_start(self):
        """`: 正文` 剥离；正文中间的冒号原样保留。"""
        assert ref_text(self._ref("", "未知")) == "正文"
        assert ref_text(ChunkResult(text=": 生命线深长", source="", score=.5,
                                    chunk_id="c")) == "生命线深长"
        assert ref_text(ChunkResult(text="男命：身强", source="", score=.5,
                                    chunk_id="c")) == "男命：身强"


class TestI2CitationDrawer:
    """引用抽屉：空 title 条目回落到调用方分类标题（I-2 用户可见面）。"""

    @staticmethod
    def _capture():
        from src.bot.handler import MessageHandler
        captured = {}
        h = MessageHandler.__new__(MessageHandler)
        h._alloc_citations = lambda uid, n: 0
        h._append_citations = lambda uid, items: captured.setdefault("items", items)
        return h, captured

    def _real_corpus_ref(self, source):
        return ChunkResult(text=CORPUS_DOC_EMPTY_TITLE, source=source, score=0.81,
                           chunk_id="c1", category="三大主线", title="")

    def test_caller_category_title_not_dropped(self):
        """title='' + source='未知'（旧注入）→ 调用方标题「相学 · 古籍参考」生效。

        旧行为：ref_title 返回「未知」→ `if src == "古籍"` 不成立 →
        渲染《未知》且调用方标题被丢弃。"""
        h, captured = self._capture()
        h._register_book_citations("u1", [self._real_corpus_ref("未知")],
                                   title="相学 · 古籍参考")
        item = captured["items"][0]
        assert "相学 · 古籍参考" in str(item)
        assert "未知" not in str(item), "不得再出现《未知》"
        assert not item["text"].startswith((":", "：")), "引用正文不得悬空冒号"

    def test_fixed_retriever_shape_also_falls_back(self):
        """修复后 retriever 形态（source='') 同样回落调用方标题。"""
        h, captured = self._capture()
        h._register_book_citations("u2", [self._real_corpus_ref("")],
                                   title="相学 · 古籍参考")
        assert "相学 · 古籍参考" in str(captured["items"][0])
        assert "古籍》" not in str(captured["items"][0])


def test_i2_llm_reference_block_shows_real_title_or_fallback():
    """LLM 参考资料块（client._format_references）不得出现【未知】或【】。"""
    from src.llm.client import FortuneLLM
    refs = [ChunkResult(text=CORPUS_DOC_EMPTY_TITLE, source="", score=0.8,
                        chunk_id="c1", category="三大主线", title=""),
            ChunkResult(text="乙木生于申月", source="古籍", score=0.7,
                        chunk_id="c2", category="bazi_case", title="正官格案例十一")]
    block = FortuneLLM._format_references(object.__new__(FortuneLLM), refs)
    assert "【未知】" not in block and "【】" not in block
    assert "【古籍】" in block          # 无出处 → 契约兜底
    assert "【正官格案例十一】" in block  # title 优先
    assert '": 生命线' not in block      # 悬空冒号不出现在送给 LLM 的依据里


# ================================================================
# 2. 【I-3(a)】万年历路径哨兵 `无` 过滤（与 zeri 同口径）
# ================================================================

class TestI3aWannianliSentinel:
    @staticmethod
    def _engine():
        from src.engines.wannianli import WannianliEngine
        return WannianliEngine()

    def test_zeri_side_reference_still_filters(self):
        """参照侧不回退：zeri._day_yi_ji 仍过滤（同一实现的两侧）。"""
        from src.engines.zeri import ZeriEngine
        r = ZeriEngine().select(2026, 2, 10)
        assert "无" not in r.yi and "无" not in r.ji

    @pytest.mark.parametrize("ds", ["2026-11-07", "2026-02-10"])
    def test_day_detail_anchor_days_no_sentinel(self, ds):
        """审查实测泄漏日：2026-11-07 忌第 4 项即哨兵、2026-02-10 忌第 6 项。"""
        det = self._engine().day_detail(*_ymd(ds))
        assert "无" not in det["yi"] and "无" not in det["ji"], \
            f"{ds} 哨兵泄漏: yi={det['yi']} ji={det['ji']}"

    def test_month_view_short_lists_no_sentinel(self):
        """月视图 yi_short/ji_short（前端宫格直出）同样不得含哨兵。"""
        mv = self._engine().month_view(2026, 11)
        for d in mv["days"]:
            assert "无" not in d["yi_short"] and "无" not in d["ji_short"], d["date"]

    def test_whole_2026_no_leak_and_only_sentinel_differs(self):
        """2026 全年 365 天遍历：无哨兵泄漏，且过滤前后**真实词集合差异仅哨兵**。

        测试侧按万年历既有口径（建除表 + 黄历、「忌优先」消解，I-3(b) 未拍板
        不动）自行复算未过滤结果作对照 —— 差异若超出 `无` 即误杀/漏放。
        """
        from lunar_python import Solar

        from src.engines.wannianli import _zeri
        from src.engines.zeri import JIANCHU_YI_JI

        engine = self._engine()
        ji_sentinel_days, yi_sentinel_days = [], []
        for month in range(1, 13):
            days_in_month = _cal.monthrange(2026, month)[1]
            for d in range(1, days_in_month + 1):
                solar = Solar.fromYmd(2026, month, d)
                lunar = solar.getLunar()
                ec = lunar.getEightChar()
                jieqi = lunar.getJieQi() or ""
                jc = _zeri._calc_jianchu_with_jieqi(
                    ec.getMonth()[1], ec.getDay()[1], jieqi)
                raw_yi = list(dict.fromkeys(
                    list(JIANCHU_YI_JI[jc]["yi"]) + list(lunar.getDayYi())))
                raw_ji = list(dict.fromkeys(
                    list(JIANCHU_YI_JI[jc]["ji"]) + list(lunar.getDayJi())))
                if "无" in raw_yi:
                    yi_sentinel_days.append(lunar.getSolar().toYmd())
                if "无" in raw_ji:
                    ji_sentinel_days.append(lunar.getSolar().toYmd())
                raw_ji_set = set(raw_ji)
                raw_yi_final = [x for x in raw_yi if x not in raw_ji_set]

                det = engine.day_detail(2026, month, d)
                assert "无" not in det["yi"] and "无" not in det["ji"], \
                    f"{det['date']} 哨兵泄漏: yi={det['yi']} ji={det['ji']}"
                # 过滤不得引入新词，且只允许少掉哨兵本身
                assert set(det["yi"]) - set(raw_yi_final) == set()
                assert set(raw_yi_final) - set(det["yi"]) <= {"无"}
                assert set(det["ji"]) - set(raw_ji) == set()
                assert set(raw_ji) - set(det["ji"]) <= {"无"}

        # 测试前提（真实历法锚点）：2026 年忌侧 13 天哨兵、宜侧 0 天
        assert len(ji_sentinel_days) == 13, ji_sentinel_days
        assert yi_sentinel_days == [], yi_sentinel_days
        assert "2026-11-07" in ji_sentinel_days and "2026-02-10" in ji_sentinel_days

    def test_api_day_endpoint_no_sentinel(self):
        """用户可见面直测：GET /api/wannianli/day?date=2026-11-07（审查最小复现）。"""
        from fastapi.testclient import TestClient

        from src.main import app
        from src.security.auth import AuthHandler, JWTHandler, set_auth_handler

        set_auth_handler(AuthHandler())
        token = JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_k26")
        r = TestClient(app).get("/api/wannianli/day?date=2026-11-07",
                                headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "无" not in body["yi"] and "无" not in body["ji"], body["ji"]


# ================================================================
# 3. 【M-1】计划路径卡片：场景评分输入与 select() 同口径（过滤后）
# ================================================================

def _sentinel_yi_solar(real_solar_cls):
    """Solar 代理：getDayYi() 在真实宜列表首位注入哨兵 `无`，其余全部透传。

    M-1 分支在真实历法里不可达：2020-2035 全部 10 个「宜侧哨兵」日（该侧
    getDayYi() 恰为 ['无']）都被卡片的「诸事不宜」排除规则提前拦下，
    _scene_score 永不执行（实测 7 场景 × 10 天均为空）。故用代理在一天可
    进入评分的真实日期上注入哨兵 —— 除哨兵外其余宜忌与真实日历逐项一致，
    排除规则/评分输入因此与真实对照日（2026-10-01）完全相同。
    """
    class _Lunar:
        def __init__(self, real):
            self._real = real

        def getDayYi(self):
            return ["无"] + list(self._real.getDayYi())

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _Solar:
        def __init__(self, real):
            self._real = real

        @classmethod
        def fromYmd(cls, y, m, d):
            return cls(real_solar_cls.fromYmd(y, m, d))

        def getLunar(self):
            return _Lunar(self._real.getLunar())

        def __getattr__(self, name):
            return getattr(self._real, name)

    return _Solar


def test_m1_lucky_card_scene_score_uses_filtered_yi(monkeypatch):
    """_build_lucky_card 传给 _scene_score 的宜列表必须已滤哨兵。

    旧行为：`lunar_yi = list(lunar.getDayYi())`（未过滤）→ 哨兵日传入 ['无']，
    与 select() 的过滤口径分裂（同一事实源两套口径）。"""
    from src.engines import zeri as zeri_mod

    engine = zeri_mod.ZeriEngine()
    calls = []
    orig = engine._scene_score

    def spy(cfg, jianchu, yi):
        calls.append(list(yi))
        return orig(cfg, jianchu, yi)

    monkeypatch.setattr(engine, "_scene_score", spy)
    monkeypatch.setattr(zeri_mod, "Solar",
                        _sentinel_yi_solar(zeri_mod.Solar))
    engine._build_lucky_card(date(2026, 10, 1), zeri_mod.SCENES["搬家"], None, True)

    from lunar_python import Solar as _RealSolar
    real_yi = list(_RealSolar.fromYmd(2026, 10, 1).getLunar().getDayYi())
    assert real_yi, "测试前提：2026-10-01 真实宜列表非空"
    assert calls, "测试前提失效：_scene_score 未被调用（该日应可进入评分）"
    assert "无" not in calls[0], f"卡片侧未过滤哨兵（与 select() 两套口径）: {calls[0]}"
    assert calls[0] == real_yi, (
        f"过滤不得误杀/改动真实宜词: {calls[0]} vs {real_yi}")


# ================================================================
# 4. 【M-2】空宜/空忌不渲染悬空行（不新造文案）
# ================================================================

def test_m2_format_zeri_chart_empty_ji_omits_line():
    """chat 择日分析正文（handler._format_zeri_chart）：2026-02-10 忌=[] → 无「忌：」行。

    旧行为：`f"忌：{'、'.join(r.ji)}"` → 输出悬空的「忌：」。"""
    from src.bot.handler import MessageHandler
    from src.engines.zeri import ZeriEngine

    r = ZeriEngine().select(2026, 2, 10)
    assert r.ji == [], f"测试前提：2026-02-10 忌应为空，实际 {r.ji}"
    out = MessageHandler._format_zeri_chart(object.__new__(MessageHandler), r,
                                            2026, 2, 10)
    assert "宜：" in out and "忌：" not in out, out


def test_m2_engine_citation_omits_empty_ji_segment():
    """引擎来源引用卡（_do_zeri_analysis）：忌为空时该段整段不出现，宜段照常。"""
    from unittest.mock import Mock

    from src.bot.handler import MessageHandler
    from src.engines.zeri import ZeriEngine

    real = ZeriEngine().select(2026, 2, 10)
    assert real.ji == []
    h = MessageHandler.__new__(MessageHandler)
    h.zeri_engine = Mock()
    h.zeri_engine.select.return_value = real
    h.dao = Mock()
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.llm = Mock()
    h.llm.analyze.return_value = Mock(response="分析结果")
    h._emit_stream_event = lambda *a, **kw: None
    h._mark_card_turn = lambda *a, **kw: None
    captured = {}
    h._register_engine_citation = lambda uid, text, **kw: captured.update(text=text)

    h._do_zeri_analysis((2026, 2, 10), "嫁娶", "2026年2月10日", "u_k26")

    assert captured.get("text"), "测试前提：引擎引用卡应生成"
    assert "宜：" in captured["text"] and "忌：" not in captured["text"], captured["text"]


def _bare_zeri_tool_handler(engine):
    """最小装配的 handler：只跑 _tool_zeri 渲染段（其余依赖打桩）。"""
    from src.bot.handler import MessageHandler
    h = MessageHandler.__new__(MessageHandler)
    h.zeri_engine = engine
    h.member_dao = None                    # _check_quota → 无限额
    h._extract_zeri_scene = lambda p: "搬家"
    h._has_zeri_intent = lambda p: True
    h._extract_zeri_exclude_dates = lambda p: None
    h._extract_window = lambda p: ("2026-02-07", "2026-02-14")
    h._map_user_bazi_for_zeri = lambda uid: None
    h._consume_quota = lambda uid: None
    return h


def test_m2_plan_path_card_empty_ji_omits_line():
    """计划路径卡片（_tool_zeri）：卡片忌为空 → 不渲染「忌：」，宜行照常。"""
    from src.engines.zeri import LuckyDayCard
    from unittest.mock import Mock

    card = LuckyDayCard(date="2026-02-10", lunar_text="农历正月廿三 乙卯日",
                        yi=["嫁娶", "开市"], ji=[], jishi="巳时(9-11点)",
                        xi_fangwei="正南", cai_fangwei="西南", scene_score=20,
                        personal_score=24, practical_score=0, total=44,
                        reason_source="成日值日")
    engine = Mock()
    engine.select_lucky_days.return_value = {"cards": [card], "scanned": 8,
                                             "suggest_wider": True, "reason": "窗口不足"}
    out = _bare_zeri_tool_handler(engine)._tool_zeri("搬家 2026年2月", "u_k26")
    assert out.ok, out.text
    assert "宜：嫁娶、开市" in out.text
    assert "忌：" not in out.text, out.text


def test_m2_plan_path_card_with_ji_still_renders():
    """正对照：忌非空时「忌：」行照常渲染（不得把整行误删）。"""
    from src.engines.zeri import LuckyDayCard
    from unittest.mock import Mock

    card = LuckyDayCard(date="2026-02-13", lunar_text="农历正月廿六 戊午日",
                        yi=["订婚"], ji=["动土", "作灶"], jishi="巳时(9-11点)",
                        xi_fangwei="正南", cai_fangwei="西南", scene_score=20,
                        personal_score=24, practical_score=0, total=44,
                        reason_source="成日值日")
    engine = Mock()
    engine.select_lucky_days.return_value = {"cards": [card], "scanned": 8,
                                             "suggest_wider": False, "reason": None}
    out = _bare_zeri_tool_handler(engine)._tool_zeri("搬家 2026年2月", "u_k26")
    assert "忌：动土、作灶" in out.text, out.text


# ================================================================
# 5. 【k25 I-1】set_default 接写侧镜像漏斗
# ================================================================

PERSON_A = {"gender": "女", "birth_year": 1999, "birth_month": 3,
            "birth_day": 28, "birth_hour": 9, "birth_minute": None,
            "calendar": "solar", "city": "长春"}
PERSON_B = {"gender": "男", "birth_year": 1968, "birth_month": 8,
            "birth_day": 8, "birth_hour": 10, "birth_minute": 0,
            "calendar": "solar", "city": "北京"}


def _db(tmp_path):
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    db = str(tmp_path / "k26.db")
    return UserDAO(db), PersonDAO(db), db


def _legacy_bazi_info(**over):
    d = {"year": 1999, "month": 3, "day": 28, "hour": 9, "minute": 0,
         "city": "长春", "gender": "女"}
    d.update(over)
    return d


def test_k25i1_set_default_mirrors_new_default_person(tmp_path):
    """提升 B 为默认 → ② 源（users.bazi_info）立即换成 B 的出生数据。

    旧行为：set_default 只写 persons（不镜像）→ bazi_info 仍镜像 A →
    窗口期内直读 ② 源的消费点拿到旧默认档案。"""
    from src.storage.dao import UserDAO

    dao, pdao, db = _db(tmp_path)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_A))
    b = pdao.create_person("u1", name="爸", relation="父母", birth=dict(PERSON_B))
    dao.save_user_bazi("u1", _legacy_bazi_info())        # ② 源镜像 A

    assert pdao.set_default("u1", b["id"]) is True
    row = dao.get_user_bazi("u1")
    assert row["year"] == 1968 and row["gender"] == "男", row
    assert row["city"] == "北京" and row["calendar"] == "solar"
    assert row["solar_time"] == 1                        # 写侧镜像契约（k25）
    assert "bazi" not in row                             # k8：四柱键不入 ② 源
    # 下一次读路径自愈判定为「非 stale」→ 零写（收敛）
    assert UserDAO(db).get_user_bazi("u1") == row


def test_k25i1_set_default_without_birth_year_keeps_bazi_info(tmp_path):
    """提升「无出生年」命主为默认 → 不得以空 payload 清空既有 ② 源。"""
    from src.storage.dao import UserDAO

    dao, pdao, db = _db(tmp_path)
    pdao.create_person("u2", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_A))
    placeholder = pdao.create_person("u2", name="宝宝", relation="子女",
                                     birth={"gender": "男"})   # 无出生年
    assert placeholder["birth_year"] is None
    dao.save_user_bazi("u2", _legacy_bazi_info())

    assert pdao.set_default("u2", placeholder["id"]) is True
    row = dao.get_user_bazi("u2")
    assert row["year"] == 1999, "无出生年默认行不得清空 ② 源"
    assert UserDAO(db).get_user_bazi("u2")["year"] == 1999


def test_k25i1_set_default_mirror_failure_does_not_break_return(tmp_path,
                                                                monkeypatch):
    """镜像失败（返回 False）→ set_default 仍成功返回（失败仅告警不抛）。"""
    from src.storage import person_dao as pd

    _, pdao, _ = _db(tmp_path)
    pdao.create_person("u3", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_A))
    b = pdao.create_person("u3", name="爸", relation="父母", birth=dict(PERSON_B))
    monkeypatch.setattr(pd, "mirror_bazi_info_to_users",
                        lambda *a, **kw: False)
    assert pdao.set_default("u3", b["id"]) is True
    assert pdao.get_default_person("u3")["id"] == b["id"]


def test_k25i1_set_default_row_missing_returns_false(tmp_path):
    """归属/存在校验语义不变：非本人命主 → False（且不触发镜像）。"""
    _, pdao, _ = _db(tmp_path)
    pdao.create_person("u4", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_A))
    b = pdao.create_person("u4", name="爸", relation="父母", birth=dict(PERSON_B))
    assert pdao.set_default("other_user", b["id"]) is False
    assert pdao.set_default("u4", "no-such-id") is False


# ================================================================
# 6. 【审查 I-1】解梦路径同样走 ref_text 契约（k26 ② 未闭环收口）
# ================================================================
# 审查实测：`src/engines/dream.py:312` 直存原始 `r.text`（绕过本批新增的
# `ref_text` 契约）→ 命中空 title 语料（27k 集合含 237 条 `": 正文"` 形态，
# 且解梦检索不设 category）时用户面仍出现悬空冒号：
#   ① 工具引用抽屉（handler 2562）与工具正文行（2565）；
#   ② chat 引用抽屉（7716-7721）、送 LLM 的 prompt 块（7764-7765）、
#      回复「📖 古籍记载：」（7776-7778）；③ dream.format_dream_prompt（512）。
# 修在源头（interpretations 构造处走契约）→ 上列消费点全部收敛，
# 不在各处复制剥离逻辑（单一实现铁律）。

# 真实语料形态（只读副本实测的 237 条之一样本，勿改）
DREAM_DOC_HANGING_COLON = (
    ": 生命线长、深、红润者，生命力强，对疾病抵抗力强；反之，纹浅、弱则体质较弱。"
)
DREAM_DOC_CLEAN = "生命线长、深、红润者，生命力强，对疾病抵抗力强；反之，纹浅、弱则体质较弱。"


def _dream_stub_retriever(texts):
    """解梦检索器替身：返回空 title 语料形态的 ChunkResult（零检索/零网络）。"""
    class _R:
        def search(self, query, top_k=5, **kw):
            return [
                ChunkResult(text=t, source="", score=0.9 - i * 0.01,
                            chunk_id=f"c{i}", category="三大主线", title="")
                for i, t in enumerate(texts)
            ]
    return _R()


def _bare_dream_handler(texts):
    """最小装配的解梦 handler（只跑 _tool_dream 渲染段，其余打桩）。"""
    from src.bot.handler import MessageHandler
    from src.engines.dream import DreamEngine
    from unittest.mock import Mock

    h = MessageHandler.__new__(MessageHandler)
    h.dream_engine = DreamEngine()
    h._get_dream_retriever = lambda: _dream_stub_retriever(texts)
    h.llm = Mock()
    h.llm.api_key = ""
    h.dao = Mock()
    h._citations = {}
    return h


def test_review_i1_dream_interpretations_follow_ref_text_contract():
    """引擎面：interpretations 必须走 ref_text 契约（剥行首悬空冒号）。

    旧行为：`interpretations=[r.text for r in all_results[:15]]` 直存原始
    r.text → 本用例首项以 `": "` 开头（悬空冒号原样进入全部下游）。"""
    from src.engines.dream import DreamEngine

    res = DreamEngine().analyze("梦见手",
                                _dream_stub_retriever([DREAM_DOC_HANGING_COLON]))
    assert res.interpretations, "测试前提：检索结果应进入 interpretations"
    assert res.interpretations[0] == DREAM_DOC_CLEAN, res.interpretations[:1]
    assert not res.interpretations[0].startswith(":")


def test_review_i1_dream_tool_face_no_hanging_colon():
    """工具面：引用抽屉 text 与工具正文行均不得以悬空冒号开头。"""
    h = _bare_dream_handler([DREAM_DOC_HANGING_COLON])
    out = h._tool_dream("梦见手", "u_k26")

    assert out.ok, out.text
    assert ": 生命线" not in out.text, out.text
    assert DREAM_DOC_CLEAN[:20] in out.text
    drawer = h.pop_citations("u_k26")
    assert drawer, "测试前提：应注册 book 引用"
    assert drawer[0]["text"] == DREAM_DOC_CLEAN, drawer[0]["text"]
    assert not drawer[0]["text"].startswith(":")


def test_review_i1_dream_chat_and_prompt_faces_no_hanging_colon(monkeypatch):
    """chat 面：引用抽屉 / LLM prompt 块 / 📖 回复 三处一并收敛。

    （handler 7716-7721 / 7764-7765 / 7776-7778 与 dream.format_dream_prompt
    共用同一份 interpretations，源头收口即全族收敛。）"""
    from src.engines.dream import DreamEngine, format_dream_prompt

    res = DreamEngine().analyze("梦见手",
                                _dream_stub_retriever([DREAM_DOC_HANGING_COLON]))

    # 7764-7765：送 LLM 的依据块（_format_dream_for_llm 形态）
    from src.bot.handler import MessageHandler
    h = MessageHandler.__new__(MessageHandler)
    llm_block = h._format_dream_for_llm(res, "梦见手")
    # 7776-7778：最终回复
    reply = h._format_dream_response("梦见手", res, "AI解读正文")
    # dream.py:512-515：prompt 注入
    prompt = format_dream_prompt("梦见手", res)

    for name, blob in (("llm_block", llm_block), ("reply", reply),
                       ("prompt", prompt)):
        for line in blob.splitlines():
            assert ": 生命线" not in line, f"{name} 悬空冒号漏面: {line!r}"
        assert "📖 古籍记载：\n  1. :" not in blob, blob


# ================================================================
# 7. 【审查 I-2】delete_person 提升默认命主接写侧镜像漏斗（同类一并修）
# ================================================================
# 审查实测：`delete_person`（删默认命主 → 裸 SQL 提升剩余最早者）未接 k25
# 写侧镜像漏斗 → ② 源 users.bazi_info 在「读路径自愈」前仍持**已删除命主**
# 的档案（与本批 set_default 同源缺陷，属 k25 审查 I-1 同类第 4 条）。
# 语义必须与既有漏斗一致：无出生年不镜像（不清空既有 ② 源）、失败仅告警不抛。

def test_review_i2_delete_default_person_mirrors_promoted(tmp_path):
    """删除默认命主 → ② 源立即换成被提升者的出生数据。

    旧行为：delete_person 只写 persons（不镜像）→ bazi_info 仍是已删除的 A
    （1968 != 1999，本用例必失败）→ 窗口期内直读 ② 源的消费点拿到已删档案。"""
    dao, pdao, db = _db(tmp_path)
    a = pdao.create_person("u5", name="我", relation="自己", is_default=True,
                           birth=dict(PERSON_A))
    b = pdao.create_person("u5", name="爸", relation="父母",
                           birth=dict(PERSON_B))
    dao.save_user_bazi("u5", _legacy_bazi_info())        # ② 源 = 已删除的 A

    assert pdao.delete_person("u5", a["id"]) is True
    assert pdao.get_default_person("u5")["id"] == b["id"], "剩余最早者应被提升"
    row = dao.get_user_bazi("u5")
    assert row["year"] == 1968 and row["gender"] == "男", row
    assert row["city"] == "北京" and row["calendar"] == "solar"
    assert row["solar_time"] == 1                        # 写侧镜像契约（k25）
    assert "bazi" not in row                             # k8：四柱键不入 ② 源
    # 下一次读路径自愈判定为「非 stale」→ 零写（收敛）
    from src.storage.dao import UserDAO
    assert UserDAO(db).get_user_bazi("u5") == row


def test_review_i2_delete_default_person_without_birth_year_keeps_bazi_info(
        tmp_path):
    """被提升者无出生年 → 不得以空 payload 清空既有 ② 源。"""
    dao, pdao, _ = _db(tmp_path)
    a = pdao.create_person("u6", name="我", relation="自己", is_default=True,
                           birth=dict(PERSON_A))
    placeholder = pdao.create_person("u6", name="宝宝", relation="子女",
                                     birth={"gender": "男"})    # 无出生年
    assert placeholder["birth_year"] is None
    dao.save_user_bazi("u6", _legacy_bazi_info())

    assert pdao.delete_person("u6", a["id"]) is True
    row = dao.get_user_bazi("u6")
    assert row["year"] == 1999, "无出生年默认行不得清空 ② 源"
    assert row["gender"] == "女"


def test_review_i2_delete_last_person_keeps_bazi_info(tmp_path):
    """删光命主 → 保留既有 ② 源（既有语义：下次访问据此自动重建默认）。"""
    dao, pdao, _ = _db(tmp_path)
    a = pdao.create_person("u7", name="我", relation="自己", is_default=True,
                           birth=dict(PERSON_A))
    dao.save_user_bazi("u7", _legacy_bazi_info())

    assert pdao.delete_person("u7", a["id"]) is True
    assert pdao.list_persons("u7") == []
    row = dao.get_user_bazi("u7")
    assert row["year"] == 1999 and row["gender"] == "女", row


def test_review_i2_delete_default_person_mirror_failure_does_not_break(
        tmp_path, monkeypatch):
    """镜像失败（返回 False）→ delete_person 仍成功返回（失败仅告警不抛）。

    同时钉住「确实调用了镜像」：旧代码（未接线）calls == [] → 断言失败。"""
    from src.storage import person_dao as pd

    _, pdao, _ = _db(tmp_path)
    a = pdao.create_person("u8", name="我", relation="自己", is_default=True,
                           birth=dict(PERSON_A))
    b = pdao.create_person("u8", name="爸", relation="父母",
                           birth=dict(PERSON_B))
    calls = []
    monkeypatch.setattr(pd, "mirror_bazi_info_to_users",
                        lambda *args, **kw: calls.append(args) or False)
    assert pdao.delete_person("u8", a["id"]) is True
    assert calls, "删除默认命主应触发写侧镜像（旧代码不接线）"
    assert pdao.get_default_person("u8")["id"] == b["id"]


def test_review_i2_delete_non_default_person_no_mirror(tmp_path, monkeypatch):
    """正对照：删非默认命主 → 默认身份未变，不触发镜像（零多余写）。"""
    from src.storage import person_dao as pd

    _, pdao, _ = _db(tmp_path)
    pdao.create_person("u9", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_A))
    b = pdao.create_person("u9", name="爸", relation="父母",
                           birth=dict(PERSON_B))
    calls = []
    monkeypatch.setattr(pd, "mirror_bazi_info_to_users",
                        lambda *args, **kw: calls.append(args) or True)
    assert pdao.delete_person("u9", b["id"]) is True
    assert calls == [], f"删非默认命主不得镜像（默认未换人）: {calls}"
