"""k81 回归门禁 —— 修 k80-xss 终验报出的「1 Important + 3 Minor」逐条对应。

覆盖（控制方定规：只要是报的都要修）：
  T1 **必修1（Important）** `性别` 别名表补全 + 归一化行为变更的处置：
     ① 别名表**逐输入**全量对照表（`GENDER_INPUTS`）—— 表驱动，不靠人眼；
     ② 跨实现一致性：`birth_contract.normalize_gender` / `person_dao._normalize_gender`
        / `BaziEngine` / `user._person_birth` **字符串输入逐输入一致**（终验实测
        改前 16/40 不同）；仍允许不同的只剩**非字符串边界**，逐条钉死；
     ③ **根因回归**：`女性` / `女士` 改前 → 引擎按"未知默认男"**顺排**（女性用户
        被当成男性排盘）；改后 → **逆排**（用真实引擎 + 真实 `/api/report/generate`
        实测，不靠读代码）；
     ④ 行为变更的**披露**不许被删：`女性` 是本批（**k81 范围内**）**唯一**排盘结果
        变化的输入，且"存量报告不回溯"的结论必须留在代码里。
        ⚠️ **k82 已扩展**：该批又收了 `女命/女生/女子/女孩`（同样由 unknown+顺排
        修正为女+逆排），累计集合见 `birth_contract` 的 k82 段与
        `tests/test_k82_gender_alias_last.py`。本文件对 k81 的结论**仍然成立**
        （k81 范围内只有 `女性/女士`），不是失效。
  T2 **M1（Minor）** `_REPORT_JSON_CONSUMERS` 门禁的两类逃逸（终验实测，且都
     **不在它自己写的"如实边界"里**）：
       - B：`DIR = 'data/reports'` + `pathlib.Path(DIR) / (rid + '.json')`（静态
         字面量 + 一层局部间接）→ 改前**零命中**，与它声称的"静态可折叠的路径串
         在面内"直接冲突 ⇒ 本批加**常量传播**，B 变红；
       - K/L：`from src.api.share import _load_report` / `s._load_report(rid)`
         → 改前**零命中**（`_load_report` 不在 `CONSUMER_NEEDLES`）⇒ 本批把
         "真读报告 JSON 的 helper"补进表，K/L 变红。
     （注入证明与判据本体在 `tests/test_k80_xss_privacy_final.py::TestConsumerInventoryGateIsNotBypassable`；
      本类只钉"这两个绿案例确实红了"+"边界声明已如实改写"。）
  T3 **M2（Minor）** 顶层剥离清单**不是闭集**：`profile.*` 早有闭集测试，顶层
     字段却是逐个枚举（`owner_enc` 自己就是这么漏的）。本批给顶层也做闭集
     （新增顶层键不表态即红）。
  T4 **M3（Minor）** `owner_enc` 在**本人路径**仍全量下发（`/api/report/{id}` 的
     JSON 与 `/report/{id}` 页面内嵌 JSON）。本批不再下发，**且归属校验不许改坏**
     （本人仍 200、他人仍 403、归属未知仍 403、不存在仍 404 —— 实测）。
  T5 交叉印证：k80 自己那条"gender 白名单"断言**一条都没被放松**。

红线：不触网、不开生产库（报告目录/数据目录全部 monkeypatch 到 tmp，库只用
conftest 的只读快照）；**只新增**，未删改任何既有断言；不新增 skip/xfail。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# T1-①②「逐输入」全量对照表（唯一事实源：这张表就是 k81 的验收对象）
# ══════════════════════════════════════════════════════════════════════

#: (输入, normalize_gender 期望, person_dao._normalize_gender 期望,
#:  user._person_birth 期望, 引擎解读期望)
#:   - 第 2/3/4 列 = 三个后端归一点对**同一个输入**的答案（**必须一致**）；
#:   - 第 5 列 = 引擎最终怎么认（`unknown` ⇒ 计算按男、输出记 unknown，P1-3 既定默认）。
#:   - `person_birth` 的 `None` 不是"未知"而是"**不覆盖既有值**"（update 路径保护约定）。
GENDER_INPUTS = [
    # ── 中文（契约成员）──────────────────────────────────────────────
    ("男", "男", "男", "男", "男"),
    ("女", "女", "女", "女", "女"),
    ("男 ", "男", "男", "男", "男"),          # 空白无关
    (" 女", "女", "女", "女", "女"),
    # ── k81 新增：最常见的中文写法（终验实测的那条）★ ────────────────
    ("男性", "男", "男", "男", "男"),
    ("女性", "女", "女", "女", "女"),
    ("男士", "男", "男", "男", "男"),
    ("女士", "女", "女", "女", "女"),
    ("先生", "男", "男", "男", "男"),
    # ── 英文/大小写 ─────────────────────────────────────────────────
    ("male", "男", "男", "男", "男"),
    ("MALE", "男", "男", "男", "男"),
    ("Male", "男", "男", "男", "男"),
    ("female", "女", "女", "女", "女"),
    ("FEMALE", "女", "女", "女", "女"),
    ("Female", "女", "女", "女", "女"),
    ("m", "男", "男", "男", "男"),
    ("M", "男", "男", "男", "男"),
    ("f", "女", "女", "女", "女"),
    ("F", "女", "女", "女", "女"),
    ("man", "男", "男", "男", "男"),
    ("woman", "女", "女", "女", "女"),
    ("boy", "男", "男", "男", "男"),
    ("girl", "女", "女", "女", "女"),
    ("male1", "男", "男", "男", "男"),        # 历史髒值（旧代码产物）
    # ── 数字（仓库约定 1=男/0=女，见 miniprogram/pages/love/love.js:190）──
    ("1", "男", "男", "男", "男"),
    ("0", "女", "女", "女", "女"),
    # ── "不知道"的写法（**必须停在 unknown，不许猜**）────────────────
    ("unknown", "unknown", "unknown", None, "unknown"),
    ("Unknown", "unknown", "unknown", None, "unknown"),
    ("UNKNOWN", "unknown", "unknown", None, "unknown"),
    ("未知", "unknown", "unknown", None, "unknown"),
    ("性别未知", "unknown", "unknown", None, "unknown"),
    ("", "unknown", "unknown", None, "unknown"),
    ("   ", "unknown", "unknown", None, "unknown"),
    ("none", "unknown", "unknown", None, "unknown"),
    ("null", "unknown", "unknown", None, "unknown"),
    ("nan", "unknown", "unknown", None, "unknown"),
    ("NaN", "unknown", "unknown", None, "unknown"),
    # ── 语义含糊/非性别设计ator：**故意不收**（宁可 unknown，也不猜）──
    # ── k82 必修1 **更正**：`男命/女命` 不属于这一组 ─────────────────────
    # k81 把这两行放在"语义含糊、故意不收"里，理由写"可男可女/语义含糊" ——
    # **理由是错的**：排盘语境里 `命` ＝ 命造/这张盘，`男命/女命` 指**命主的性别**，
    # 是唯一性别指向，与已收的 `男性/女性` 完全对称（终验实测：`女命` 改前 →
    # `unknown` → 顺排，与 `女性` 是同一类 bug）。k82 按明文「收词判据」收入表内，
    # 故这两行的期望值**由 unknown 更正为 男/女**（**不是放宽**：方向是"必须认出来"，
    # 覆盖未减、无 skip/xfail；行数只增不减，见 k82 门禁的交叉断言）。
    ("男命", "男", "男", "男", "男"),
    ("女命", "女", "女", "女", "女"),
    # ── 语义含糊/非性别指示词：**故意不收**（宁可 unknown，也不猜）──
    ("好人", "unknown", "unknown", None, "unknown"),
    ("2", "unknown", "unknown", None, "unknown"),
    ("1.0", "unknown", "unknown", None, "unknown"),
    ("true", "unknown", "unknown", None, "unknown"),
    # ── 攻击载荷（k80 的 Critical 通路）：仍然只能是 unknown ─────────
    ("</script><script>alert(document.cookie)</script>",
     "unknown", "unknown", None, "unknown"),
    ("<img src=x onerror=alert(1)>", "unknown", "unknown", None, "unknown"),
]


class TestGenderAliasTableIsCompleteAndConsistent:
    def test_per_input_table_matches_birth_contract(self):
        """`normalize_gender` 与**全量表**逐输入一致（表就是验收对象）。"""
        from src.api.birth_contract import GENDERS, normalize_gender
        bad = []
        for raw, want, _pd, _pb, _eng in GENDER_INPUTS:
            got = normalize_gender(raw)
            if got != want:
                bad.append(f"{raw!r}: {got!r} != {want!r}")
            assert got in GENDERS, f"{raw!r} → {got!r} 不在白名单"
        assert bad == [], "normalize_gender 逐输入不符：\n  - " + "\n  - ".join(bad)

    def test_per_input_table_matches_person_dao(self):
        """存储层归一**逐输入与 `normalize_gender` 一致**（终验点名：改前 16/40 不同）。

        这一条就是"值集相同、逐输入不同"那个不一致的**修好证明**：两处对同一个
        字符串输入的答案必须逐字相同。
        """
        from src.storage.person_dao import _normalize_gender as pd
        bad = [f"{raw!r}: {pd(raw)!r} != {want!r}"
               for raw, want, _pdb, _pb, _eng in GENDER_INPUTS
               if pd(raw) != want]
        assert bad == [], "person_dao 逐输入与 birth_contract 不一致：\n  - " + \
            "\n  - ".join(bad)

    def test_per_input_table_matches_person_birth(self):
        """`user._person_birth` 的字符串分支同样逐输入一致（**认不出仍是 None**）。"""
        from src.api.user import PersonRequest, _person_birth
        bad = []
        for raw, _want, _pdb, want_pb, _eng in GENDER_INPUTS:
            got = _person_birth(PersonRequest(gender=raw))["gender"]
            if got != want_pb:
                bad.append(f"{raw!r}: {got!r} != {want_pb!r}")
        assert bad == [], "person_birth 逐输入不符：\n  - " + "\n  - ".join(bad)

    def test_non_string_edge_policy_is_deliberate_and_pinned(self):
        """**仍允许不同**的只剩非字符串边界 —— 逐条钉死（防止有人"顺手对齐"）。

        - `normalize_gender` = **入参边界**：缺省＝男（BaziEngine 的历史默认）；
        - `person_dao._normalize_gender` = **存储哨兵**：None/falsy ＝ 未提供 ⇒ unknown，
          **绝不**继承"None → 男"，否则一份缺 gender 的老档案会被认领成"男"。
        """
        from src.api.birth_contract import normalize_gender as ng
        from src.storage.person_dao import _normalize_gender as pd

        # 入参边界（历史口径，k80 未改、k81 也未改）
        assert ng(None) == "男" and ng(True) == "男" and ng(False) == "女"
        assert ng(1) == "男" and ng(0) == "女" and ng(2) == "女"
        # 存储哨兵（"未提供"必须停在 unknown）
        for v in (None, False, 0, 2, object()):
            assert pd(v) == "unknown", f"存储层把 {v!r} 认领成了 {pd(v)!r}"
        assert pd(1) == "男"      # 数字 1（=男）经字符串分支已对齐

    def test_disclosure_of_behavior_change_is_in_the_code(self):
        """行为变更的**披露与受影响清单**不许被删（控制方要求"必须写明"）。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        for must in ("行为变更披露", "不重算", "存量报告", "旧口径", "受影响"):
            assert must in src, f"`normalize_gender` 的披露段缺了「{must}」"
        # "唯一排盘结果变化的是 女性 / 女士" 这条结论必须写明
        assert "`女性` / `女士`" in src or "女性` / `女士`" in src, \
            "「唯一排盘结果发生变化的是 女性/女士」这条结论不在代码里"

    def test_unknown_never_becomes_male_in_the_engine(self):
        """`unknown` 的既有口径不变：计算按男、输出记 `unknown`（P1-3 既定默认）。"""
        from src.engines.bazi import BaziEngine
        r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "unknown")
        assert r.gender == "unknown"
        # 与显式"男"同盘（默认男这条口径没变）
        r_m = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
        assert r.gender != r_m.gender and r.dayun == r_m.dayun


# ══════════════════════════════════════════════════════════════════════
# T1-③ 根因回归：`女性` 的**排盘方向**（真实引擎 + 真实接口）
# ══════════════════════════════════════════════════════════════════════

FEMALE_SPELLINGS = ["女", "女性", "女士", "female", "F", "f", "woman", "girl", "0"]
MALE_SPELLINGS = ["男", "男性", "男士", "先生", "male", "M", "m", "man", "boy", "1"]
UNKNOWN_SPELLINGS = ["unknown", "未知", "", "2"]


def _client(tmp_path, monkeypatch):
    """只挂报告/分享路由的 app（`_DATA_DIR` 指向 tmp；不碰生产库）。"""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.api import share as share_api
    from src.api import visual_report as vr
    from src.security.auth import AuthHandler, set_auth_handler

    monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
    ah = AuthHandler()
    set_auth_handler(ah)
    app = FastAPI()
    app.include_router(vr.router)
    app.include_router(share_api.router)
    return TestClient(app, raise_server_exceptions=False), ah


def _dayun_of(gender, spelling=None):
    """真实引擎：该性别写法排出来的大运（前 4 步干支）。"""
    from src.engines.bazi import BaziEngine
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京",
                               gender if spelling is None else spelling)
    return [gz for _age, gz in r.dayun[:4]]


class TestFemaleSpellingsNowGetFemaleDayun:
    def test_engine_widened_only_in_the_intended_direction(self):
        """引擎侧：所有**女性写法**都排成女命（逆排）、男性写法都顺排、认不出的仍按男。

        改前（k80-）实测：`女性` / `0` / `F` / `girl` / `woman` 全部落在"非女 ⇒ 按男"
        ⇒ **顺排**（终验报告表格的第一行就是这个）。
        """
        male = _dayun_of("男")
        female = _dayun_of("女")
        assert male != female, "本次回归的前提不成立（男女应排出不同大运）"
        for s in MALE_SPELLINGS:
            assert _dayun_of(s) == male, f"{s!r} 没有按男排"
        for s in FEMALE_SPELLINGS:
            assert _dayun_of(s) == female, f"{s!r} 没有按女排（改前就是这里错）"
        for s in UNKNOWN_SPELLINGS:
            assert _dayun_of(s) == male, f"{s!r} 认不出时应仍按男排（P1-3 默认）"

    def test_report_endpoint_per_spelling(self, tmp_path, monkeypatch):
        """端到端：`POST /api/report/generate` 的 `profile.gender` + 大运方向。"""
        client, ah = _client(tmp_path, monkeypatch)
        h = {"Authorization": f"Bearer {ah.create_user_token('k81-owner')}"}

        def gen(g):
            body = {"user_id": "k81-owner",
                    "birth": {"year": 1990, "month": 5, "day": 20, "hour": 12,
                              "minute": 0, "gender": g, "city": "北京", "name": "探针"},
                    "scenario": "overall"}
            r = client.post("/api/report/generate", json=body, headers=h)
            assert r.status_code == 200, r.text
            d = r.json()
            return ((d.get("profile") or {}).get("gender"),
                    [x["ganzhi"] for x in (d.get("bazi_analysis") or {}).get("dayun", [])[:4]])

        male = gen("男")[1]
        female = gen("女")[1]
        for s in FEMALE_SPELLINGS:
            g, du = gen(s)
            assert g == "女", f"{s!r}: 落盘 gender={g!r}（应为 女）"
            assert du == female, f"{s!r}: 大运没按女排 —— {du[:2]}"
        for s in MALE_SPELLINGS:
            g, du = gen(s)
            assert g == "男", f"{s!r}: 落盘 gender={g!r}（应为 男）"
            assert du == male, f"{s!r}: 大运没按男排"
        for s in UNKNOWN_SPELLINGS:
            g, du = gen(s)
            assert g == "unknown", f"{s!r}: 落盘 gender={g!r}（应为 unknown）"

    def test_affected_input_list_is_exactly_female_and_lady(self):
        """★ 结论钉死：**排盘结果发生变化**（男 → 女）的输入只有 `女性` / `女士`。

        依据（本文件同时给实测）：k80 起白名单外的串已统一成 `unknown`（⇒ 排盘仍是
        男，**没修**，正是终验报的那条既有 bug）；k81 补别名后才真正改成女的，
        只有 `女性` / `女士` 这两个**词**（`男性/男士/先生` 的值从 unknown 变准，
        但排盘本来就是男，**结果不变**）。
        """
        male = _dayun_of("男")
        female = _dayun_of("女")
        # 明确断言：这三个"新认出的男性写法"排盘与改前一致（仍是男）
        for s in ("男性", "男士", "先生"):
            assert _dayun_of(s) == male
        # 这两个"新认出的女性写法"排盘**变了**（这是本批的修正点）
        for s in ("女性", "女士"):
            assert _dayun_of(s) == female
        assert male != female


# ══════════════════════════════════════════════════════════════════════
# T2 M1：门禁的两类逃逸已进面内（注入证明）
# ══════════════════════════════════════════════════════════════════════

def _gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "k80_gate", REPO / "tests" / "test_k80_xss_privacy_final.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestConsumerGateEscapesAreNowRed:
    NEW_READER = "src/plugins/new_reader.py"

    def test_escape_b_static_literal_plus_local_indirection(self):
        """终验绿案例 B：局部常量 + 纯静态字面量路径 ⇒ 改后必须红。"""
        scan = _gate().scan_source
        src = ("import pathlib\n"
               "def f(rid):\n"
               "    DIR = 'data/reports'\n"
               "    return pathlib.Path(DIR) / (rid + '.json')\n")
        assert scan(src, self.NEW_READER), "逃逸 B 仍然零命中（常量传播没生效）"

    def test_escape_k_and_l_borrowing_a_registered_helper(self):
        """终验绿案例 K/L：借用已登记模块自己的 helper ⇒ 改后必须红。"""
        scan = _gate().scan_source
        for label, src in [
            ("K from-import", "from src.api.share import _load_report\nr = _load_report(rid)\n"),
            ("L 属性访问", "from src.api import share as s\nr = s._load_report(rid)\n"),
        ]:
            hits = scan(src, self.NEW_READER)
            assert any(h == "_load_report" for _k, h, _l in hits), \
                f"{label}: 借用 `_load_report` 仍然零命中：{hits}"

    def test_control_case_was_already_red(self):
        """对照：`_card_from_report` 本来就在 needles 里 → 红（证明判据本身工作）。"""
        hits = _gate().scan_source(
            "from src.api.share import _card_from_report\nx = _card_from_report(r)\n",
            self.NEW_READER)
        assert hits, "对照案例（改前就红）现在不红了 —— 判据被削弱"

    def test_boundary_statement_no_longer_overclaims(self):
        """终验点名："不在它自己写的如实边界里" —— 边界声明必须**如实改写**。"""
        src = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        head = src[:src.index("_REPORT_JSON_CONSUMERS = (")]
        # 既有的四个必留词（k80 的 test_escape_outside_the_surface_is_listed_honestly 钉着）
        for must in ("仍在面外", "运行期", "跨函数", "拦得住"):
            assert must in head, f"边界声明缺了「{must}」"
        # k81 新增：常量传播的覆盖范围与**不覆盖**范围都要写明
        for must in ("常量传播", "赋值链", "终验逃逸 B", "借用别人的 helper"):
            assert must in head, f"边界声明没如实写「{must}」"

    def test_needles_include_every_real_json_reader(self):
        """needles 必须覆盖**所有真读报告 JSON 的入口**（不只新增的那几个）。"""
        needles = _gate().CONSUMER_NEEDLES
        for name in ("load_report", "load_report_from", "_load_report",
                     "report_path_in", "_report_path", "purge_report_files",
                     "without_report_owner"):
            assert name in needles, f"`{name}` 不在 CONSUMER_NEEDLES 里"


# ══════════════════════════════════════════════════════════════════════
# T3 M2：顶层字段闭集（新增顶层键不表态即红）
# ══════════════════════════════════════════════════════════════════════

class TestTopLevelFieldsAreClassified:
    def test_every_top_level_field_is_classified(self, tmp_path, monkeypatch):
        """判据：**实际生成的报告**顶层键 ⊆ 剥离集 ∪ 保留集（各类都有理由）。

        隔离：`generate_report_data` 会**写盘**，所以先把 `visual_report._DATA_DIR`
        monkeypatch 到 tmp（红线：不碰 `data/reports`，更不碰生产库）。
        """
        from src.api import visual_report as vr
        from src.api.share import _SHARE_KEEP_TOP_FIELDS, _SHARE_REDACT_TOP_FIELDS
        from src.api.visual_report import generate_report_data
        from src.engines.bazi import BaziEngine

        monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
        res = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "女")
        report = generate_report_data(
            res, birth={"year": 1990, "month": 5, "day": 20, "gender": "女"},
            name="张三", owner_uid="k81-owner")
        keys = set(report.keys())
        redact = set(_SHARE_REDACT_TOP_FIELDS)
        keep = set(_SHARE_KEEP_TOP_FIELDS)
        assert keys <= (redact | keep), (
            f"报告里有未表态的**顶层**字段：{sorted(keys - redact - keep)} —— "
            "请判定它是账号/归属/鉴权性质（进 _SHARE_REDACT_TOP_FIELDS）还是"
            "被分享的内容本体（进 _SHARE_KEEP_TOP_FIELDS 并写明理由）")
        assert not (redact & keep), "同一个顶层字段同时出现在剥离集与保留集"
        assert "owner_enc" in redact and "owner_enc" not in keep

    def test_top_level_keep_entries_have_reasons_in_the_source(self):
        """保留集不是空口号：每个字段都要在 share.py 里出现且带理由段。"""
        from src.api.share import _SHARE_KEEP_TOP_FIELDS
        src = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        assert "_SHARE_KEEP_TOP_FIELDS" in src
        for field in _SHARE_KEEP_TOP_FIELDS:
            assert field in src, f"保留集字段 {field} 在 share.py 里找不到"
        # 理由段必须写明"顶层也做闭集"这条（M2 的处置结论）
        assert "顶层" in src and "闭集" in src

    def test_redaction_still_strips_the_whole_top_level_redact_set(self, tmp_path, monkeypatch):
        """闭集扩张后，剥离动作必须**逐个**真的生效（不只 owner_enc）。"""
        from src.api.share import (_SHARE_REDACT_TOP_FIELDS,
                                   _redact_report_for_share)
        report = {"reading_id": "aa000001", "owner_enc": "dev:xxx",
                  "profile": {"name": "张三", "gender": "女"}}
        safe = _redact_report_for_share(report)
        for field in _SHARE_REDACT_TOP_FIELDS:
            assert not safe.get(field), f"顶层 {field} 没被剥离"
        assert report["owner_enc"] == "dev:xxx", "脱敏改了原报告（'不改原 dict' 破了）"


# ══════════════════════════════════════════════════════════════════════
# T4 M3：本人路径不再下发 `owner_enc`；**归属校验不许改坏**
# ══════════════════════════════════════════════════════════════════════

def _write_report(tmp_path, rid, owner_uid="k81-owner", extra=None):
    from src.api.visual_report import _REPORT_OWNER_FIELD
    from src.security.encryption import DataEncryptor
    payload = {"reading_id": rid,
               "generated_at": "2026-09-01T10:00:00",
               "profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯",
                           "day_master": "甲木", "gender": "女"},
               "insights": ["洞察一"]}
    payload.update(extra or {})
    if owner_uid:
        payload[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
    (tmp_path / f"{rid}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


class TestOwnerPathNoLongerShipsOwnerEnc:
    def test_owner_json_has_no_owner_enc(self, tmp_path, monkeypatch):
        """`GET /api/report/{id}`：本人 200，**响应里没有 `owner_enc` 这个键**。"""
        client, ah = _client(tmp_path, monkeypatch)
        disk = _write_report(tmp_path, "aa000001")
        assert disk.get("owner_enc"), "前提不成立：盘上没落 owner_enc"
        r = client.get("/api/report/aa000001",
                       headers={"Authorization": f"Bearer {ah.create_user_token('k81-owner')}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "owner_enc" not in body, f"本人 JSON 仍下发 owner_enc：{body.get('owner_enc')!r}"
        assert "dev:" not in r.text and disk["owner_enc"] not in r.text, \
            "归属密文仍出现在响应体里"
        # 内容本体不受影响（别的字段照旧）
        assert body["profile"]["name"] == "张三"

    def test_owner_page_has_no_owner_enc(self, tmp_path, monkeypatch):
        """`GET /report/{id}`：本人 200，页面（含内嵌 JSON）里没有归属密文。"""
        client, ah = _client(tmp_path, monkeypatch)
        disk = _write_report(tmp_path, "aa000002")
        r = client.get("/report/aa000002",
                       headers={"Authorization": f"Bearer {ah.create_user_token('k81-owner')}"})
        assert r.status_code == 200
        assert disk["owner_enc"] not in r.text, "页面里仍嵌着归属密文"
        assert "owner_enc" not in r.text, "页面里仍出现 owner_enc 键名"

    def test_ownership_check_still_enforced(self, tmp_path, monkeypatch):
        """★ **归属校验没被改坏**：本人 200 / 他人 403 / 归属未知 403 / 不存在 404。"""
        from src.security.auth import AuthHandler
        client, ah = _client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aa000003", owner_uid="k81-owner")
        _write_report(tmp_path, "aa000004", owner_uid=None)       # 老报告：归属未知
        tok = lambda u: {"Authorization": f"Bearer {ah.create_user_token(u)}"}  # noqa: E731

        assert client.get("/api/report/aa000003", headers=tok("k81-owner")).status_code == 200
        assert client.get("/report/aa000003", headers=tok("k81-owner")).status_code == 200
        for path in ("/api/report/aa000003", "/report/aa000003"):
            assert client.get(path, headers=tok("u-other")).status_code == 403, path
            assert client.get(path, headers=tok("k81-owner")).status_code == 200, path
        for path in ("/api/report/aa000004", "/report/aa000004"):
            for u in ("k81-owner", "u-other"):
                assert client.get(path, headers=tok(u)).status_code == 403, (path, u)
        assert client.get("/api/report/nosuch01", headers=tok("k81-owner")).status_code == 404

    def test_share_page_still_renders_and_still_hides_personal_info(self, tmp_path, monkeypatch):
        """既有的分享通道不许因为 M3 退化：匿名可读、仍剥离姓名/性别/归属。"""
        client, ah = _client(tmp_path, monkeypatch)
        disk = _write_report(tmp_path, "aa000005")
        page = client.get("/share/aa000005")
        assert page.status_code == 200
        assert "张三" not in page.text, "分享页出现姓名"
        assert "owner_enc" not in page.text and disk["owner_enc"] not in page.text
        assert page.text.count("<script") == 1
        assert "四柱" in page.text or "REPORT" in page.text

    def test_owner_page_js_still_renders_after_owner_enc_is_stripped(self, tmp_path, monkeypatch):
        """M3 不许把本人页弄白屏：**node 真跑**本人页内嵌 JS（剥了 owner_enc 之后）。

        复用 k80 门禁里的渲染 harness（同一份实现，不抄第二份）——判据与那条
        「200 + 白屏不许出现」完全相同：`html_len > 0` 且不抛异常。
        """
        gate = _gate()
        client, ah = _client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aa000006")
        r = client.get("/report/aa000006",
                       headers={"Authorization": f"Bearer {ah.create_user_token('k81-owner')}"})
        assert r.status_code == 200
        p = tmp_path / "aa000006.html"
        p.write_text(r.text, encoding="utf-8")
        res = gate._render_pages(tmp_path, [p])[0]
        assert res["error"] is None and res["html_len"] > 0, \
            f"本人页内嵌 JS 白屏了：{res}"
        assert res["script_open"] == 1

    def test_without_report_owner_is_pure_and_non_mutating(self):
        """工具函数本身：不改原 dict、不抛、非 dict 原样返回。"""
        from src.api.visual_report import without_report_owner
        src = {"reading_id": "aa1", "owner_enc": "dev:secret", "profile": {"name": "x"}}
        out = without_report_owner(src)
        assert "owner_enc" not in out
        assert src["owner_enc"] == "dev:secret", "改了调用方手里那份 dict"
        assert out["reading_id"] == "aa1"
        assert without_report_owner(None) is None
        assert without_report_owner("x") == "x"
        assert without_report_owner({"a": 1}) == {"a": 1}


# ══════════════════════════════════════════════════════════════════════
# T5 交叉印证：k80 的既有断言一条都没被放松
# ══════════════════════════════════════════════════════════════════════

class TestNoExistingAssertionWasLoosened:
    def test_gender_whitelist_assertions_still_present(self):
        src = (REPO / "tests" / "test_k80_xss_privacy_final.py").read_text(encoding="utf-8")
        for keep in ("def test_gender_whitelist_blocks_every_payload",
                     "assert stored in GENDERS",
                     "def test_share_page_has_exactly_one_script_tag",
                     "def test_redaction_strips_gender_and_owner_enc",
                     "def test_every_profile_field_is_classified"):
            assert keep in src, f"k80 的既有断言被删/改名了：{keep}"

    def test_payloads_cannot_reach_the_page_after_this_batch(self, tmp_path, monkeypatch):
        """本批改了 `normalize_gender` —— 载荷必须**仍然**到不了任何页面。"""
        from src.api.birth_contract import GENDERS
        payloads = ["</script><script>alert(document.cookie)</script>",
                    "<img src=x onerror=alert(1)>", "女性</script>",
                    '女性" onmouseover="alert(1)']
        client, ah = _client(tmp_path, monkeypatch)
        h = {"Authorization": f"Bearer {ah.create_user_token('k81-owner')}"}
        for payload in payloads:
            body = {"user_id": "k81-owner",
                    "birth": {"year": 1990, "month": 5, "day": 20, "hour": 12,
                              "minute": 0, "gender": payload, "city": "北京", "name": payload},
                    "scenario": "overall"}
            d = client.post("/api/report/generate", json=body, headers=h).json()
            assert d["profile"]["gender"] in GENDERS, payload
            rid = d["reading_id"]
            page = client.get(f"/share/{rid}")
            assert page.status_code == 200
            assert page.text.count("<script") == 1, payload
            assert "alert(" not in page.text, payload
            assert payload not in page.text, payload

    def test_no_new_skip_or_xfail(self):
        """红线：不许新增 skip/xfail（本文件与 k80 门禁都在内）。

        用 AST 判**真实的调用/装饰器**（而不是在源码里找字符串）—— 否则这条
        断言会把自己的字面量搜出来（自带假红）。
        """
        import ast as _ast
        banned_call = "skip"
        banned_mark = "xfail"
        for rel in ("tests/test_k81_gender_privacy_final.py",
                    "tests/test_k80_xss_privacy_final.py"):
            tree = _ast.parse((REPO / rel).read_text(encoding="utf-8"))
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute) \
                        and node.func.attr == banned_call:
                    raise AssertionError(f"{rel}:{node.lineno} 新增了 pytest.skip 调用")
                if isinstance(node, _ast.Attribute) and node.attr == banned_mark:
                    raise AssertionError(f"{rel}:{node.lineno} 新增了 xfail 标记")
