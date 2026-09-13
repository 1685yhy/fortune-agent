# -*- coding: utf-8 -*-
"""k43 联网触发语义路由（bge-m3 零成本）——行为区分用例。

三层：
  A 路由内核（stub embedder，确定性向量）：阈值判定 / unknown 回退 / 缓存 /
    异常兜底 / 环境开关——不依赖真实模型，秒级；
  B 判定层接线（stub `semantic_router.route`）：语义 ACCEPT 补搜、语义 VETO
    抑制词表层误触、硬否决不可越过、llm_needs_search 优先、无命名主体护栏；
  C 真机（本地 bge-m3 就绪才跑）：「换个说法的公司问句仍判搜」等语义用例。

改造前基线（main=b7d3ee1）在本文件 A/B 用例下的行为差异见
`.superpowers/sdd/task-k43-report.md` §A/B（tests/test_k43_semantic_ab.py 可复跑）。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.rag import semantic_router  # noqa: E402
from src.rag.search_trigger import SearchDecision, decide_search  # noqa: E402

_EXAMPLES = json.loads(
    (Path(__file__).resolve().parent.parent / "src/rag/semantic_router_examples.json")
    .read_text(encoding="utf-8"))["examples"]

# 示例集锁：改动示例集/阈值必须同步改这些数字（审查可追溯）
# k43-r1：仅溯源标签更正（corpus:insomnia_01→authored:example、T079→T026、
#   T084→T084#prefix，text 未动 → 语义向量矩阵不变）
_EXAMPLES_SHA256 = "b5a0ab73b0febf607ed1bcb1369ee9b982c2574749ca03371d53c3ce1638ed90"
_MIN_PER_CLASS = 40
_MAX_BYTES = 40_000


# ============================================================
# A：路由内核（stub embedder）
# ============================================================

class _StubEmbedder:
    """确定性 4 维向量：正例 → e0，负例 → e1，查询按表给——可解析验证 margin。"""

    dimension = 4

    def __init__(self, table):
        self._table = table
        self.calls = 0

    def encode(self, texts):
        return np.asarray([self._table[t] for t in texts], dtype=np.float32)

    def encode_single(self, text):
        self.calls += 1
        return np.asarray(self._table[text], dtype=np.float32)


def _stub_table(extra=None):
    table = {}
    for e in _EXAMPLES:
        table[e["text"]] = ([1.0, 0.0, 0.0, 0.0] if e["label"] == "search"
                            else [0.0, 1.0, 0.0, 0.0])
    table.update(extra or {})
    return table


@pytest.fixture
def stub_router(tmp_path, monkeypatch):
    """干净的路由状态 + 独立缓存目录（不污染真机向量缓存）。"""
    monkeypatch.setenv("SEMANTIC_ROUTER_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("SEMANTIC_ROUTER_DISABLE", raising=False)
    semantic_router.reset_for_tests()
    yield semantic_router
    semantic_router.reset_for_tests()


class TestRouterCore:
    def test_margin_decision_three_way(self, stub_router):
        """margin 三分：≥TAU_SEARCH → search；≤-TAU_LOCAL → local；中间 → unknown。"""
        emb = _StubEmbedder(_stub_table({
            "接受句": [0.9, 0.4, 0.0, 0.0],     # cos: pos .9 / neg .4 → margin +0.5
            "否决句": [0.4, 0.9, 0.0, 0.0],     # margin -0.5
            "不确定句": [0.7, 0.7, 0.0, 0.0],   # margin 0
        }))
        assert stub_router.install_embedder(emb) is True
        assert stub_router.route("接受句").label == "search"
        assert stub_router.route("否决句").label == "local"
        assert stub_router.route("不确定句").label == "unknown"
        v = stub_router.route("接受句")
        assert v.margin > 0 and v.pos > v.neg

    def test_thresholds_locked(self):
        """阈值=判定口径的单一事实源：改动必须显式（本用例即锁）。"""
        assert (semantic_router.TOP_K, semantic_router.TAU_SEARCH,
                semantic_router.TAU_LOCAL) == (3, 0.01, 0.02)

    def test_unknown_before_ready_and_on_error(self, stub_router):
        """未就绪 → unknown（零行为变化）；编码异常 → unknown 不抛。"""
        assert stub_router.is_ready() is False
        assert stub_router.route("任何问句").label == "unknown"

        class _Boom(_StubEmbedder):
            def encode_single(self, text):
                raise RuntimeError("boom")

        assert stub_router.install_embedder(_Boom(_stub_table())) is True
        assert stub_router.route("炸掉的问句").label == "unknown"

    def test_query_cache_avoids_reencode(self, stub_router):
        """同问句判定按文本缓存：只编码一次（延迟护栏的行为面）。"""
        emb = _StubEmbedder(_stub_table({"缓存问句": [0.9, 0.4, 0.0, 0.0]}))
        stub_router.install_embedder(emb)
        first = stub_router.route("缓存问句")
        calls = emb.calls
        assert stub_router.route("缓存问句") == first
        assert emb.calls == calls

    def test_install_embedder_idempotent(self, stub_router):
        emb = _StubEmbedder(_stub_table({"x": [0.9, 0.4, 0.0, 0.0]}))
        assert stub_router.install_embedder(emb) is True
        before = stub_router.route("x")
        assert stub_router.install_embedder(_StubEmbedder(_stub_table())) is True
        assert stub_router.route("x") == before

    def test_disable_env_short_circuits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTER_DISABLE", "1")
        monkeypatch.setenv("SEMANTIC_ROUTER_CACHE", str(tmp_path / "c"))
        semantic_router.reset_for_tests()
        try:
            assert semantic_router.route("帮我查一下苹果公司的最新新闻").label == "unknown"
            assert semantic_router.warmup(blocking=False) is False
        finally:
            semantic_router.reset_for_tests()

    def test_no_sync_model_load_in_pytest(self, monkeypatch):
        """单测进程默认不触发后台模型加载（否则内存/时序不可控）。"""
        monkeypatch.delenv("SEMANTIC_ROUTER_PRELOAD", raising=False)
        assert semantic_router._preload_allowed() is False
        monkeypatch.setenv("SEMANTIC_ROUTER_PRELOAD", "1")
        assert semantic_router._preload_allowed() is True

    def test_preload_does_not_block_request_path(self, stub_router):
        """后台预加载期间（示例矩阵编码 ~1-2s）请求路径不得被长锁阻塞。"""
        import threading
        import time

        class _Slow(_StubEmbedder):
            def encode(self, texts):
                time.sleep(0.6)          # 模拟示例集编码耗时
                return super().encode(texts)

        done = threading.Event()

        def _install():
            stub_router.install_embedder(_Slow(_stub_table()))
            done.set()

        t = threading.Thread(target=_install)
        t.start()
        time.sleep(0.1)                  # 编码进行中
        t0 = time.time()
        v = stub_router.route("预加载期间的问句")
        elapsed = time.time() - t0
        t.join()
        assert v.label == "unknown"      # 未就绪 → 回词表层
        assert elapsed < 0.3, elapsed    # 不被长锁阻塞
        assert done.is_set()

    def test_reuses_loaded_faiss_embedder(self, stub_router, monkeypatch, tmp_path):
        """复用路径：**本进程已先加载 FAISS 检索器**时直接复用其实例（否则自建一份）。

        k43-r1（审查 Important-4）：该路径只在「FAISS 先加载」时成立；常规时序下
        语义路由先加载 → 复用不成立（且本机未装 faiss-cpu → `loaded_embedder`
        恒 None）。因此不宣称「省 ~2GB」，只锁「有得复用时确实复用」的行为。
        """
        emb = _StubEmbedder(_stub_table({"复用问句": [0.9, 0.4, 0.0, 0.0]}))
        fake_retriever = type("R", (), {"loaded_embedder": emb})()
        import src.rag.faiss_retriever as fr
        monkeypatch.setattr(fr, "get_faiss_retriever", lambda index_dir=None: fake_retriever)
        assert stub_router.warmup(blocking=True) is True
        assert stub_router.route("复用问句").label == "search"
        assert stub_router._EMBEDDER is emb          # 复用的是同一实例（未自建）

    def test_warmup_retries_after_failed_latch(self, stub_router, monkeypatch):
        """k43-r1（审查 Minor-3）：失败闩死后，显式 warmup() 清闩重试一次。

        背景：`_LOAD_FAILED=True` 后进程内不再自动重试（避免后台反复拉 40s 冷加载），
        原先连运维显式预热也被闩死 → 只能重启进程。
        """
        emb = _StubEmbedder(_stub_table({"重试问句": [0.9, 0.4, 0.0, 0.0]}))
        monkeypatch.setattr(stub_router, "_acquire_embedder", lambda: emb)
        with stub_router._LOCK:
            stub_router._LOAD_FAILED = True
            stub_router._LOAD_STARTED = True
        assert stub_router.warmup(blocking=True) is True      # 清闩 → 重试成功
        assert stub_router._LOAD_FAILED is False
        assert stub_router.route("重试问句").label == "search"

    def test_query_cache_is_lru_and_private_dir(self, stub_router, monkeypatch, tmp_path):
        """k43-r1（审查 Minor-6）：文本缓存满时淘汰最久未用（不再整清）；
        （Minor-2）默认缓存目录在用户私有路径，落盘目录 0700。"""
        monkeypatch.setattr(stub_router, "_QUERY_CACHE_MAX", 3)
        table = {f"缓存问句{i}": [0.9, 0.4, 0.0, 0.0] for i in range(4)}
        stub_router.install_embedder(_StubEmbedder(_stub_table(table)))
        for i in range(3):
            stub_router.route(f"缓存问句{i}")
        stub_router.route("缓存问句0")                 # 触达 0（LRU 最近使用）
        stub_router.route("缓存问句3")                 # 触发淘汰：最久未用 = 1
        keys = list(stub_router._QUERY_CACHE)
        assert "缓存问句1" not in keys and "缓存问句0" in keys and "缓存问句3" in keys, keys
        # 默认目录：用户私有路径（~/.cache）；落盘目录 chmod 0700（上面的构建已建）
        d = semantic_router._default_cache_dir()
        assert d.startswith(str(Path.home()) + os.sep) and ".cache" in d, d
        cache_dir = Path(os.environ["SEMANTIC_ROUTER_CACHE"])
        assert cache_dir.is_dir(), cache_dir
        assert (cache_dir.stat().st_mode & 0o777) == 0o700, oct(
            cache_dir.stat().st_mode & 0o777)

    def test_examples_locked(self):
        """示例集锁：正负例规模/唯一性/标签 + 文件体积 + 内容 sha256。"""
        import hashlib
        path = (Path(__file__).resolve().parent.parent
                / "src/rag/semantic_router_examples.json")
        raw = path.read_bytes()
        assert len(raw) <= _MAX_BYTES, f"示例集体积 {len(raw)}B 超预算"
        pos = [e for e in _EXAMPLES if e["label"] == "search"]
        neg = [e for e in _EXAMPLES if e["label"] == "local"]
        assert len(pos) >= _MIN_PER_CLASS and len(neg) >= _MIN_PER_CLASS
        assert len({e["text"] for e in _EXAMPLES}) == len(_EXAMPLES)
        assert not ({e["text"] for e in pos} & {e["text"] for e in neg})
        assert all(e.get("src") for e in _EXAMPLES)      # 来源可追溯
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == _EXAMPLES_SHA256, "示例集变更：请同步 _EXAMPLES_SHA256 并记录理由"


# ============================================================
# B：判定层接线（stub 语义信号）
# ============================================================

@pytest.fixture
def stub_signal(monkeypatch):
    """把语义信号钉死（不加载模型），用于验证接线语义。"""
    def _set(label):
        monkeypatch.setattr(semantic_router, "route",
                            lambda text: semantic_router.Verdict(label))
    return _set


class TestDecideSearchWiring:
    def test_semantic_accept_recall_paraphrase(self, stub_signal):
        """换说法的外部事实问句（无实体名/无关键词）：语义 ACCEPT → 搜，reason=semantic。"""
        msg = "瑞幸咖啡现在怎么样"
        stub_signal("unknown")
        assert decide_search(msg).should_search is False       # 词表层漏搜（旧行为）
        stub_signal("search")
        d = decide_search(msg)
        assert d.should_search is True and d.reason == "semantic" and d.query

    def test_semantic_accept_guarded_for_unnamed_subject(self, stub_signal):
        """无命名主体指代（那个中医馆/某医馆/这家公司）不产 query → 护栏抑制 ACCEPT。"""
        stub_signal("search")
        for msg in ("新开的那个中医馆 靠谱吗", "某新成立的医馆口碑如何",
                    "那家店待遇怎么样", "这家公司怎么样"):
            assert decide_search(msg).should_search is False, msg
        # 对照：有命名主体（即便夹带「这家公司」）由实体层/词表层覆盖的仍搜
        assert decide_search("易宝支付这家公司靠不靠谱").should_search is True

    def test_semantic_accept_in_entity_branch(self, stub_signal):
        """实体层问词未覆盖的换说法（无强/弱问词、无时效词）：语义 ACCEPT 补搜，
        query 仍取实体名（实体抽取是 query 单一事实源）。"""
        msg = "易宝支付是不是骗子"
        stub_signal("unknown")
        assert decide_search(msg).should_search is False
        stub_signal("search")
        d = decide_search(msg)
        assert d.should_search is True and d.entity == "易宝支付"
        assert d.reason == "semantic" and d.query == "易宝支付"
        # 硬锚本地问挂靠时语义 ACCEPT 不得越过（层 3 硬否决优先）
        stub_signal("search")
        d2 = decide_search("我在易宝支付上班 今年运势怎么样")
        assert d2.should_search is False and d2.reason == "local"

    def test_semantic_veto_suppresses_keyword_false_positive(self, stub_signal):
        """词表层关键词误触（最近/看看…实为本地/倾诉）：语义 VETO → 不搜（reason=semantic）。"""
        msg = "最近工作压力好大，心里很累"
        stub_signal("unknown")
        assert decide_search(msg).should_search is True        # 旧词表层误触（timely）
        stub_signal("local")
        d = decide_search(msg)
        assert d.should_search is False and d.reason == "semantic"
        # llm 信号优先：模型说搜仍搜（OR 语义不变）
        d2 = decide_search(msg, llm_needs_search=True)
        assert d2.should_search is True and d2.reason == "timely"

    def test_semantic_veto_never_overrides_hard_vetoes(self, stub_signal):
        """硬否决优先：金融排除/命理本地判定在语义层之前，语义 ACCEPT 不得越过。"""
        stub_signal("search")
        for msg, reason in (("今天股市行情怎么样", "finance"),
                            ("帮我查一下大盘指数", "finance"),
                            ("今年运势如何", "local"),
                            ("我在易宝支付上班，今年运势怎么样？", "local"),
                            ("这个月适合搬家吗", "local"),
                            ("帮我看看我的运势怎么样", "local")):
            d = decide_search(msg)
            assert d.should_search is False and d.reason == reason, (msg, d)

    def test_entity_layer_deterministic_regardless_of_semantic(self, stub_signal):
        """实体层语义不变：强/弱实体问词与实体抽取是确定性（query 来源）。"""
        for label in ("unknown", "local"):
            stub_signal(label)
            d = decide_search("易宝支付这家公司靠不靠谱")
            assert d.should_search and d.reason == "entity" and d.entity == "易宝支付"
            d = decide_search("腾讯怎么样")
            assert d.should_search and d.reason == "entity"

    def test_unknown_keeps_keyword_semantics(self, stub_signal):
        """unknown（模型不可用/不确定）→ 既有 reason 语义逐字保持。"""
        stub_signal("unknown")
        assert decide_search("最近有什么政策变化").reason == "timely"
        assert decide_search("帮我查一下最近的行业新闻").reason == "timely"
        d = decide_search("新开的那个中医馆 靠谱吗", llm_needs_search=True)
        assert d.should_search and d.reason == "llm"

    def test_external_timely_ask_not_vetoed(self, stub_signal):
        """k43-r1（审查 Important-2）：时效性赛事/影视类**外部事实问句**——词表层
        关键词正信号（base 判搜）不得被语义负信号压掉（漏搜比多搜更不可接受）。

        这些句子是 A/B 旧用例集构造上缺失的「关键词层判对而语义误否决」样本。
        """
        stub_signal("local")
        for msg, reason in (("比赛什么时候开始", "whitelist"),
                            ("这场比赛什么时候开始", "whitelist"),
                            ("这部电影好看吗", "whitelist"),
                            ("这场比赛在哪踢", "whitelist"),
                            ("世界杯什么时候开始", "whitelist")):
            d = decide_search(msg)
            assert d.should_search and d.reason == reason and d.query, (msg, d)
        # 对照：个人记叙/倾诉（无外部主体词）仍被语义 VETO 正常抑制
        for msg in ("最近工作压力好大，心里很累", "我今天看了一部电影，特别感人"):
            d = decide_search(msg)
            assert d.should_search is False and d.reason == "semantic", (msg, d)
        # 对照：llm 信号仍高于一切（OR 语义不变）
        assert decide_search("最近工作压力好大，心里很累",
                             llm_needs_search=True).should_search is True

    def test_deictic_guard_exempts_external_factual_ask(self, stub_signal):
        """k43-r1（审查 Important-3）：指示代词+量词**不得一律当「无主体」**——
        外部时效/事实类（美联储/诺奖/新车/新机…）放行语义 ACCEPT，produce query。

        base 也不搜（非回归），但它们正是语义 ACCEPT 的目标类，不得被
        `_has_unnamed_subject_ref` 在入口掐掉。
        """
        stub_signal("search")
        for msg in ("这次美联储降息了吗", "这次诺贝尔奖颁给谁了",
                    "这台新车值得买吗", "这款新车什么时候上市",
                    "这台笔记本电脑值得买吗", "这部电视剧值得追吗"):
            d = decide_search(msg)
            assert d.should_search and d.reason == "semantic" and d.query, (msg, d)
        # 对照：无外部主体词的指代句仍被护栏拦下（沿用层 4/5「纯指代不触发」语义）
        for msg in ("新开的那个中医馆 靠谱吗", "某新成立的医馆口碑如何",
                    "那家店待遇怎么样", "这家公司怎么样"):
            assert decide_search(msg).should_search is False, msg

    def test_llm_signal_beats_semantic_veto_in_all_layers(self, stub_signal):
        """llm_needs_search OR 叠加：层 4/5 与实体层时效子分支的语义否决都可被推翻。"""
        stub_signal("local")
        assert decide_search("最近工作压力好大，心里很累",
                             llm_needs_search=True).reason == "timely"
        assert decide_search("小米公司最新动态",
                             llm_needs_search=True).should_search is True

    def test_reason_values_are_new_additive_only(self):
        """reason 取值集合：既有语义不动，仅新增 semantic（结构锁）。"""
        known = {"entity", "timely", "whitelist", "llm", "none", "finance",
                 "local", "semantic"}
        import inspect
        src = inspect.getsource(sys.modules["src.rag.search_trigger"])
        import re
        used = set(re.findall(r'reason="([a-z_]+)"', src))
        assert used <= known, used - known
        assert "semantic" in used

    def test_signature_and_decision_shape_unchanged(self):
        """接口锁：签名/返回结构不变（多处调用）。"""
        import inspect
        sig = inspect.signature(decide_search)
        assert list(sig.parameters) == ["text", "llm_needs_search"]
        assert sig.parameters["llm_needs_search"].default is False
        d = decide_search("易宝支付靠谱吗")
        assert isinstance(d, SearchDecision)
        assert set(d.__dataclass_fields__) == {"should_search", "query",
                                               "entity", "reason"}


# ============================================================
# C：真机（本地 bge-m3）
# ============================================================

def _local_model_ready() -> bool:
    """仅当本地已有 bge-m3 权重才跑真机用例（避免 CI 触发网络下载）。"""
    try:
        import src.rag.embedder  # noqa: F401  触发本地路径注册
        from src.rag.embedder_v2 import _LOCAL_PATH_MAP
        p = _LOCAL_PATH_MAP.get("BAAI/bge-m3")
        return bool(p and Path(p).exists())
    except Exception:
        return False


@pytest.fixture(scope="module")
def real_router():
    if not _local_model_ready():
        pytest.skip("本地无 bge-m3 权重（真机用例跳过）")
    if not semantic_router.warmup(blocking=True):
        pytest.skip("语义路由真机预热失败")
    return semantic_router


@pytest.mark.usefixtures("real_router")
class TestRealSemanticRouting:
    def test_paraphrased_company_question_still_searched(self):
        """k43 核心语义用例：换个说法的公司/外部事实问句仍判搜。"""
        for msg in ("瑞幸咖啡现在怎么样", "C919 现在投入商业运营了吗",
                    "现在去泰国旅游安全吗", "星巴克在中国还赚钱吗",
                    "我朋友推荐我去米哈游，那边加班多不多"):
            assert decide_search(msg).should_search is True, msg

    def test_local_chitchat_not_searched(self):
        """词表层关键词误触的本地/倾诉句：语义层判不搜（回归锁）。"""
        for msg in ("最近工作压力好大，心里很累", "帮我看看我的婚姻状况",
                    "帮我看看我的会员还剩几天", "我最近在准备考试，压力很大"):
            assert decide_search(msg).should_search is False, msg

    def test_hard_vetoes_hold_with_real_model(self):
        """真机模型下硬否决仍逐条成立（语义层不得越过）。"""
        for msg, reason in (("今天股市行情怎么样", "finance"),
                            ("今年运势如何", "local"),
                            ("我在易宝支付上班，今年运势怎么样？", "local")):
            d = decide_search(msg, llm_needs_search=True)
            assert not d.should_search and d.reason == reason, (msg, d)

    def test_external_timely_ask_families_searched(self):
        """k43-r1（审查 Important-2/3）真机：赛事/影视/宏观/新品族的外部事实问句
        判搜——含审查点名的 5 句（比赛什么时候开始/这部电影好看吗/这次美联储降息
        了吗/这次诺贝尔奖颁给谁了/这台新车值得买吗）。"""
        for msg in ("比赛什么时候开始", "这场比赛什么时候开始", "这场比赛在哪踢",
                    "这部电影好看吗", "这部电影什么时候上映", "那部电影值得看吗",
                    "这次美联储降息了吗", "这次诺贝尔奖颁给谁了",
                    "这台新车值得买吗", "这款新车什么时候上市",
                    "这台笔记本电脑值得买吗", "这部电视剧值得追吗"):
            assert decide_search(msg).should_search is True, msg

    def test_personal_anecdote_still_not_searched(self):
        """k43-r1 反向锁：个人记叙/情绪倾诉（无外部主体词）仍不搜（VETO 未放宽）。"""
        for msg in ("最近工作压力好大，心里很累", "我今天看了一部电影，特别感人",
                    "我最近在准备考试，压力很大"):
            assert decide_search(msg).should_search is False, msg

    def test_eval_positives_unchanged(self):
        """评测正例（T104/T105/T072）与 k15 断言口径不变。"""
        d = decide_search("易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。")
        assert d.should_search and d.entity == "易宝支付" and d.reason == "entity"
        d = decide_search("腾讯这家公司怎么样，适合我的事业吗？我的盘你之前排过。")
        assert d.should_search and d.entity == "腾讯"
        assert decide_search("明天北京天气怎么样").should_search is True
