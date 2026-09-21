# -*- coding: utf-8 -*-
"""k82 回归门禁 —— **最后一批**：修 k81-gender 终验报出的 3 Minor + 1 卫生问题。

覆盖（控制方定规：**只要是报的，都要修**）：
  T1 **必修1（Minor）** `女命` 未收 + **注释给的理由与事实相反**：
      · 终验实测 `POST /api/report/generate` 送 `女命` → `unknown` → **顺排（按男）**，
        与 k81 修的 `女性/女士` 是**同一类 bug、同一处表**；
      · k81 的表注释把 `男命/女命` 归为"**可男可女/语义含糊**" —— 在八字排盘语境里
        这**恰恰是唯一性别指向**（`命` ＝ 命造，与 `男性/女性` 对称）⇒ **理由是错的**；
      · 本批按明文「收词判据」补 `男命/女命/男生/女生/男子/女子/男孩/女孩`，
        并**逐词给理由**、给**改前/改后逐输入对照**（含真实接口的大运方向）。
  T2 **必修2（Minor）** "全仓盘点"清单不全（`handler.py` 的
      `in ("男","女","male","female")` 漏在清单外）：
      · 该处**已收敛**为 `in ("男","女")`（不可达分支，行为零变化 —— 实测）；
      · ★ 更重要的是**判据换成可复跑的**：`scripts/audit_gender_consumers.py`
        （AST 扫描 + `--check` 门禁），本文件把它当门禁跑，并做**注入证明**。
  T3 **必修3（Minor）** 存储层 int 边界**不对称且源码未列**：
      · `pd(1)` 由 `unknown` → `男`（k81 起，如实写进注释）；
      · `pd(0)`/`pd(2)` 仍 `unknown` —— **判断为保留**（理由：`0` 双重语义 +
        不可达 + 改了会破"绝不替用户认领性别"哨兵），逐条钉死；
      · `bazi.py` 里"`0`=女"的**错误注释**一并更正（实测 int `0` 仍按男排）。
  T4 **必修4（卫生）** 被 git 跟踪的明文数据 + 测试污染被跟踪文件：
      · `data/reports/*.json`（4 个，合成 fixture）**从版本库移除**；
      · `logs/*.log` **取消跟踪**（写进 `.gitignore`，运行期不再污染仓库）；
      · 顺带更正 `visual_report.py` 里"线上 4 份**真实**报告"的**失实引用**。

红线：不触网（LLM 层全部 Mock / 无 key 短路）、**不开生产库**（报告目录全部
monkeypatch 到 tmp）、**不跑全量**、不新增 skip/xfail。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent


def _k81():
    """k81 门禁模块（复用它的**逐输入全量表**与真实接口 harness，不复制一份）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "k81mod_for_k82", REPO / "tests" / "test_k81_gender_privacy_final.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: k82 新增的词（**逐词理由**见 `birth_contract.GENDER_ALIASES` 内注释与表注释）。
K82_MALE = ["男命", "男生", "男子", "男孩"]
K82_FEMALE = ["女命", "女生", "女子", "女孩"]
#: **负向对照**（明确**不收**，必须停在 unknown —— 防止有人"顺手补全"）：
K82_NEGATIVE = ["好人", "男方", "女方", "美女", "帅哥", "男宝宝", "女宝宝",
                "爷们", "娘们", "男女"]


# ══════════════════════════════════════════════════════════════════════
# T1 必修1：收词 + 逐输入对照 + 真实接口的大运方向
# ══════════════════════════════════════════════════════════════════════

class TestNewWordsAreAcceptedEverywhere:
    def test_per_input_all_four_call_sites_agree(self):
        """四个归一点（入参边界/存储/档案/引擎解读）对 k82 新词**逐输入一致**。"""
        from src.api.birth_contract import GENDERS, normalize_gender
        from src.api.user import PersonRequest, _person_birth
        from src.storage.person_dao import _normalize_gender as pd
        bad = []
        for w in K82_MALE + K82_FEMALE:
            want = "男" if w in K82_MALE else "女"
            got_ng, got_pd = normalize_gender(w), pd(w)
            got_pb = _person_birth(PersonRequest(gender=w))["gender"]
            if (got_ng, got_pd, got_pb) != (want, want, want):
                bad.append(f"{w!r}: ng={got_ng!r} pd={got_pd!r} pb={got_pb!r} != {want!r}")
            assert got_ng in GENDERS
        assert bad == [], "k82 新词逐输入不符：\n  - " + "\n  - ".join(bad)

    def test_negative_controls_stay_unknown(self):
        """**不收**的词必须停在 `unknown`（宁可未知，也不猜）。"""
        from src.api.birth_contract import normalize_gender
        from src.storage.person_dao import _normalize_gender as pd
        for w in K82_NEGATIVE:
            assert normalize_gender(w) == "unknown", f"{w!r} 被认领了"
            assert pd(w) == "unknown", f"{w!r} 在存储层被认领了"

    def test_relational_words_reason_is_recorded(self):
        """`男方/女方` 不收的**理由**（关系角色 ≠ 自身属性）必须在源码里写明。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        assert "男方/女方" in src and "关系角色" in src, "`男方/女方` 不收的理由没写进代码"
        assert "intrinsic" in src and "relational" in src, "收词判据的自然语言没写进代码"

    def test_the_old_wrong_reason_is_gone(self):
        """k81 那条**错误理由**（`男命/女命` 属"可男可女/语义含糊"）不许还在。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        assert "`男命/女命`、`好人` 这类**故意不收**" not in src
        assert "不含任何\"可男可女/语义含糊\"的词" not in src
        # 新理由必须在位：这个理由**是错的**这件事本身要留痕（防止又被改回去）
        assert "这个理由是错的" in src

    def test_k81_table_rows_were_corrected_not_removed(self):
        """k81 那张"逐输入全量表"**没有被删行**，`男命/女命` 是**更正**不是放宽。

        覆盖不减（行数只增不减）+ 更正方向是"必须认出来"（比原来更严）。
        """
        k81 = _k81()
        rows = k81.GENDER_INPUTS
        by_input = {r[0]: r for r in rows}
        # 原 k81 表里就有这两行（不是本批新加的）—— 只是期望值被更正
        assert by_input["男命"][1:] == ("男", "男", "男", "男"), \
            f"`男命` 的期望没被更正：{by_input['男命']}"
        assert by_input["女命"][1:] == ("女", "女", "女", "女"), \
            f"`女命` 的期望没被更正：{by_input['女命']}"
        # 负向对照仍在（`好人` 一行不许被顺手删掉）
        assert by_input["好人"][1:] == ("unknown", "unknown", None, "unknown")
        assert len(rows) >= 40, f"k81 表被删行了（只剩 {len(rows)} 行）"

    def test_gender_inputs_table_still_passes_for_every_row(self):
        """把 k81 全量表**整表**再跑一遍（更正后的 43 行逐输入全绿）。"""
        from src.api.birth_contract import normalize_gender
        from src.api.user import PersonRequest, _person_birth
        from src.storage.person_dao import _normalize_gender as pd
        bad = []
        for raw, want, want_pd, want_pb, _eng in _k81().GENDER_INPUTS:
            if normalize_gender(raw) != want:
                bad.append(f"ng({raw!r}) != {want!r}")
            if pd(raw) != want_pd:
                bad.append(f"pd({raw!r}) != {want_pd!r}")
            if _person_birth(PersonRequest(gender=raw))["gender"] != want_pb:
                bad.append(f"pb({raw!r}) != {want_pb!r}")
        assert bad == [], "全量表回归失败：\n  - " + "\n  - ".join(bad)


class TestEngineDirectionForNewWords:
    def _dayun(self, g):
        k81 = _k81()
        return k81._dayun_of(g)

    def test_engine_only_the_intended_four_female_words_changed(self):
        """引擎侧：4 个新女词**改成逆排**；4 个新男词**排盘方向不变**（本来就按男）。"""
        male, female = self._dayun("男"), self._dayun("女")
        assert male != female, "前提不成立（男女大运应不同）"
        for w in K82_FEMALE:
            assert self._dayun(w) == female, f"{w!r} 没按女排（改前就是这里错）"
        for w in K82_MALE:
            assert self._dayun(w) == male, f"{w!r} 没按男排"
        for w in K82_NEGATIVE:
            assert self._dayun(w) == male, f"{w!r} 认不出时应仍按男排（P1-3 默认）"

    def test_report_endpoint_per_input_before_after_table(self, tmp_path, monkeypatch):
        """★ 端到端：`POST /api/report/generate` 的 `profile.gender` + 大运方向。

        这就是报告里那张"改前/改后逐输入对照"的**可复跑版本**：
        `女命/女生/女子/女孩` 改前是 `unknown` + **顺排（按男）**，改后必须
        `女` + **逆排**；`男命/男生/男子/男孩` 改前 `unknown` + 顺排，改后 `男` +
        **顺排不变**（值变准、排盘结果不变）；负向对照仍 `unknown` + 顺排。
        """
        k81 = _k81()
        client, ah = k81._client(tmp_path, monkeypatch)
        h = {"Authorization": f"Bearer {ah.create_user_token('k82-owner')}"}

        def gen(g):
            body = {"user_id": "k82-owner",
                    "birth": {"year": 1990, "month": 5, "day": 20, "hour": 12,
                              "minute": 0, "gender": g, "city": "北京", "name": "探针"},
                    "scenario": "overall"}
            r = client.post("/api/report/generate", json=body, headers=h)
            assert r.status_code == 200, r.text
            d = r.json()
            return (str(((d.get("profile") or {}).get("gender"))),
                    [x["ganzhi"] for x in
                     (d.get("bazi_analysis") or {}).get("dayun", [])[:4]])

        male, female = gen("男")[1], gen("女")[1]
        assert male != female
        for w in K82_FEMALE:
            g, du = gen(w)
            assert (g, du) == ("女", female), f"{w!r}: 落盘 {g!r} / 大运 {du[:2]}（应 女 + 逆排）"
        for w in K82_MALE:
            g, du = gen(w)
            assert (g, du) == ("男", male), f"{w!r}: 落盘 {g!r} / 大运 {du[:2]}（应 男 + 顺排）"
        for w in K82_NEGATIVE:
            g, du = gen(w)
            assert (g, du) == ("unknown", male), \
                f"{w!r}: 落盘 {g!r} / 大运 {du[:2]}（应 unknown + 顺排）"

    def test_disclosure_lists_the_k82_changed_inputs(self):
        """行为变更披露必须含 k82 的结论（排盘变化的 4 个女词 + 累计集合）。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        for must in ("女命", "女生", "女子", "女孩"):
            assert must in src
        assert "k82 排盘结果发生变化的是" in src, "k82 的排盘变化结论没写进代码"
        assert "累计" in src, "累计受影响集合没写进代码"


# ══════════════════════════════════════════════════════════════════════
# T2 必修2：handler 那处收敛 + **可复跑判据**（本轮真正的修法）
# ══════════════════════════════════════════════════════════════════════

def _audit():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "k82_audit", REPO / "scripts" / "audit_gender_consumers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGenderConsumerInventoryIsReproducible:
    def test_gate_is_green(self):
        """全仓盘点门禁：每一处性别消费点都已表态（清单是**跑出来的**，不是人记的）。"""
        mod = _audit()
        added, removed = mod.check(mod.collect())
        assert added == [], "有未表态的性别消费点：\n  - " + "\n  - ".join(added)
        assert removed == [], "已表态的站点消失了（请更新清单）：\n  - " + "\n  - ".join(removed)

    def test_gate_is_not_vacuous_new_table_and_new_set_go_red(self, tmp_path):
        """**注入证明**：新别名表 / 新集合判定都必须变红（否则门禁等于没有）。"""
        mod = _audit()
        inject = tmp_path / "src"
        inject.mkdir()
        (inject / "probe.py").write_text(
            '_ALIAS = {"male": "男", "female": "女", "女命": "女"}\n'
            'def pick(g):\n'
            '    return "女" if g in ("female", "女士", "女命") else "男"\n',
            encoding="utf-8")
        hits = mod.collect(tmp_path)
        kinds = {h["kind"] for h in hits}
        assert "table-in" in kinds, "注入的别名表没被识别"
        assert "membership" in kinds, "注入的集合判定没被识别"
        added, _ = mod.check(hits)
        assert any("probe.py" in a for a in added), "注入的消费点没有变红（门禁是空的）"

    def test_gate_ignores_data_tables_and_dream_words(self, tmp_path):
        """**判据的边界**：宿名表/名字库/梦象词表这类"同形词数据"不许被误报。"""
        mod = _audit()
        d = tmp_path / "src"
        d.mkdir()
        (d / "data_tables.py").write_text(
            'XIUNIAN = {"角": "吉", "女": "凶", "虚": "凶"}\n'      # 二十八宿（单性别 key）
            'DREAM = {"name": "男孩", "match": "男孩"}\n'            # 梦象词表（值侧）
            'NAMES = {"善": {"wx": "金"}}\n',                        # 取名用字库
            encoding="utf-8")
        hits = mod.collect(tmp_path)
        assert hits == [], f"同形词数据表被误报：{hits}"

    def test_gate_detects_the_real_repo_sites(self):
        """正向对照：判据在**真仓库**上必须看到已知的那几处（不是空跑）。"""
        mod = _audit()
        keys = set(mod.collapsed(mod.collect()))
        assert any("birth_contract.py" in k and "table-in" in k for k in keys), \
            "没看到唯一事实源别名表"
        # `_GENDER_CN` 是**模块级** dict ⇒ 作用域显示为 `<module>`（key 不带行号）
        assert any("bot/handler.py" in k and "table-in" in k for k in keys), \
            "没看到 `bot/handler.py` 的 `_GENDER_CN`（k81 漏掉的那张表）"
        assert any("_gen_instant_reply" in k for k in keys), \
            "没看到 `_gen_instant_reply`（终验点名的那处）"

    def test_handler_instant_reply_converged_and_behaviour_identical(self):
        """`_gen_instant_reply` 已收敛为 `("男","女")`，且**可达输入逐输入零变化**。"""
        src = (REPO / "src" / "bot" / "handler.py").read_text(encoding="utf-8")
        assert 'if _ig in ("男", "女"):' in src, "handler 的字面量集合没有收敛"
        assert '_ig in ("男", "女", "male", "female")' not in src, "旧集合还在"

        # 可达值集上，**旧实现**与**收敛后实现**产出的 prompt 片段逐字相同。
        def note_old(g):        # k81 的实现（收敛前）
            if g in ("男", "女", "male", "female"):
                note = f"用户性别：{'男' if g in ('男', 'male') else '女'}；"
                if g in ("男", "male"):
                    note += ("使用中性称谓（你/朋友），"
                             "禁止女性称谓与闺蜜口吻（姐妹/亲爱的等）。")
                return note
            return ""

        def note_new(g):        # k82 收敛后的实现
            if g in ("男", "女"):
                note = f"用户性别：{g}；"
                if g == "男":
                    note += ("使用中性称谓（你/朋友），"
                             "禁止女性称谓与闺蜜口吻（姐妹/亲爱的等）。")
                return note
            return ""

        # ① **可达值集**（引擎输出全集）：逐值**逐字相同** ⇒ 行为零变化
        for g in ("男", "女", "unknown"):
            assert note_old(g) == note_new(g), \
                f"{g!r} 的 prompt 片段变了：{note_old(g)!r} vs {note_new(g)!r}"
        # ② 差异**只**发生在不可达值上（`male`/`female` 引擎永不产出，见
        #    `test_engine_never_emits_english_gender`）—— 这正是"收敛无害"的边界。
        #    注意两项实现在这里**都不带 `.lower()`**，故大小写混写（`Male`）本来
        #    就两边都不命中 ⇒ 不在差异集里。
        for g in ("male", "female"):
            assert note_old(g) != "" and note_new(g) == "", \
                f"{g!r}: 差异不在不可达值上（前提变了，收敛需要重评）"
        for g in ("Male", "MALE", "男命"):
            assert note_old(g) == note_new(g) == "", f"{g!r}: 两边本都不该命中"

    def test_handler_behaviour_end_to_end_via_real_method(self):
        """跑**真方法体**：喂 男/女/unknown 的 engine 结果，抓 prompt 逐字比对。"""
        from unittest.mock import Mock
        from src.bot.handler import MessageHandler
        from src.engines.bazi import BaziEngine

        def prompt_for(gender):
            h = object.__new__(MessageHandler)
            h._quick_flash = Mock(return_value="OK")
            result = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", gender)
            h._gen_instant_reply(result)
            assert h._quick_flash.called, f"{gender!r} 没走 LLM 路径"
            return h._quick_flash.call_args[0][0]

        p_m, p_f, p_u = (prompt_for(g) for g in ("男", "女", "unknown"))
        assert "用户性别：男；" in p_m and "禁止女性称谓" in p_m
        assert "用户性别：女；" in p_f and "禁止女性称谓" not in p_f
        assert "用户性别" not in p_u, "unknown 不该注入性别断言语"

    def test_engine_never_emits_english_gender(self):
        """收敛的**前提**：引擎输出恒为 `男/女/unknown`（`male/female` 不可达）。"""
        from src.engines.bazi import BaziEngine
        for g in ("男", "女", "unknown", "male", "female", "M", "F", "女命",
                  "男生", "0", "1", "2", "女性", ""):
            got = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", g).gender
            assert got in ("男", "女", "unknown"), f"{g!r} → {got!r}（引擎跑出了英文/别的值）"

    def test_residual_table_list_is_the_ast_inventory(self):
        """`birth_contract` 的"残留的表"清单必须**含 k82 新盘出来的 3 处**。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        assert "_GENDER_CN" in src, "第 5 张表 `_GENDER_CN` 没进清单"
        assert "engines/ming.py::_GENDER_TAG" in src, "`_GENDER_TAG` 没进清单"
        assert "_gen_instant_reply" in src, "终验点名的 handler 那处没进清单"
        assert "audit_gender_consumers" in src, "清单没指向可复跑判据"


# ══════════════════════════════════════════════════════════════════════
# T3 必修3：存储层 int 边界不对称 —— 如实披露 + **保留**的判断
# ══════════════════════════════════════════════════════════════════════

class TestIntBoundaryAsymmetryIsDisclosed:
    def test_asymmetry_is_pinned_row_by_row(self):
        from src.api.birth_contract import gender_of_alias as ga
        from src.api.birth_contract import normalize_gender as ng
        from src.storage.person_dao import _normalize_gender as pd
        # 入参边界：int != 1 即女（历史口径）
        assert ng(1) == "男" and ng(0) == "女" and ng(2) == "女"
        # 存储哨兵：falsy ＝ 未提供；**只有 int 1 对齐**
        assert pd(1) == "男"          # ★ k81 起由 unknown 变成 男
        for v in (None, False, 0, 2, object()):
            assert pd(v) == "unknown", f"存储层把 {v!r} 认领成了 {pd(v)!r}"
        # `ga` 层（查表入口）的不对称来源：falsy 折成 ""
        assert ga(1) == "男" and ga(0) == "unknown" and ga(2) == "unknown"
        assert ga("1") == "男" and ga("0") == "女" and ga("2") == "unknown"
        # 字符串两侧对称（"0"/"1" 都认），int 侧只有 1 认 —— 这就是那处不对称
        assert (ga("0"), ga(0)) == ("女", "unknown")

    def test_disclosure_says_pd1_changed_from_unknown_to_male(self):
        """`pd(1)` 的**变化**（unknown → 男）必须写明；且"保留不对称"的判断在位。"""
        src = (REPO / "src" / "api" / "birth_contract.py").read_text(encoding="utf-8")
        assert "由 `unknown` **变成** `男`" in src, "pd(1) 的变化没写明"
        assert "判断为保留" in src, "保留/修改的判断没写明"
        assert "绝不替用户认领性别" in src, "保留的核心理由没写明"

    def test_http_int_gender_is_422_so_the_branch_is_unreachable(self, tmp_path, monkeypatch):
        """**可达性如实标注**：HTTP 层送数字 gender ⇒ pydantic 422（不可达）。"""
        k81 = _k81()
        client, ah = k81._client(tmp_path, monkeypatch)
        h = {"Authorization": f"Bearer {ah.create_user_token('k82-reach')}"}
        for bad in (0, 1, 2, True):
            body = {"user_id": "k82-reach",
                    "birth": {"year": 1990, "month": 5, "day": 20, "hour": 12,
                              "minute": 0, "gender": bad, "city": "北京", "name": "探针"},
                    "scenario": "overall"}
            r = client.post("/api/report/generate", json=body, headers=h)
            assert r.status_code == 422, \
                f"gender={bad!r} 竟然不是 422（则 int 边界可达，判断需重做）：{r.status_code}"

    def test_bazi_comment_corrected_and_int_zero_really_is_male_ordered(self):
        """`bazi.py` 里"`0`=女"的注释是**错的** —— 已更正，且实测 int 0 仍按男排。"""
        src = (REPO / "src" / "engines" / "bazi.py").read_text(encoding="utf-8")
        # 原句逐字（含前后的标点）必须消失；k82 的更正段里**引用**了旧句，故判据
        # 取带上下文的整句，而不是那句被引用的短语。
        assert "直接 AttributeError（`1.strip()`），现在按别名表" not in src, "错误注释还在"
        assert "k82 必修3 更正" in src, "更正留痕不在"
        from src.engines.bazi import BaziEngine
        r0 = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", 0)
        rm = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
        assert r0.gender == "unknown" and r0.dayun == rm.dayun, "int 0 不是按男排（注释更正错了）"
        rs = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "0")
        assert rs.gender == "女" and rs.dayun != rm.dayun, "字符串 '0' 应认出女（逆排）"


# ══════════════════════════════════════════════════════════════════════
# T4 必修4：仓库卫生（移除后**不许**再被跟踪回来）
# ══════════════════════════════════════════════════════════════════════

def _git(*args):
    out = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                         text=True, check=True)
    return out.stdout


class TestRepoHygieneNoPlaintextTrackedData:
    def test_no_report_json_in_the_worktree(self):
        """4 个合成报告 JSON 已从版本库移除（工作树里只剩 `.gitkeep`）。"""
        left = sorted(p.name for p in (REPO / "data" / "reports").glob("*.json"))
        assert left == [], f"仍有报告 JSON：{left}"

    def test_reports_and_logs_are_not_tracked(self):
        """`data/reports/*.json` 与 `logs/*.log` **不在 git 索引里**。"""
        tracked = _git("ls-files", "data/reports", "logs").split()
        bad = [t for t in tracked if t.endswith(".json") or t.endswith(".log")]
        assert bad == [], f"仍被跟踪的运行期文件：{bad}"

    def test_gitignore_covers_logs_and_reports_json(self):
        """`.gitignore` 必须能盖住运行期日志/报告（否则下次 `git add .` 又进去）。"""
        text = (REPO / ".gitignore").read_text(encoding="utf-8")
        for pat in ("logs/", "*.log", "data/reports/*.json"):
            assert pat in text, f".gitignore 缺 {pat!r}"

    def test_audit_log_path_env_redirects_writes_to_tmp(self, tmp_path, monkeypatch):
        """运行期审计写入走**可重定向的**路径（k54/k53 已用 `AUDIT_LOG_PATH`）。

        本用例钉两件事：① 该开关存在且**真的生效**（记录落进 tmp）；
        ② 生效时**不碰仓库路径** —— 审计日志本来就是运行期产物，不该污染仓库。
        """
        from src.security.audit import AuditLogger
        target = tmp_path / "audit.log"
        monkeypatch.setenv("AUDIT_LOG_PATH", str(target))
        lg = AuditLogger()
        assert lg.log_path == str(target), f"AUDIT_LOG_PATH 没生效：{lg.log_path}"
        lg.log("data_access", "u-hygiene", "127.0.0.1", "pytest", "success")
        assert target.exists(), "审计记录没写进被重定向的路径"
        assert "u-hygiene" in target.read_text(encoding="utf-8")

    def test_purge_report_files_still_skips_ownership_unknown(self, tmp_path):
        """移除那 4 个文件**不改行为**：没有 `owner_enc` 的报告仍一律不动。"""
        from src.storage.dao import purge_report_files
        rep, ch = tmp_path / "reports", tmp_path / "charts"
        rep.mkdir()
        ch.mkdir()
        (rep / "deadbeef.json").write_text(
            json.dumps({"reading_id": "deadbeef",
                        "profile": {"name": "测试用户", "gender": "男"}},
                       ensure_ascii=False), encoding="utf-8")
        stats = purge_report_files("u-nobody", reports_dir=rep, charts_dir=ch)
        assert stats["reports"] == 0 and stats["share_cards"] == 0
        assert (rep / "deadbeef.json").exists(), "归属未知的报告被误删了"

    def test_visual_report_no_longer_cites_real_production_reports(self):
        """`visual_report.py` 里"线上 4 份**真实**报告"是**失实引用**，已更正。"""
        src = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        assert "线上 4 份真实报告" not in src, "失实引用还在"
        assert "k82" in src and "合成" in src, "更正说明不在"

    def test_no_gender_audit_script_drift(self):
        """脚本本身可独立运行（`--check` 退出码 0）——不依赖测试进程的 import 次序。"""
        r = subprocess.run([sys.executable,
                            str(REPO / "scripts" / "audit_gender_consumers.py"),
                            "--check"], cwd=str(REPO), capture_output=True, text=True)
        assert r.returncode == 0, f"盘点脚本独立运行失败：{r.stdout}{r.stderr}"
