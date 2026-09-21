"""k79 隐私/安全**真正收尾**批回归门禁 —— 与 k78-final 独立复审报出的
「1 Critical + 2 Important + 4 Minor」逐条对应（控制方定规：只要是报的都要修）。

覆盖：
  T1 **必修1（Critical）** 畸形报告 JSON 仍然 500 + 判据有缺口：
     - 「判据说可展示 ⇒ 所有共用它的路由都不 500」用**穷举语料 × 4 路由**实测
       （复审列的全部形态 + 本批补的：嵌套数组 / 超深嵌套 / 巨大对象 /
       `{"profile":null}` / `{"profile":{"name":123}}` / 批量元素类型错 / NaN）；
     - 判据（`report_shape_problem`）的边界：**结构**不可能才 404，值层面降级；
     - 三个根因逐条钉住：`profile=null` 时 `.get` 默认值不生效、
       `insights[0][:N]` 的副本、`bazi_analysis` 非对象。
  T2 **必修1 的收敛**：`_REPORT_JSON_CONSUMERS`（全部消费点清单）**机器穷举**
     —— 出现新的"读报告 JSON"的文件即红，逼着人来更新清单并让新消费点也站得住。
  T3 **必修2（Important）** `account_copy.py` 不实注释：docstring 的边界必须
     **准确**（写清拦得住什么、**仍在面外**什么），且宽面/折叠/局部名三条修法
     在守卫里**真的接线**（守卫自身由 `miniprogram/tests/k71_*.test.js` 跑，
     本文件钉住接线与注入证明的存在性）。
  T4 **必修3（Important）** "客户端会渲染"这个理由说过头：按实况改成
     「API/OpenAPI 接入方可见面」，并把小程序侧的事实（哪三处不显示）钉住。
  T5 **M-1** 删除失败与"本来就没有"必须可区分（表 / 文件 / "表不存在" 三类）。
  T6 **M-2** 加载与路径拼接**收敛为单一实现**，且测试隔离仍然成立。
  T7 **M-3** `privacy.md` 与 `src/` 措辞统一（`不可自助恢复`）。
  T8 **M-4** 守卫里的陈旧数字改准（实测口径）。

红线：不触网、不开生产库（库/报告目录全部 monkeypatch 到 tmp）；不新增 skip/xfail；
未删改任何既有断言（本文件只**新增**）。
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent


# ── 共用 ───────────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch):
    """一个只挂报告/分享路由的 app：两个模块的 _DATA_DIR 都指向 tmp（隔离）。"""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.api import share as share_api
    from src.api import visual_report as vr
    from src.security.auth import AuthHandler, set_auth_handler

    monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(share_api, "_dao", None)
    ah = AuthHandler()
    set_auth_handler(ah)
    app = FastAPI()
    app.include_router(vr.router)
    app.include_router(share_api.router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, {"Authorization": f"Bearer {ah.create_user_token('k79-owner')}"}


def _write(tmp_path: Path, rid: str, payload, owner_uid: str = "k79-owner") -> Path:
    from src.api.visual_report import _REPORT_OWNER_FIELD
    from src.security.encryption import DataEncryptor
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("reading_id", rid)
        if owner_uid:
            payload[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
    p = tmp_path / f"{rid}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


_DEEP = "x"
for _ in range(120):
    _DEEP = [_DEEP]
_DEEPER = "x"
for _ in range(400):
    _DEEPER = [_DEEPER]
_FAR = 1 << 2000

#: 复审列出的形态 + 本批补的形态（**只增不减**：新报一个形态就往这里加一条）
MALFORMED_CASES = [
    ("profile=null", {"profile": None, "insights": ["ok"]}),
    ("insights=[12345]", {"profile": {"name": "x"}, "insights": [12345]}),
    ("insights=[{'a':1}]", {"profile": {"name": "x"}, "insights": [{"a": 1}]}),
    ("insights=[True]", {"profile": {"name": "x"}, "insights": [True]}),
    ("insights=[None,1,2.5,{},[]]",
     {"profile": {"name": "x"}, "insights": [None, 1, 2.5, True, {}, [], "真文本"]}),
    ("insights=[NaN]", {"profile": {"name": "x"}, "insights": [float("nan")]}),
    ("insights=[Infinity]", {"profile": {"name": "x"}, "insights": [float("inf")]}),
    ("insights=[[['deep']]]", {"profile": {"name": "x"}, "insights": [[["deep"]]]}),
    ("insights=[大整数]", {"profile": {"name": "x"}, "insights": [_FAR]}),
    ("bazi_analysis='notadict'",
     {"profile": {"name": "x"}, "insights": ["ok"], "bazi_analysis": "notadict"}),
    ("bazi_analysis.yongshen=123",
     {"profile": {"name": "x"}, "insights": ["ok"], "bazi_analysis": {"yongshen": 123}}),
    ("charts='x'", {"profile": {"name": "x"}, "insights": ["ok"], "charts": "x"}),
    ("profile.name=123", {"profile": {"name": 123}, "insights": ["ok"]}),
    ("profile.bazi=[1,2]", {"profile": {"name": "x", "bazi": [1, 2]}, "insights": ["ok"]}),
    ("profile=null+insights=null", {"profile": None, "insights": None}),
    ("profile={}+insights=[12345]", {"profile": {}, "insights": [12345]}),
    ("profile 缺 + insights=['ok']", {"insights": ["ok"]}),
    ("超深嵌套(120 层)", {"profile": {"name": "x"}, "insights": ["ok"], "deep": _DEEP}),
    ("超深嵌套(400 层)", {"profile": {"name": "x"}, "insights": ["ok"], "deep": _DEEPER}),
    ("巨大对象(20 万字符)",
     {"profile": {"name": "x", "big": "A" * 200000}, "insights": ["ok"]}),
    ("对照：正常报告",
     {"generated_at": "2026-09-01T10:00:00",
      "profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯", "day_master": "甲木"},
      "bazi_analysis": {"geju": "正官格", "yongshen": "水（喜）", "wuxing": {"金": 2}},
      "charts": {"wuxing_radar": [], "monthly_fortune": [], "annual_trend": []},
      "insights": ["洞察一"], "recommendations": []}),
]

ROUTES = ("/share/{rid}", "/api/share/{rid}", "/api/report/{rid}", "/report/{rid}")


# ══════════════════════════════════════════════════════════════════════
# T1 必修1：判为"可展示"的形态，在所有共用判据的路由上都不 5xx
# ══════════════════════════════════════════════════════════════════════

class TestNoRouteEver500OnAnyReportShape:
    def test_all_shapes_on_all_routes_are_not_5xx(self, tmp_path, monkeypatch):
        """穷举语料 × 4 个共用路由：**一个 5xx 都不许有**（复审实测改前 7~13 个）。

        判据说"可展示"的形态 → 200（可能降级显示）；判据说"不可展示" → 404；
        **两者都不等于 500**。这条断言就是"判据是所有消费点的判据"的可执行形式：
        真有一处消费点没兜住，这里必红（k79 报告有改前/改后原始 HTTP 对照）。
        """
        from src.api.visual_report import report_shape_problem

        client, h = _client(tmp_path, monkeypatch)
        bad = []
        for i, (label, payload) in enumerate(MALFORMED_CASES):
            rid = "b9%06x" % (0x200 + i)      # 含字母 → /api/share 走旧报告分支
            _write(tmp_path, rid, payload)
            judged = report_shape_problem(json.loads(
                (tmp_path / f"{rid}.json").read_text(encoding="utf-8")))
            for route in ROUTES:
                r = client.get(route.replace("{rid}", rid), headers=h)
                if r.status_code >= 500:
                    bad.append(f"{label} {route} → {r.status_code}（判据说"
                               f"{'不可展示' if judged else '可展示'}）")
        assert bad == [], "判为可展示的形态在共用路由上出现 5xx：\n  - " + "\n  - ".join(bad)

    def test_judge_rejects_only_structural_impossibility(self):
        """判据边界：**结构**不可能 → 判"(不可展示)"；值层面异常 → 判可展示（降级）。"""
        from src.api.visual_report import report_shape_problem
        # ① 结构不可能（容器类型错 / 空壳）
        for payload in ("just a string", [1, 2, 3], 12345, None, {},
                        {"profile": [1, 2]}, {"insights": "notalist"},
                        {"profile": None, "insights": None},
                        {"profile": {}, "insights": [12345]},
                        {"bazi_analysis": "notadict", "profile": {"name": "x"},
                         "insights": ["ok"]},
                        {"charts": ["x"], "profile": {"name": "x"}, "insights": ["ok"]}):
            assert report_shape_problem(payload), f"{payload!r} 应判不可展示"
        # ② 值层面异常（渲染降级，不 404）
        for payload in ({"profile": None, "insights": ["ok"]},
                        {"profile": {"name": 123}, "insights": ["ok"]},
                        {"profile": {"name": "x"}, "insights": [12345]},
                        {"profile": {"name": "x"}, "insights": [float("nan")]}):
            assert report_shape_problem(payload) == "", (
                f"{payload!r} 被判不可展示 —— 值层面异常应降级显示（本批口径）")

    def test_card_from_report_never_raises(self):
        """`_card_from_report`：复审点名的四处 AttributeError/TypeError/KeyError 全消。"""
        from src.api.share import _card_from_report
        for label, payload in MALFORMED_CASES:
            card = _card_from_report(payload)       # 不许抛
            assert isinstance(card, dict) and card["reading_id"] is not None, label
            assert isinstance(card["title"], str) and isinstance(card["summary"], str), label
            assert isinstance(card["bazi_data"]["bazi"], list), label
            # 卡片必须**可 JSON 序列化**（NaN → null），否则端点仍是 500
            json.dumps(card, ensure_ascii=False, allow_nan=False)

    def test_first_insight_text_single_implementation(self):
        """「取第一条洞察」只有一份实现（副本漂移正是本批根因）。"""
        from src.api.visual_report import first_insight_text as f
        assert f({"insights": ["甲乙丙"]}) == "甲乙丙"
        assert f({"insights": ["甲乙丙"]}, 2) == "甲乙"
        for bad in ({"profile": None}, {"insights": None}, {"insights": "x"},
                    {"insights": [12345]}, {"insights": [float("nan")]},
                    {"insights": [None, {}, [], "  ", "真的"]}, {}, "notadict", None):
            assert f(bad) == "" or f(bad) == "真的", f"{bad!r} → {f(bad)!r}"
        assert f({"insights": [None, 12345, "真的"]}) == "真的", "应取第一条**真的是文本**的"

    def test_json_safe_neutralises_non_finite(self):
        """`NaN/Infinity` 是**值**缺陷 → 序列化成 null（不是 404、更不是 500）。"""
        from src.api.visual_report import json_safe
        out = json_safe({"a": float("nan"), "b": [float("inf"), 1], "c": {"d": -float("inf")}})
        assert out == {"a": None, "b": [None, 1], "c": {"d": None}}
        json.dumps(out, allow_nan=False)           # 不会抛
        assert json_safe("文本") == "文本" and json_safe(7) == 7

    def test_no_raw_insights_subscript_left_in_report_consumers(self):
        """「insights[0][:N]」这种**无兜底的下标**不得再出现在消费点里（副本要消失）。

        用 AST 判（注释/docstring 里**引述**这段历史代码不算回归）。
        """
        import ast
        offenders = []
        for rel in ("src/api/share.py", "src/api/visual_report.py"):
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                        and node.value.id == "insights":
                    offenders.append(f"{rel}:{node.lineno}")
        assert offenders == [], (
            f"又出现对 insights 的直接下标（应走 first_insight_text）：{offenders}")
        share_py = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        vr_py = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        assert "first_insight_text" in share_py and "first_insight_text" in vr_py


# ══════════════════════════════════════════════════════════════════════
# T2 必修1：全部消费点清单是**闭集**（新消费点必须被看见）
# ══════════════════════════════════════════════════════════════════════

class TestReportJsonConsumerInventoryIsClosed:
    #: 允许读报告 JSON 的模块（**唯一**的事实；新增消费者必须同时更新
    #: `visual_report._REPORT_JSON_CONSUMERS` 与这里，并证明新点自己也不抛）
    ALLOWED = {"src/api/visual_report.py", "src/api/share.py", "src/storage/dao.py"}

    def test_inventory_entries_exist_in_code(self):
        from src.api.visual_report import _REPORT_JSON_CONSUMERS
        assert len(_REPORT_JSON_CONSUMERS) >= 7, "消费点清单被删过？"
        for entry in _REPORT_JSON_CONSUMERS:
            rel, _, symbol = entry.partition("::")
            src = (REPO / rel).read_text(encoding="utf-8")
            assert f"def {symbol}(" in src or f"{symbol} =" in src, \
                f"清单里的 {entry} 在代码里找不到（清单与代码已漂移）"

    def test_no_new_reader_outside_the_inventory(self):
        """全仓搜「读报告 JSON」的迹象：落在清单外的文件即红。

        这是"你怎么保证全部消费点真的是全部"的**机器答案**：不靠人记，靠扫描 +
        白名单；白名单外的任何新读者都会让这条红，逼着人来把它写进清单、并补上
        它自己的类型兜底与验证。
        """
        import ast
        needles = {"load_report", "load_report_from", "report_path_in",
                   "_card_from_report", "_redact_report_for_share",
                   "_report_share_expires_at", "report_shape_problem",
                   "_build_report_html", "first_insight_text", "as_mapping",
                   "json_safe"}
        found = {}
        for py in sorted((REPO / "src").rglob("*.py")):
            rel = str(py.relative_to(REPO))
            tree = ast.parse(py.read_text(encoding="utf-8"))
            hits = set()
            for node in ast.walk(tree):
                # 只看**真引用**（标识符/属性名）；注释与 docstring 里的引述不算
                if isinstance(node, ast.Name) and node.id in needles:
                    hits.add(node.id)
                elif isinstance(node, ast.Attribute) and node.attr in needles:
                    hits.add(node.attr)
                # 报告目录常量：只认 `… / "data" / "reports"` 这种**路径拼接**
                # （字典键 `{"reports": …}`、文案里的 "data/reports/*.json" 不算）
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    for side in (node.left, node.right):
                        if isinstance(side, ast.Constant) and side.value == "reports":
                            hits.add('/ "reports" 路径常量')
            if hits and rel not in self.ALLOWED:
                found[rel] = sorted(hits)
        assert found == {}, (
            "发现清单外的模块引用了报告 JSON 的读取符号 —— 请先把它加进 "
            "`visual_report._REPORT_JSON_CONSUMERS` 与本测试的 ALLOWED，"
            "并证明它对任意输入都不抛：\n  " + json.dumps(found, ensure_ascii=False))

    def test_dao_is_covered_by_the_corpus_too(self, tmp_path):
        """第三个消费者（dao 的注销清理）也必须对畸形输入站得住（k79 附带发现）。"""
        from src.security.encryption import DataEncryptor
        from src.storage import dao as dao_mod
        enc = DataEncryptor()
        (tmp_path / "aaa.json").write_text("[1, 2, 3]", encoding="utf-8")
        (tmp_path / "ddd.json").write_text("{ 不是 JSON", encoding="utf-8")
        (tmp_path / "bee.json").write_text(json.dumps(
            {"reading_id": "bee", "owner_enc": enc.encrypt("me"), "insights": ["x"]},
            ensure_ascii=False), encoding="utf-8")
        (tmp_path / "cee.json").write_text(json.dumps(
            {"reading_id": "cee", "owner_enc": enc.encrypt("other")}), encoding="utf-8")
        failures = []
        stats = dao_mod.purge_report_files("me", reports_dir=tmp_path,
                                          charts_dir=tmp_path, failures=failures)
        # 改前：'list' object has no attribute 'get' 打断**整轮**清理 → 本人报告没删掉
        assert stats["reports"] == 1, "畸形文件把整轮清理打断了（本人报告没删掉）"
        assert not (tmp_path / "bee.json").exists(), "本人报告仍在（注销后仍可被公开读）"
        assert (tmp_path / "cee.json").exists(), "别人的报告被删了（越界）"
        assert (tmp_path / "aaa.json").exists(), "畸形文件被删了（不该动）"
        assert failures == [], f"这些不是真失败（不该进 failures）：{failures}"


# ══════════════════════════════════════════════════════════════════════
# T3 必修2：不实注释改成准确边界（+ 修法接线）
# ══════════════════════════════════════════════════════════════════════

class TestBackendCopyBoundaryIsAccurate:
    def _doc(self):
        src = (REPO / "src" / "security" / "account_copy.py").read_text(encoding="utf-8")
        return src[:src.index('"""', src.index('"""') + 3)]

    def test_no_longer_claims_no_escape(self):
        doc = self._doc()
        # 「**不会逃逸**」（加粗的结论式说法）只允许出现在**引述并更正**的句子里；
        # 边界句（"这一面不会逃逸"）是准确表述，不算违规。
        for line in [l for l in doc.split("\n") if "**不会逃逸**" in l]:
            assert ("错的" in line) or ("不实" in line) or ("改前" in line), (
                "docstring 把「不会逃逸」写成了结论（复审实测四类逃逸）："
                + line.strip())
        assert "仍在面外" in doc and "拦得住" in doc, \
            "docstring 未同时写清「拦得住什么」与「仍在面外什么」"
        assert "仍在面外" in doc, "docstring 未如实列出**面外**形态"
        assert "拼接" in doc and "局部" in doc, "docstring 未写明折叠/局部名这两条修法"

    def test_guard_actually_scans_the_wide_face(self):
        """修法必须在守卫里**真的接线**（宽面 + 折叠 + 局部名 + env 传规则表）。"""
        guard = (REPO / "miniprogram" / "tests" / "k71_privacy_consistency.test.js"
                 ).read_text(encoding="utf-8")
        assert "k79-必修2 宽面" in guard, "守卫里没有「宽面」这一条测试"
        assert "K71_BACKEND_FORBIDDEN_JSON" in guard and "pyJsonEnv" in guard, \
            "禁语表没有经 env 交给抽取器（宽面就没扫到同一份规则）"
        assert "collect_locals" in guard, "抽取器里没有局部常量解析（`detail=局部名` 仍在面外）"
        assert "folded" in guard, "抽取器里没有常量折叠"
        # 注入证明：守卫必须能被四类逃逸**变红**（k79 报告有逐条实测输出）
        assert "wide_face_hits" in guard

    def test_no_stale_counts_left(self):
        """M-4：陈旧数字（285）必须清掉，且实测口径写在注释里。"""
        guard = (REPO / "miniprogram" / "tests" / "k71_privacy_consistency.test.js"
                 ).read_text(encoding="utf-8")
        assert "改前实测 285" not in guard, "285 这个与实测对不上的数字还在"
        assert "k79 实测 414" in guard, "未写明实测值（k78 版本实测 406）"
        assert ">= 250" in guard, "阈值被改动（不得放宽既有断言）"


# ══════════════════════════════════════════════════════════════════════
# T4 必修3：可见面的理由要说准（API/OpenAPI 接入方，不是"客户端会渲染"）
# ══════════════════════════════════════════════════════════════════════

class TestVisibilityReasonIsAccurate:
    def test_doc_states_api_surface_not_client_rendering(self):
        src = (REPO / "src" / "security" / "account_copy.py").read_text(encoding="utf-8")
        assert "API / OpenAPI 接入方可见面" in src, "可见面口径未写明"
        assert "不要" in src and "客户端会渲染" in src, \
            "未把「客户端会渲染」标为**不要**用的理由"

    def test_the_three_constants_really_are_not_rendered_by_the_client(self):
        """把小程序侧的事实钉住（改了任一处，本断言红 → 必须同步这段说明）。

        - 登录 403 detail → app.js 的 catch 只 console.warn + 进本地模式；
        - 注销响应 message → settings.js 的 .then() 忽略响应体、弹自己的 toast；
        - 两个数据删除端点 + /retention/info → 客户端零调用点。
        """
        app_js = (REPO / "miniprogram" / "app.js").read_text(encoding="utf-8")
        settings_js = (REPO / "miniprogram" / "pages" / "settings" / "settings.js"
                       ).read_text(encoding="utf-8")
        assert "initLocalMode" in app_js and "console.warn('[登录] 失败" in app_js, \
            "登录失败路径变了 —— account_copy 的可见面说明要跟着改"
        i = settings_js.index("api.cancelAccount(")
        win = settings_js[i:i + 700]
        assert "wx.showToast({ title: '账号已注销'" in win, "注销响应的处理变了"
        assert "res.message" not in win and "err.detail" not in win, \
            "注销路径开始读响应体了 —— 该常量对小程序用户可见了，说明要更新"
        # 零调用点（整仓搜客户端代码，不含测试）
        client_calls = []
        for js in (REPO / "miniprogram").rglob("*.js"):
            if "tests" in js.parts or "node_modules" in js.parts:
                continue
            text = js.read_text(encoding="utf-8")
            for needle in ("user/data", "retention/info", "/data/"):
                if needle in text:
                    client_calls.append(f"{js.relative_to(REPO)} ← {needle}")
        assert client_calls == [], (
            "客户端出现了数据删除/保留策略端点的新调用点 —— 这三条常量对用户可见了，"
            f"可见面说明要改成实况：{client_calls}")


# ══════════════════════════════════════════════════════════════════════
# T5 M-1：删除失败 ⊥ 本来就没有（响应里必须可区分）
# ══════════════════════════════════════════════════════════════════════

def _sec_router_client(db_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.security import router as sec_router
    from src.security.auth import AuthHandler, set_auth_handler
    from src.security.encryption import DataEncryptor
    from src.security.privacy import PrivacyManager

    monkeypatch.setenv("FORTUNE_DB_PATH", db_path)
    ah = AuthHandler()
    set_auth_handler(ah)
    sec_router.init_security_router(db_path=db_path, auth_handler=ah,
                                    encryptor=DataEncryptor())
    monkeypatch.setattr(sec_router, "_privacy_manager",
                        PrivacyManager(db_path, DataEncryptor()))
    app = FastAPI()
    app.include_router(sec_router.router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, {"Authorization": f"Bearer {ah.create_user_token('k79-del')}"}


class TestPurgeFailureIsDistinguishable:
    def test_missing_table_predicate(self):
        from src.storage.dao import _is_missing_table_error
        assert _is_missing_table_error(sqlite3.OperationalError("no such table: x"))
        assert not _is_missing_table_error(
            sqlite3.OperationalError("cannot modify x because it is a view"))
        assert not _is_missing_table_error(
            sqlite3.OperationalError("no such column: owner_uid"))
        assert not _is_missing_table_error(sqlite3.OperationalError("database is locked"))

    def test_empty_db_is_ok_and_missing_tables_are_visible(self, tmp_path, monkeypatch):
        """复审空库场景：**不是失败**（0 行就是事实），但必须在响应里看得见。"""
        from src.security.account_copy import USER_DATA_PURGED_NOTICE
        db = str(tmp_path / "empty.db")
        client, h = _sec_router_client(db, monkeypatch)
        r = client.delete("/api/security/user/k79-del/data", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["message"] == USER_DATA_PURGED_NOTICE
        rec = body["records_deleted"]
        assert rec["ok"] is True and rec["failed_tables"] == {}
        assert len(rec["missing_tables"]) > 0, "表不存在这一档没有在响应里体现"

    def test_real_failure_is_not_reported_as_success(self, tmp_path, monkeypatch):
        """`no such table` 之外的失败（此处把表换成同名视图）→ **不许**回"已删除"。"""
        from src.security.account_copy import (DATA_PURGE_INCOMPLETE_NOTICE,
                                              USER_DATA_PURGED_NOTICE)
        from src.storage.models import init_db
        db = str(tmp_path / "broken.db")
        init_db(db).close()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE consultations")
        conn.execute("CREATE VIEW consultations AS SELECT 1 AS id")
        conn.commit()
        conn.close()
        client, h = _sec_router_client(db, monkeypatch)
        r = client.delete("/api/security/user/k79-del/data", headers=h)
        # 改前：200 + "个人数据已删除…"（与实况相反的成功）
        assert r.status_code == 500, f"真删失败仍返回 {r.status_code}：{r.text[:200]}"
        body = r.json()
        assert body["message"] == DATA_PURGE_INCOMPLETE_NOTICE
        assert body["message"] != USER_DATA_PURGED_NOTICE
        assert "consultations" in body["failed_tables"], body["failed_tables"]
        assert "view" in body["failed_tables"]["consultations"]

    def test_main_endpoint_same_behaviour(self, tmp_path, monkeypatch):
        """`src/main.py` 的同一端点同一口径（两处必须一致）。"""
        import src.main as main_mod
        from src.security.account_copy import DATA_PURGE_INCOMPLETE_NOTICE
        from src.security.auth import AuthHandler, set_auth_handler
        from src.storage.models import init_db
        from starlette.testclient import TestClient

        db = str(tmp_path / "main.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        init_db(db).close()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE consultations")
        conn.execute("CREATE VIEW consultations AS SELECT 1 AS id")
        conn.commit()
        conn.close()
        ah = AuthHandler()
        set_auth_handler(ah)
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        h = {"Authorization": f"Bearer {ah.create_user_token('k79-del2')}"}
        r = client.delete("/api/user/data/k79-del2", headers=h)
        assert r.status_code == 500, f"{r.status_code}: {r.text[:200]}"
        assert r.json()["message"] == DATA_PURGE_INCOMPLETE_NOTICE
        assert "consultations" in r.json()["failed_tables"]

    def test_file_failure_is_visible_too(self, tmp_path, monkeypatch):
        """文件类失败同样可区分（改前只写一行日志，`files` 里记 0）。"""
        from src.storage.dao import purge_account_files
        reps, charts, avatars = (tmp_path / "r"), (tmp_path / "c"), (tmp_path / "a")
        for d in (reps, charts, avatars):
            d.mkdir()
        # 头像路径是个**目录** → unlink 必失败（IsADirectoryError），但不是"不存在"
        (avatars / "k79-x.jpg").mkdir()
        failures = []
        stats = purge_account_files("k79-x", reports_dir=reps, charts_dir=charts,
                                    avatar_dir=avatars, failures=failures)
        assert stats["avatar"] == 0
        assert any(f["kind"] == "头像" for f in failures), failures
        assert "IsADirectoryError" in failures[0]["error"], failures

    def test_purge_account_data_reports_failed_and_missing_separately(self, tmp_path):
        from src.storage.dao import purge_account_data
        from src.storage.models import init_db
        db = str(tmp_path / "split.db")
        conn = init_db(db)
        try:
            conn.execute("DROP TABLE consultations")
            conn.execute("CREATE VIEW consultations AS SELECT 1 AS id")
            conn.commit()
            out = purge_account_data(conn, "k79-split", purge_files=False)
        finally:
            conn.close()
        assert out["ok"] is False
        assert "consultations" in out["failed_tables"]
        assert "consultations" not in out["missing_tables"]
        assert len(out["missing_tables"]) > 0        # 本库本来就没建全
        assert out["failed_files"] == []


# ══════════════════════════════════════════════════════════════════════
# T6 M-2：加载/路径拼接收敛为单一实现，且测试隔离仍成立
# ══════════════════════════════════════════════════════════════════════

class TestLoadingHasOneImplementation:
    def test_share_delegates_but_keeps_own_dir(self, tmp_path, monkeypatch):
        """委托同一实现 + **各自的目录常量** ⇒ monkeypatch 仍然各自生效。"""
        from src.api import share as share_api
        from src.api import visual_report as vr

        mine, theirs = tmp_path / "share", tmp_path / "vr"
        mine.mkdir()
        theirs.mkdir()
        _write(mine, "aa000001", {})
        monkeypatch.setattr(share_api, "_DATA_DIR", mine)
        monkeypatch.setattr(vr, "_DATA_DIR", theirs)
        assert share_api._load_report("aa000001"), "分享模块没读自己的目录（隔离破了）"
        assert vr.load_report("aa000001") is None, "报告模块读到了别人的目录（隔离破了）"
        _write(theirs, "aa000002", {})
        assert vr.load_report("aa000002"), "报告模块没读自己的目录"

    def test_one_implementation_only(self):
        """两份副本必须消失：`_DATA_DIR / f"{id}.json"` 的拼接只剩一处。"""
        share_py = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        vr_py = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        assert 'f"{reading_id}.json"' not in share_py, "share.py 又自己拼路径了"
        assert 'f"{reading_id}.json"' in vr_py, "唯一实现（report_path_in）不见了"
        assert "load_report_from" in share_py and "load_report_from" in vr_py
        # 判据也仍只有一份
        assert "def report_shape_problem" in vr_py
        assert "def report_shape_problem" not in share_py

    def test_malformed_root_is_rejected_by_the_single_loader(self, tmp_path):
        from src.api.visual_report import load_report_from, report_path_in
        (tmp_path / "aa000003.json").write_text("[1,2,3]", encoding="utf-8")
        (tmp_path / "aa000004.json").write_text("{坏 JSON", encoding="utf-8")
        assert load_report_from(tmp_path, "aa000003") is None
        assert load_report_from(tmp_path, "aa000004") is None
        assert load_report_from(tmp_path, "aa000005") is None
        assert report_path_in(tmp_path, "aa000003").name == "aa000003.json"

    def test_loader_never_raises_on_impossible_paths(self, tmp_path):
        """**加载器永不抛**：超长文件名 / NUL 字节 / 权限都不许变成 500。

        实测改前 `/api/share/{300 字符 id}` → 500（`exists()` 的 OSError 逃出 try）。
        """
        from src.api.visual_report import load_report_from
        assert load_report_from(tmp_path, "a" * 300) is None
        assert load_report_from(tmp_path, "a\x00b") is None
        assert load_report_from(tmp_path, "") is None

    def test_long_id_on_share_routes_is_404_not_500(self, tmp_path, monkeypatch):
        client, h = _client(tmp_path, monkeypatch)
        long_id = "a" * 300
        r1 = client.get(f"/api/share/{long_id}", headers=h)
        assert r1.status_code == 404, f"/api/share 超长 id → {r1.status_code}（应 404）"
        r2 = client.get(f"/share/{long_id}")
        assert r2.status_code == 404, f"/share 超长 id → {r2.status_code}（应 404）"
        r3 = client.get(f"/api/report/{long_id}", headers=h)
        r4 = client.get(f"/report/{long_id}", headers=h)
        assert r3.status_code == 404 and r4.status_code == 404, (r3.status_code, r4.status_code)


# ══════════════════════════════════════════════════════════════════════
# T7 M-3：privacy.md 与 src/ 措辞统一
# ══════════════════════════════════════════════════════════════════════

class TestPrivacyDocVocabularyUnified:
    def test_retention_line_uses_repo_vocabulary(self):
        md = (REPO / "miniprogram" / "privacy.md").read_text(encoding="utf-8")
        lines = [l for l in md.split("\n") if l.startswith("- 账号注销后：")]
        assert len(lines) == 1, f"第六节的注销保留期这一行不唯一：{lines}"
        line = lines[0]
        assert "不可自助恢复" in line, "与 src/ 的口径仍未统一（应是「不可自助恢复」）"
        assert "无法恢复" not in line, "旧的「无法恢复」措辞还在（同一事实两套词汇）"
        # 例外必须就近（支付流水依法留存）
        assert "支付流水" in line
        # 保留期内的真实路径要写清（不是"不可恢复"了事）
        assert "备份" in line and "不保证" in line

    def test_version_note_records_the_change(self):
        md = (REPO / "miniprogram" / "privacy.md").read_text(encoding="utf-8")
        assert "**版本：v1.5" in md, "措辞统一没有记进修订记录（版本号未动）"
        assert "v1.5" in md and "不可自助恢复" in md

    def test_backend_copy_uses_the_same_word(self):
        from src.security.account_copy import DATA_RETENTION_ACTION_NOTICE
        assert "不可自助恢复" in DATA_RETENTION_ACTION_NOTICE
        assert "不可恢复" not in DATA_RETENTION_ACTION_NOTICE


# ══════════════════════════════════════════════════════════════════════
# T8 M-4：文案常量的数字口径（常量数 / 调用点）与实测一致
# ══════════════════════════════════════════════════════════════════════

class TestCopyConstantCountsAreAccurate:
    def _measured(self):
        """实测：模块级文案常量数 + 各常量的**使用点**（AST 的 `ast.Name` 引用，
        import 语句不算、注释与 docstring 里的引述也不算）。"""
        import ast
        names = []
        mod = REPO / "src" / "security" / "account_copy.py"
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and not tgt.id.startswith("__"):
                        names.append(tgt.id)
        sites, files = 0, set()
        for py in sorted((REPO / "src").rglob("*.py")):
            if py.name == "account_copy.py":
                continue
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Name) and node.id in names:
                    sites += 1
                    files.add(str(py.relative_to(REPO)))
        return names, sites, files

    def test_every_constant_has_a_call_site(self):
        names, sites, _files = self._measured()
        assert len(names) >= 4, f"文案常量少了：{names}"
        assert sites >= len(names), f"有常量没有任何使用点：{names} / {sites} 处"

    def test_guard_comments_state_the_measured_numbers(self):
        """守卫里的数字必须与**实测**一致（M-4：陈旧数字就是缺陷）。"""
        names, sites, files = self._measured()
        guard = (REPO / "miniprogram" / "tests" / "k71_privacy_consistency.test.js"
                 ).read_text(encoding="utf-8")
        assert f"**{sites} 处使用点 / {len(files)} 个文件**" in guard, (
            f"守卫注释里的调用点数字与实测不符（实测 {len(names)} 常量 / "
            f"{sites} 使用点 / {len(files)} 文件）—— 请同步那段注释")
        assert f"k79 实测 **{len(names)}**" in guard or f"k79 是 **{len(names)}**" in guard, (
            f"守卫注释里的常量数未跟上实测（实测 {len(names)}）")
