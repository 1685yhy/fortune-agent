"""k80 隐私/安全**最后一批**回归门禁 —— 与 k79-final2 终验报出的
「1 Critical + 2 Important + 5 Minor」逐条对应（控制方定规：只要是报的都要修）。

覆盖：
  T1 **必修1（Critical · 存储型 XSS）** `POST /api/report/generate` 的 `gender`
     无校验 → 落盘 → 匿名公开页 `/share/{id}` 上执行攻击者 JS。**多层都修**：
       ① 根因层：`gender` 白名单（`birth_contract.normalize_gender` 收敛到
          `男/女/unknown`，白名单外一律 unknown，不再透传任意串）；
       ② 输出编码层：JSON 内嵌 `<script>` 前转义 `< > & U+2028 U+2029 $`
          （`json_for_script`；**覆盖所有字段**，不只 gender）；
       ③ 脱敏层：`_SHARE_REDACT_PROFILE_FIELDS` 逐字段重审 —— `gender` 补进清单，
          顶层 `owner_enc`（账号标识密文）也剥离；
       ④ 渲染汇点：页面内嵌 JS 的 `innerHTML` 写入点逐个 `esc()`（只做脚本标签
          转义挡不住 `<img onerror>` 这条 DOM XSS 通路）；
       ⑤ 同类排查：`profile.name` → `<title>`/og 标签（HTML 上下文）同样转义。
  T2 **必修2（Important）** 内嵌 JS **元素层**兜底：k79 只补了容器层，
     `charts.wuxing_radar=[1,"x",null]` 仍 TypeError 白屏。判据
     「200 + 白屏」不许出现 —— 用 **node 真跑 `render()`** 实测（覆盖终验列的
     全部形态 + k79 的畸形语料）。
  T3 **必修3（Important）** `_REPORT_JSON_CONSUMERS` 的 AST 门禁可绕过：
     扩大扫描面（`scripts/`、仓根、`src/plugins/`）+ 加**形状判据**（路径拼接、
     `os.path.join`、`getattr` 动态名）。注入证明：改前绿 / 改后红。
  T4 **M1/M2** 禁语宽面的"有界例外机制"与"消灭手写数字"（守卫侧实现在
     `miniprogram/tests/k71_privacy_consistency.test.js`，本文件钉住接线与边界）。
  T5 **M3** 「路径唯一实现」名副其实：`f"{reading_id}.json"` 只允许出现在
     `report_path_in` 一处（写盘也走它）。
  T6 **M4** JS 侧漏字：`rec.action` 出 `[object Object]`、`annual_trend` 缺 score
     出 `MNaN,NaN` —— 由 T2 的 node 实测钉住（`has_object_object` / `has_nan`）。
  T7 **M5** `/api/share/{id}` 的分派判据：全数字的 8 位 reading_id
     （概率≈2.33%）被误当咨询 ID → 503/404。改成"哪一边真的有东西"。

红线：不触网、不开生产库（库/报告目录全部 monkeypatch 到 tmp）；本文件**只新增**，
未删改任何既有断言；不新增 skip/xfail。node / python3 缺失即**报错**（不静默降级）。
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent

#: k80-必修1：`gender` 的存储型 XSS 载荷（终验原样）+ 同类 variant。
XSS_PAYLOADS = [
    ("终验原样（脚本闭合）", "</script><script>alert(document.cookie)</script>"),
    ("DOM 汇点（事件属性）", "<img src=x onerror=alert(document.cookie)>"),
    ("HTML 属性逃逸", '" onmouseover="alert(1)'),
    ("标题闭合", "</title><script>alert(1)</script>"),
    ("实体+反引号", "`${alert(1)}`&amp;"),
    ("行分隔符 U+2028", "男\u2028alert(1)"),
    ("模板占位符", "$og_title$share_url"),
    ("对照：正常值", "男"),
]


def _client(tmp_path, monkeypatch, dao=None):
    """只挂报告/分享路由的 app（两个模块的 `_DATA_DIR` 都指向 tmp；不碰生产库）。"""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.api import share as share_api
    from src.api import visual_report as vr
    from src.security.auth import AuthHandler, set_auth_handler

    monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(share_api, "_dao", dao)
    ah = AuthHandler()
    set_auth_handler(ah)
    app = FastAPI()
    app.include_router(vr.router)
    app.include_router(share_api.router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, {"Authorization": f"Bearer {ah.create_user_token('k80-owner')}"}


def _generate(client, headers, gender="男", name="张三"):
    body = {"user_id": "k80-owner",
            "birth": {"year": 1990, "month": 5, "day": 20, "hour": 12, "minute": 0,
                      "gender": gender, "city": "北京", "name": name},
            "scenario": "overall"}
    r = client.post("/api/report/generate", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _write(tmp_path: Path, rid: str, payload, owner_uid: str = "k80-owner") -> Path:
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


# ══════════════════════════════════════════════════════════════════════
# T1 必修1（Critical）：存储型 XSS —— 多层都修
# ══════════════════════════════════════════════════════════════════════

class TestGenderPayloadCannotReachAnyPage:
    def test_gender_whitelist_blocks_every_payload(self, tmp_path, monkeypatch):
        """根因层：`gender` 落盘值必须是白名单成员（改前：载荷原样落盘）。"""
        from src.api.birth_contract import GENDERS, normalize_gender
        client, h = _client(tmp_path, monkeypatch)
        for label, payload in XSS_PAYLOADS:
            data = _generate(client, h, gender=payload)
            stored = (data.get("profile") or {}).get("gender")
            assert stored in GENDERS, f"{label}: 落盘 gender={stored!r}（不在白名单）"
            # 白名单之外的一切 → unknown（"未知"是受控值，不是把用户串转发下去）
            expect = payload if payload in GENDERS else (
                "男" if payload == "男" else "unknown")
            assert stored == expect, f"{label}: {stored!r} != {expect!r}"
            disk = json.loads((tmp_path / f"{data['reading_id']}.json").read_text("utf-8"))
            assert (disk.get("profile") or {}).get("gender") in GENDERS, label

    def test_share_page_has_exactly_one_script_tag(self, tmp_path, monkeypatch):
        """匿名公开页：`<script` 计数必须为 1（改前终验实测 2 = 攻击者脚本进去了）。"""
        client, h = _client(tmp_path, monkeypatch)
        for label, payload in XSS_PAYLOADS:
            rid = _generate(client, h, gender=payload)["reading_id"]
            page = client.get(f"/share/{rid}")
            assert page.status_code == 200, label
            assert page.text.count("<script") == 1, (
                f"{label}: 分享页出现 {page.text.count('<script')} 个脚本标签（应为 1）")
            assert page.text.count("</script") == 1, label
            if payload not in ("男",):
                assert payload not in page.text, f"{label}: 载荷原样出现在页面上"
                assert "alert(" not in page.text, f"{label}: 页面上存在可执行载荷"

    def test_owner_page_has_exactly_one_script_tag(self, tmp_path, monkeypatch):
        """本人页 `/report/{id}`：`profile.name` → `<title>`/og 也是 XSS 通路（同类）。"""
        client, h = _client(tmp_path, monkeypatch)
        for label, payload in XSS_PAYLOADS:
            rid = _generate(client, h, gender="男", name=payload)["reading_id"]
            page = client.get(f"/report/{rid}", headers=h)
            assert page.status_code == 200, label
            assert page.text.count("<script") == 1, (
                f"{label}: 本人页出现 {page.text.count('<script')} 个脚本标签（应为 1）")
            assert "</title><script>" not in page.text, f"{label}: 标题被闭合逃逸"
            # 标题内容里**不许出现任何原始尖括号**（出现了就说明没转义 → 可闭合）
            title = page.text.split("<title>", 1)[1].split("</title>", 1)[0]
            assert "<" not in title and ">" not in title, (
                f"{label}: `<title>` 内容里有未转义的尖括号：{title[:80]!r}")

    def test_script_encoder_covers_every_field_not_just_gender(self):
        """输出编码层是**通用**修法：任意字段变脏都逃不出 `<script>`。"""
        from src.api.visual_report import json_for_script, js_string_literal
        payload = "</script><script>alert(document.cookie)</script>"
        for probe in ({"profile": {"gender": payload}},
                      {"insights": [payload]},
                      {"future_field": {"deep": [payload, {"x": payload}]}}):
            text = json_for_script(probe)
            assert "</script" not in text and "<script" not in text, text
            assert "\\u003c/script" in text
            # 反解后语义不变（转义是"编码"不是"丢字符"）
            assert json.loads(text) == probe
        # 行分隔符：老引擎里出现在字符串字面量中即 SyntaxError（整页白屏）
        assert "\\u2028" in json_for_script({"a": "x\u2028y\u2029z"})
        # `$`：本页模板替换用 string.Template，用户数据里的 `$og_title` 会被二次替换
        assert "$og_title" not in json_for_script({"a": "$og_title"})
        # JS 字符串字面量：反斜杠/换行/引号都必须编码（改前只换 `"`）
        lit = js_string_literal('a\\b\n"c"</script>')
        assert json.loads(lit) == 'a\\b\n"c"</script>'
        assert "</script" not in lit

    def test_redaction_strips_gender_and_owner_enc(self, tmp_path, monkeypatch):
        """脱敏层：公开副本不留个人信息，也不留账号标识密文；**不改原 dict**。"""
        from src.api.share import (_SHARE_REDACT_PROFILE_FIELDS,
                                   _SHARE_REDACT_TOP_FIELDS,
                                   _redact_report_for_share)
        client, h = _client(tmp_path, monkeypatch)
        data = _generate(client, h, gender="男", name="张三")
        disk = json.loads((tmp_path / f"{data['reading_id']}.json").read_text("utf-8"))
        assert disk.get("owner_enc"), "本报告没落 owner_enc（测试前提不成立）"
        before = json.dumps(disk, ensure_ascii=False, sort_keys=True)
        safe = _redact_report_for_share(disk)
        assert json.dumps(disk, ensure_ascii=False, sort_keys=True) == before, \
            "脱敏改了**原报告**（「不改原 dict」这条契约破了）"
        for field in _SHARE_REDACT_PROFILE_FIELDS:
            assert (safe.get("profile") or {}).get(field, "") in ("", "用户"), field
        for field in _SHARE_REDACT_TOP_FIELDS:
            assert not safe.get(field), f"顶层 {field} 没被剥离"
        assert "gender" in _SHARE_REDACT_PROFILE_FIELDS, \
            "gender 不在公开页剥离清单里（privacy.md 第 2 条把它列为收集的个人信息）"
        assert "owner_enc" in _SHARE_REDACT_TOP_FIELDS
        # 内容本体仍在（分享通道承载的是八字结论，不是个人信息）
        assert (safe.get("profile") or {}).get("bazi")

    def test_every_profile_field_is_classified(self, tmp_path, monkeypatch):
        """脱敏清单是**闭集**：报告新出现一个 profile 字段而不表态 → 红。

        这是"你怎么保证没有别的字段该剥"的机器答案：不靠人记，靠
        「实际生成的 profile 键 == 剥离集 ∪ 保留集（各自附理由）」。
        """
        from src.api.share import _SHARE_KEEP_PROFILE_FIELDS, _SHARE_REDACT_PROFILE_FIELDS
        client, h = _client(tmp_path, monkeypatch)
        data = _generate(client, h)
        keys = set((data.get("profile") or {}).keys())
        redact, keep = set(_SHARE_REDACT_PROFILE_FIELDS), set(_SHARE_KEEP_PROFILE_FIELDS)
        assert keys <= (redact | keep), (
            f"报告 profile 里有未表态的字段：{sorted(keys - redact - keep)} —— "
            "请判定它是个人信息（进 _SHARE_REDACT_PROFILE_FIELDS）还是内容本体"
            "（进 _SHARE_KEEP_PROFILE_FIELDS 并写明理由）")
        assert not (redact & keep), "同一个字段同时出现在剥离集与保留集"
        src = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        for field in _SHARE_KEEP_PROFILE_FIELDS:
            assert field in src, field


# ══════════════════════════════════════════════════════════════════════
# T2 必修2（Important）：内嵌 JS 元素层兜底 —— node 真跑 render()
# ══════════════════════════════════════════════════════════════════════

#: node 里真执行页面内嵌 `<script>`，把 render() 的结果抓下来。
#: 判据 = 「要么不白屏（html_len>0 且无异常），要么被判为不可展示（不是 200）」。
_RENDER_HARNESS = r"""
const fs = require('fs');
function extractScript(html) {
  const re = /<script[^>]*>([\s\S]*?)<\/script>/g;
  let m, last = null;
  while ((m = re.exec(html)) !== null) last = m[1];
  return last;
}
function run(file) {
  const html = fs.readFileSync(file, 'utf8');
  const src = extractScript(html);
  const out = { file: file, script_open: (html.match(/<script/g) || []).length,
                script_len: src ? src.length : 0, html_len: 0, error: null,
                raw_img_onerror: false, object_object: false, nan: false };
  if (!src) { out.error = 'no-script'; return out; }
  let captured = null;
  const appEl = {};
  Object.defineProperty(appEl, 'innerHTML',
    { set(v) { captured = v; }, get() { return captured; } });
  const doc = { getElementById: (id) => (id === 'app' ? appEl : null),
                querySelector: () => null, createElement: () => ({ style: {} }),
                body: { appendChild() {}, removeChild() {} }, execCommand: () => true };
  try {
    new Function('document', 'navigator', 'window', 'localStorage', src)(
      doc, { userAgent: 'node' }, { scrollTo() {} }, { getItem: () => null, setItem() {} });
  } catch (e) { out.error = (e && e.name) + ': ' + (e && e.message); }
  out.html_len = captured ? captured.length : 0;
  out.raw_img_onerror = !!(captured && /<img[^>]*onerror/i.test(captured));
  out.object_object = !!(captured && captured.indexOf('[object Object]') !== -1);
  out.nan = !!(captured && /NaN/.test(captured));
  return out;
}
for (const f of process.argv.slice(2)) console.log(JSON.stringify(run(f)));
"""


def _render_pages(tmp_path, paths):
    """在 node 里真跑这些页面的内嵌 JS，返回逐页结果（node 缺失即报错，不 skip）。"""
    harness = tmp_path / "k80_render_harness.js"
    harness.write_text(_RENDER_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness)] + [str(p) for p in paths],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"node 渲染实测失败：\n{r.stdout}\n{r.stderr}"
    out = []
    for line in r.stdout.strip().splitlines():
        if line.startswith("{"):
            out.append(json.loads(line))
    assert len(out) == len(paths), f"node 没跑完全部页面：{len(out)}/{len(paths)}"
    return out


#: 终验列出的形态 + k79 的畸形语料（**只增不减**）
RENDER_CASES = [
    ("charts.wuxing_radar=[1,'x',null]",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"wuxing_radar": [1, "x", None]}}),
    ("wuxing_radar 元素缺 value",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"wuxing_radar": [{"axis": "金"}, {"axis": "木", "value": None}]}}),
    ("insights 含真实换行", {"profile": {"name": "x"}, "insights": ["第一行\n第二行", "二"]}),
    ("insights 含行分隔符 U+2028",
     {"profile": {"name": "x"}, "insights": ["a\u2028b\u2029c"]}),
    ("insights 含引号与反斜杠",
     {"profile": {"name": "x"}, "insights": ['a"b\\c', "d"]}),
    ("rec.action 是对象",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "recommendations": [{"time_window": "春季", "action": {"a": 1}, "reason": None}]}),
    ("rec 元素是标量",
     {"profile": {"name": "x"}, "insights": ["ok"], "recommendations": [1, "x", None]}),
    ("annual_trend 元素缺 score",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"annual_trend": [{"year": "2027"}, {"year": "2028", "score": 60}]}}),
    ("annual_trend 单点",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"annual_trend": [{"year": "2027", "score": 60}]}}),
    ("monthly_fortune 单点",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"monthly_fortune": [{"label": "正月", "score": 60}]}}),
    ("monthly_fortune 元素非对象",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "charts": {"monthly_fortune": [1, [], None, {"label": "正月", "score": 60}]}}),
    ("shensha 是字符串", {"profile": {"name": "x", "shensha": "天乙"}, "insights": ["ok"]}),
    ("profile.name=123", {"profile": {"name": 123}, "insights": ["ok"]}),
    ("profile=null", {"profile": None, "insights": ["ok"]}),
    ("profile 缺 + insights=[12345]", {"insights": [12345]}),
    ("全部容器缺", {"reading_id": "x"}),
    ("深嵌套字段", {"profile": {"name": "x"}, "insights": ["ok"],
                    "charts": {"wuxing_radar": {"a": 1}}}),
    ("注入形态：name 带 img onerror",
     {"profile": {"name": "<img src=x onerror=alert(1)>"}, "insights": ["ok"]}),
    ("注入形态：insight 带 img onerror",
     {"profile": {"name": "x"}, "insights": ["<img src=x onerror=alert(1)>"]}),
    ("注入形态：rec.action 带 html",
     {"profile": {"name": "x"}, "insights": ["ok"],
      "recommendations": [{"action": "<img src=x onerror=alert(1)>"}]}),
    ("对照：正常报告",
     {"profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯", "day_master": "甲木",
                  "gender": "男", "birth_info": ""},
      "bazi_analysis": {"geju": "正官格", "yongshen": "水（喜）", "shensha": ["天乙"]},
      "charts": {"wuxing_radar": [{"axis": "金", "value": 2}, {"axis": "木", "value": 3}],
                 "monthly_fortune": [{"label": "正月", "score": 60}, {"label": "二月", "score": 70}],
                 "annual_trend": [{"year": "2027", "score": 60}, {"year": "2028", "score": 70}]},
      "insights": ["洞察一"], "recommendations": [{"time_window": "春季", "action": "行动",
                                                  "reason": "理由"}]}),
]


class TestEmbeddedJsNeverWhiteScreens:
    def test_no_white_screen_on_any_shape(self, tmp_path, monkeypatch):
        """**判据**：判为"可展示"的形态，页面内嵌 JS 真跑时必须 `html_len > 0`
        且不抛异常（`200 + 白屏` 是不许出现的形态）。

        为什么走"元素层兜底"而不是"改判据让这些形态 404"：判据
        （`report_shape_problem`）的边界由 k79 的
        `test_judge_rejects_only_structural_impossibility` 钉死 —— **值层面**异常
        必须判可展示（降级显示）。改判据等于放宽/推翻那条既有断言（红线禁止），
        所以只能把元素层也兜全。

        对照（改前，同一 harness 实测）：`charts.wuxing_radar=[1,"x",null]` →
        `TypeError: Cannot read properties of null (reading 'value')`、`html_len=0`；
        `insights` 含换行 → `SyntaxError: Invalid or unexpected token`、`html_len=0`。
        """
        client, h = _client(tmp_path, monkeypatch)
        paths, labels = [], []
        for i, (label, payload) in enumerate(RENDER_CASES):
            rid = "c8%06x" % (0x300 + i)
            _write(tmp_path, rid, payload)
            r = client.get(f"/share/{rid}")
            # 判据（二选一，都合法）：**要么 404（判为不可展示）**，
            # **要么 200 且渲染出东西**。"200 + 白屏"不许出现。
            assert r.status_code in (200, 404), f"{label}: {r.status_code}（只许 200/404）"
            if r.status_code == 404:
                continue
            p = tmp_path / f"{rid}.html"
            p.write_text(r.text, encoding="utf-8")
            paths.append(p)
            labels.append(label)
        assert paths, "没有任何形态被判为可展示（测试前提不成立）"
        results = _render_pages(tmp_path, paths)
        bad = []
        for label, res in zip(labels, results):
            if res["error"] or res["html_len"] <= 0:
                bad.append(f"{label}: error={res['error']} html_len={res['html_len']}")
        assert bad == [], ("内嵌 JS 在这些形态上白屏（200 + 白屏是不许出现的；"
                           "要么别白屏、要么判不可展示）：\n  - " + "\n  - ".join(bad))

    def test_no_repr_leaks_and_no_nan_svg(self, tmp_path, monkeypatch):
        """M4：不把 repr 当文案（`[object Object]`）、不在 SVG 里写 `NaN`。"""
        client, h = _client(tmp_path, monkeypatch)
        paths, labels = [], []
        for i, (label, payload) in enumerate(RENDER_CASES):
            rid = "d8%06x" % (0x400 + i)
            _write(tmp_path, rid, payload)
            r = client.get(f"/share/{rid}")
            p = tmp_path / f"{rid}.html"
            p.write_text(r.text, encoding="utf-8")
            paths.append(p)
            labels.append(label)
        results = _render_pages(tmp_path, paths)
        bad = []
        for label, res in zip(labels, results):
            if res["object_object"]:
                bad.append(f"{label}: 页面出现 [object Object]（把 repr 当文案了）")
            if res["nan"]:
                bad.append(f"{label}: 页面出现 NaN（SVG/文案里不该有）")
        assert bad == [], "\n  - ".join([""] + bad)

    def test_injection_shapes_are_escaped_at_the_dom_sink(self, tmp_path, monkeypatch):
        """第 4 层（渲染汇点）：载荷即使落到运行时的字符串上，也必须是**已转义**的。"""
        client, h = _client(tmp_path, monkeypatch)
        paths, labels = [], []
        for i, (label, payload) in enumerate(RENDER_CASES):
            if "注入形态" not in label:
                continue
            rid = "e8%06x" % (0x500 + i)
            _write(tmp_path, rid, payload)
            r = client.get(f"/share/{rid}")
            p = tmp_path / f"{rid}.html"
            p.write_text(r.text, encoding="utf-8")
            paths.append(p)
            labels.append(label)
        assert paths, "注入形态语料为空（测试前提不成立）"
        results = _render_pages(tmp_path, paths)
        bad = [f"{label}: 未转义的 img/onerror 进了 innerHTML"
               for label, res in zip(labels, results) if res["raw_img_onerror"]]
        assert bad == [], "\n  - ".join([""] + bad)

    def test_script_tag_count_is_one_for_every_shape(self, tmp_path, monkeypatch):
        """所有畸形形态下，公开页的脚本标签计数都不许因为载荷而变多。"""
        from src.api.visual_report import json_for_script
        for label, payload in XSS_PAYLOADS:
            text = json_for_script({"profile": {"gender": payload}, "insights": [payload]})
            assert "<script" not in text, label


# ══════════════════════════════════════════════════════════════════════
# T3 必修3（Important）：「读报告 JSON」的消费点门禁 —— 扩大面 + 形状判据
# ══════════════════════════════════════════════════════════════════════

#: 「读报告 JSON」的迹象符号（与 k79 的清单同源，**只增不减**）。
CONSUMER_NEEDLES = {
    "load_report", "load_report_from", "report_path_in", "_card_from_report",
    "_redact_report_for_share", "_report_share_expires_at", "report_shape_problem",
    "_build_report_html", "first_insight_text", "as_mapping", "json_safe",
    # k80 新增的编码/归属符号（新消费点若走它们，同样要被看见）
    "json_for_script", "js_string_literal", "html_attr_text",
    "report_owner_tag", "assert_report_owner", "_REPORT_JSON_CONSUMERS",
    # ── k81-M1（终验实测逃逸 K/L）：**已登记模块自己的 helper** ──────────────
    # 终验实测 `from src.api.share import _load_report` / `s._load_report(rid)`
    # → **零命中**：新消费者不必自己拼路径，只要"借用已登记模块里真读 JSON 的
    # helper"就整类不可见（对照：`_card_from_report` 在表里 → 红，证明判据本身
    # 是工作的，漏的是**表格**）。
    # 判据：凡是**真的把报告 JSON 读进来**的入口，无论它在哪个模块、是不是
    # 私有名，都必须在表里。当前全仓共 3 个（`visual_report.load_report_from`
    # 是唯一实现，`share._load_report` / `visual_report.load_report` 是它的两个
    # 薄包装；`report_path_in` 是唯一"路径怎么拼"实现，`share._report_path` 是
    # 它的薄包装）；`purge_report_files` 直接读 `owner_enc` 决定删哪些文件，
    # 同样在消费点清单里。
    "_load_report", "_report_path", "purge_report_files", "without_report_owner",
}

#: **形状判据**（k80-必修3 的关键）：不再只看"符号名对不对"，还看"像不像在拼
#: 报告路径 / 动态取报告函数"。这四类正是终验实测的逃逸手法。
_PATH_PARTS = ("reports", "data" + "/reports", "report")
_FUNCISH = ("report", "reading", "insight", "share")


def _fold_const(node, consts=None):
    """静态可折叠的字符串（字面量 / 加法拼接 / f-string 的字面段 / **常量名**）。

    k81-M1：新增 `consts`（局部/模块级常量传播表）—— 只传了它才解析 `Name`。
    不传 = 老行为（纯字面量折叠），既有调用点语义不变。

    为什么必须做（终验实测逃逸 B）：`DIR = "data/reports"` 先赋局部变量、再
    `pathlib.Path(DIR) / (rid + ".json")` —— **纯静态字面量 + 一层局部间接**，
    旧扫描器不做常量传播 ⇒ 零命中，与它自己声称的"静态可折叠的路径串在面内"
    直接冲突。
    """
    if consts:
        if isinstance(node, ast.Name) and node.id in consts:
            return consts[node.id]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue) and v.conversion in (-1, None) \
                    and v.format_spec is None:
                # 只折叠"没有格式转换/格式说明"的 F 值，且值本身要能静态折叠
                folded = _fold_const(v.value, consts)
                if folded is not None:
                    parts.append(folded)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_const(node.left, consts)
        right = _fold_const(node.right, consts)
        if left is not None and right is not None:
            return left + right
    return None


def _assign_pairs(tree):
    """模块/函数里 `名 = 表达式` 的（目标名，右值）对（只取简单名目标）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                yield node.target.id, node.value


def _collect_const_strings(tree):
    """**常量候选表**：`名 → {静态可折叠的字符串, …}`（k81-M1）。

    同一名字被赋过多个不同值（不同作用域/分支各赋各的）⇒ **全都留在候选集里**
    —— 判据用"任一候选带 `reports` 就算碰了报告目录"，于是"赋两次以规避传播"
    这条捷径不通（只有候选**全都不带**才算干净）。链式折叠（`a="x"` → `b=a+"y"`）
    只用**无歧义**的名字参与（见 `_unambiguous`），最多 4 轮 fixpoint。

    **只做"赋值链"这一层**。不做的：跨函数/跨模块传播（参数、返回值、import
    进来的常量）、容器元素（`cfg["dir"]`）、`getattr` 结果、运行期才产生的值
    —— 这些仍写在 `visual_report._REPORT_JSON_CONSUMERS` 的"仍在面外"里。
    """
    cands = {}
    for _ in range(4):
        changed = False
        for name, value in _assign_pairs(tree):
            folded = _fold_const(value, _unambiguous(cands))
            if folded is None:
                continue
            bucket = cands.setdefault(name, set())
            if folded not in bucket:
                bucket.add(folded)
                changed = True
        if not changed:
            break
    return cands


def _unambiguous(cands):
    """候选表里"只有一个可能值"的那些（`_fold_const` 用的精确折叠表）。"""
    return {k: next(iter(v)) for k, v in cands.items() if len(v) == 1}


def _folded_in_subtree(node, consts, cands=None):
    """子树里所有**静态可折叠**的子表达式折出来的串（含节点自身）。

    用于"这段代码像不像在拼报告路径"：只要**式子里的任何一层**折出来带
    `reports`，就算碰了报告目录（终验逃逸 B 正是"外层折不出、内层 `DIR` 折得出"）。
    `cands` 给的是**逐名候选集**（含"同名多值"的每一个值），用于堵"赋两次"。
    """
    out = []
    for sub in ast.walk(node):
        folded = _fold_const(sub, consts)
        if folded:
            out.append(folded)
        if cands and isinstance(sub, ast.Name):
            out.extend(cands.get(sub.id, ()))
    return out


def _subtree_text(node) -> str:
    """子树里出现的字符串常量与标识符名（用于"这段代码像不像在碰报告"）。"""
    parts = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.append(sub.value)
        elif isinstance(sub, ast.Name):
            parts.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            parts.append(sub.attr)
    return " ".join(parts)


def scan_source(src: str, rel: str):
    """扫一段源码里"读报告 JSON"的迹象；返回 `[(kind, hit, lineno)]`。

    四类判据（k81-M1 起，②③④ 的折叠都带**常量传播**，见 `_collect_const_strings`）：
      ① **符号引用**：`Name`/`Attribute`/`import` 别名命中 `CONSUMER_NEEDLES`；
      ② **路径拼接**：`/` 或 `os.path.join` 的参数里出现 `"reports"`；表达式里
         出现含 `reports` 的字符串常量（如 `"data/reports/" + rid + ".json"`）；
      ③ **动态取函数名**：`getattr(x, "load_" + "report")` 这类**可静态折叠**成
         报告符号的名字，或**折叠不出来但字面里带** report/reading/insight/share
         的表达式（`getattr(m, prefix + "report")`）；
      ④ **动态导入**：`importlib.import_module(...)` / `__import__(...)` 同上。
    """
    hits = []
    tree = ast.parse(src)
    _cands = _collect_const_strings(tree)
    consts = _unambiguous(_cands)
    for node in ast.walk(tree):
        # ① 符号名（含 import 别名：只 import 不调用的文件同样是消费点）
        if isinstance(node, ast.Name) and node.id in CONSUMER_NEEDLES:
            hits.append(("symbol", node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in CONSUMER_NEEDLES:
            hits.append(("symbol", node.attr, node.lineno))
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in CONSUMER_NEEDLES:
            hits.append(("symbol", node.name, node.lineno))
        # ② 路径拼接
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            # k81-M1：两侧**逐层折叠**（含常量传播）—— 终验逃逸 B
            # （`pathlib.Path(DIR) / (rid + ".json")`，DIR 是局部常量）在此变红。
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == "reports":
                    hits.append(("path", '/ "reports"', node.lineno))
                elif any("reports" in f for f in _folded_in_subtree(side, consts, _cands)):
                    hits.append(("path", "/ <folded reports>",
                                 getattr(side, "lineno", node.lineno)))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            args = list(node.args)
            if name == "join":                       # os.path.join(...)
                folded = [_fold_const(a, consts) for a in args]
                if any(f and "reports" in f for f in folded):
                    hits.append(("path", "os.path.join(...reports...)", node.lineno))
                elif any("reports" in f for a in args
                         for f in _folded_in_subtree(a, consts, _cands)):
                    hits.append(("path", "os.path.join(<folded reports>)", node.lineno))
            # ③ 动态取函数名
            if name == "getattr" and len(args) >= 2:
                target = _fold_const(args[1], consts)
                if target is not None and any(w in target for w in _FUNCISH):
                    hits.append(("getattr-name", target, node.lineno))
                elif target is None and any(
                        w in _subtree_text(args[1]) for w in _FUNCISH):
                    hits.append(("getattr-dynamic", _subtree_text(args[1])[:60], node.lineno))
            # ④ 动态导入
            if name in ("import_module", "__import__") and args:
                target = _fold_const(args[0], consts)
                if target is not None and any(w in target for w in _FUNCISH):
                    hits.append(("import-name", target, node.lineno))
                elif target is None and any(
                        w in _subtree_text(node) for w in _FUNCISH):
                    hits.append(("import-dynamic", _subtree_text(node)[:60], node.lineno))
        # ② 附带：表达式里含 `data/reports` 的字符串常量（含不可折叠的拼接）。
        #    只认**报告目录那一串**（`data/reports`），不认任何含 "reports" 的字样 ——
        #    否则散文（`"…reports/element_freq_top.csv"`）、HTTP 路由串
        #    （`"/api/reports/"`）、"weekly reports" 这类都会假红。
        if isinstance(node, (ast.BinOp, ast.JoinedStr, ast.Call)):
            folded = _fold_const(node, consts)
            if folded and "data/reports" in folded:
                hits.append(("path-folded", folded[:60], node.lineno))
            else:
                # k81-M1：这里从"只找字面量常量"扩成"找**任何可折叠的子表达式**
                # （含常量名）" —— 路径常量被搬进局部变量后，字面量已不在这个
                # 节点里（逃逸 B 的第二种写法：`p = DIR + "/" + rid + ".json"`）。
                part = next((f for f in _folded_in_subtree(node, consts, _cands)
                             if "data/reports" in f), None)
                if part is not None:
                    hits.append(("path-part", part[:60], node.lineno))
    # 去重（同一处可能同时命中多条）
    return sorted(set(hits), key=lambda x: (x[2], x[0]))


#: 允许读报告 JSON 的模块（唯一事实；新增必须同时更新
#: `visual_report._REPORT_JSON_CONSUMERS` 与本表，并证明新点自己也不抛）
ALLOWED_FILES = {"src/api/visual_report.py", "src/api/share.py", "src/storage/dao.py"}

#: 扫描面（k80：**扩到 scripts/ 与仓根**，src/ 递归含 src/plugins/）。
def _scan_targets():
    files = sorted((REPO / "src").rglob("*.py"))
    files += sorted((REPO / "scripts").rglob("*.py"))
    files += sorted(REPO.glob("*.py"))
    out = []
    for p in files:
        rel = str(p.relative_to(REPO))
        if rel.startswith("tests/") or rel.startswith("data/"):
            continue
        out.append(rel)
    return out


#: 允许的**非消费者**命中（显式登记 + 理由；卫生测试保证不会堆积）。
#: 键 = `(路径或目录前缀, 形状)`；前缀以 `/` 结尾，**只覆盖该子树**。
ALLOWED_HITS = {
    ("scripts/weekly_report.py", "path"):
        "写自己的周报 .txt 到 settings.data_dir（/mnt/d/fortune-data）下的 reports/，"
        "与 data/reports 不是同一个目录、也不读 reading_id 报告 JSON —— 只碰巧同名",
    ("scripts/k55_dream/", "path"):
        "k55 梦境检索语料的 reports/ 目录（`DATA_ROOT = "
        "/mnt/d/fortune-data/books/k55_dream`，见 crawl_lib.py:45）—— "
        "与 `data/reports`（reading_id 报告）无关，且只读写自己那套 stats/json",
}


def _allowed(rel: str, kind: str):
    """命中是否被例外表覆盖（精确路径 或 目录前缀）。"""
    if (rel, kind) in ALLOWED_HITS:
        return True
    return any(key[0].endswith("/") and rel.startswith(key[0]) and key[1] == kind
               for key in ALLOWED_HITS)


class TestConsumerInventoryGateIsNotBypassable:
    def test_repo_scan_has_no_unregistered_consumer(self):
        """全仓扫描（src/ + scripts/ + 仓根）：清单外的"读报告 JSON"迹象即红。"""
        found = {}
        for rel in _scan_targets():
            src = (REPO / rel).read_text(encoding="utf-8")
            try:
                hits = scan_source(src, rel)
            except SyntaxError as e:      # 解析不了的文件本身要人看一眼
                found.setdefault(rel, []).append(("parse-error", str(e), 0))
                continue
            if rel in ALLOWED_FILES:
                continue
            for kind, hit, line in hits:
                if _allowed(rel, kind):
                    continue
                found.setdefault(rel, []).append((kind, hit, line))
        assert found == {}, (
            "发现清单外的模块在碰报告 JSON（请先写进 "
            "`visual_report._REPORT_JSON_CONSUMERS` 与 ALLOWED_FILES，"
            "或按 ALLOWED_HITS 的格式登记理由）：\n  "
            + json.dumps(found, ensure_ascii=False, indent=1))

    def test_allowlist_entries_are_still_needed(self):
        """例外卫生：登记过的例外必须仍然被需要（防豁免堆积成新的面外）。"""
        stale = []
        for (rel, kind), why in ALLOWED_HITS.items():
            assert why.strip(), f"{rel}/{kind} 的例外没写理由"
            if rel.endswith("/"):          # 目录前缀：子树里还有命中就算仍被需要
                alive = False
                for target in _scan_targets():
                    if not target.startswith(rel):
                        continue
                    src = (REPO / target).read_text(encoding="utf-8")
                    if kind in {k for k, _h, _l in scan_source(src, target)}:
                        alive = True
                        break
            else:
                src = (REPO / rel).read_text(encoding="utf-8")
                alive = kind in {k for k, _h, _l in scan_source(src, rel)}
            if not alive:
                stale.append(f"{rel} [{kind}] 已不再命中 → 请删掉这条例外")
        assert stale == [], "\n  - ".join([""] + stale)

    def test_allowlist_is_bounded_and_enumerable(self):
        """例外表必须**有界、可枚举**（条目数与内容都能被人一眼审完）。"""
        assert len(ALLOWED_HITS) <= 8, (
            f"例外表膨胀到 {len(ALLOWED_HITS)} 条 —— 例外表本身就是新的面外，"
            "要么收敛判据、要么把条目补上理由并复核")
        for (rel, kind), why in ALLOWED_HITS.items():
            path = REPO / (rel if not rel.endswith("/") else rel.rstrip("/"))
            assert path.exists(), f"例外指向的路径不存在：{rel}"
            assert len(why) >= 20, f"{rel}/{kind} 的理由过短（要能自证清白）"

    def test_scan_surface_really_covers_scripts_and_root(self):
        """面不能悄悄缩小：scripts/ 与仓根必须在扫描面内。"""
        targets = _scan_targets()
        assert any(t.startswith("scripts/") for t in targets), "scripts/ 不在扫描面内"
        assert any("/" not in t for t in targets), "仓根 .py 不在扫描面内"
        assert any(t.startswith("src/") for t in targets)

    @pytest.mark.parametrize("label,src,expect_kind", [
        ("裸符号名（k79 拦得住的那种）",
         "from src.api.visual_report import load_report\nx = load_report('a')\n", "symbol"),
        ("只 import 不调用（k79 的纯符号判据漏掉）",
         "from src.api.visual_report import load_report\n", "symbol"),
        ("str 拼路径",
         "p = str(DATA) + '/data/reports/' + rid + '.json'\n", "path-part"),
        ("os.path.join 拼路径",
         "import os\np = os.path.join(root, 'data', 'reports', rid + '.json')\n", "path"),
        ("getattr 动态名（拼接折叠）",
         "import m\nf = getattr(m, 'load_' + 'report')\nf('a')\n", "getattr-name"),
        ("getattr 真动态名（字面带词）",
         "import m\nf = getattr(m, suffix + 'report')\n", "getattr-dynamic"),
        ("getattr 常量名",
         "import m\nf = getattr(m, 'load_report_from')\n", "getattr-name"),
        ("dynamic import",
         "import importlib\nm = importlib.import_module('src.api.' + 'visual_report')\n",
         "import-name"),
        ("/ \"reports\" 路径常量",
         "from pathlib import Path\np = Path(DATA) / 'reports' / (rid + '.json')\n", "path"),
        ("f-string 拼路径",
         'p = f"{DATA}/data/reports/{rid}.json"\n', "path-folded"),
        # ── k81-M1：终验实测的**两个绿案例**，改后必须红（至少 B 必红）────────
        ("k81-B 静态字面量 + 一层局部间接（终验绿案例）",
         "import pathlib\n"
         "def f(rid):\n"
         "    DIR = 'data/reports'\n"
         "    return pathlib.Path(DIR) / (rid + '.json')\n", "path"),
        ("k81-B' 路径常量搬进局部名后用加法拼",
         "DIR = 'data/reports'\n"
         "def f(rid):\n"
         "    return DIR + '/' + rid + '.json'\n", "path-part"),
        ("k81-B'' os.path.join 的目录实参是局部常量",
         "import os\n"
         "DIR = 'data/reports'\n"
         "p = os.path.join(root, DIR, rid + '.json')\n", "path"),
        ("k81-B''' f-string 里插局部常量",
         "DIR = 'data/reports'\n"
         "def f(rid):\n"
         "    return f'{DIR}/{rid}.json'\n", "path-folded"),
        ("k81-K 借用已登记模块自己的 helper（from-import，终验绿案例）",
         "from src.api.share import _load_report\nr = _load_report(rid)\n", "symbol"),
        ("k81-L 借用已登记模块自己的 helper（属性访问，终验绿案例）",
         "from src.api import share as s\nr = s._load_report(rid)\n", "symbol"),
        ("k81-K' 另外两个真读 JSON 的 helper 同样在表里",
         "from src.api.share import _report_path\n"
         "from src.storage.dao import purge_report_files\n", "symbol"),
    ])
    def test_escape_techniques_are_all_caught(self, label, src, expect_kind):
        """终验的逃逸手法逐条注入：改前 6/7 静默通过，改后都必须在面内。

        k81 追加：终验点名的两个**仍在绿**的逃逸（B 局部常量传播、K/L 借用
        helper）也钉在这里 —— 它们改前是绿的（有实测记录），现在必须红。
        """
        hits = scan_source(src, "src/plugins/new_reader.py")
        kinds = {k for k, _h, _l in hits}
        assert expect_kind in kinds, (
            f"{label} 没被扫出来（期望 {expect_kind}，实得 {sorted(kinds)}）")

    def test_local_constant_propagation_does_not_invent_false_positives(self):
        """常量传播**不许把正常代码判红**（否则门禁会被"绕过式豁免"反噬）。

        判据：不带报告目录常量的普通拼接/路径代码 → 零命中。
        """
        for label, src in [
            ("普通字符串拼接",
             "def f(a, b):\n    sep = '-' + ':'\n    return a + sep + b\n"),
            ("普通 pathlib 拼接",
             "from pathlib import Path\nROOT = '/mnt/d/fortune-data'\n"
             "p = Path(ROOT) / 'statics' / 'app.js'\n"),
            ("只提到 report 三个字的路由串",
             "ROUTES = ('/api/report/{id}', '/report/{id}')\n"),
            ("常量名与报告目录无关",
             "BASE = '/mnt/d/fortune-data'\nd = BASE + '/json'\n"),
        ]:
            hits = scan_source(src, "src/plugins/innocent.py")
            assert hits == [], f"{label}: 假红 {hits}"

    def test_assign_twice_is_not_an_escape(self):
        """**赋两次不是逃逸手法**：同名多值时，**任一候选**带 `data/reports` 即红。

        改前这里会被"先把常量赋成两个不同值"绕过（或干脆不做传播 ⇒ 一直绿）。
        现在的判据是"候选集里只要有带报告目录的就算碰"（`_collect_const_strings`
        保留**每一个**候选值，`_folded_in_subtree` 逐个对照）。
        """
        src = ("def a():\n    D = 'data/reports'\n    return D\n"
               "def b():\n    D = 'other/place'\n    return open(D)\n")
        hits = scan_source(src, "src/plugins/evasive.py")
        assert any("data/reports" in h for _k, h, _l in hits), \
            f"同名多值把常量传播绕过去了：{hits}"

    def test_a_new_reader_in_scripts_or_root_is_red(self, tmp_path):
        """面扩到位：同一个消费者放到 scripts/ 或仓根也必须红。"""
        src = "from src.api.visual_report import load_report\n"
        for rel in ("scripts/new_consumer.py", "new_consumer.py", "src/plugins/new_consumer.py"):
            hits = scan_source(src, rel)
            assert hits, f"{rel} 里的消费者没被扫出来（面没覆盖到）"
            assert rel not in ALLOWED_FILES

    def test_escape_outside_the_surface_is_listed_honestly(self):
        """**仍在面外**的形态必须如实写在 docstring 里（不许说过头）。

        这一段是"边界"的单一事实源：只要有人删掉边界声明，本条即红。
        """
        doc = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        head = doc[:doc.index("_REPORT_JSON_CONSUMERS = (")]
        for must in ("仍在面外", "运行期", "跨函数", "拦得住"):
            assert must in head, f"`_REPORT_JSON_CONSUMERS` 的边界声明缺了「{must}」"


# ══════════════════════════════════════════════════════════════════════
# T4 M1/M2：禁语宽面的例外机制与"消灭手写数字"（守卫侧实现在 JS）
# ══════════════════════════════════════════════════════════════════════

class TestBannedWordGuardHasBoundedExemptions:
    """M1/M2 的实现在 `miniprogram/tests/k71_privacy_consistency.test.js`（JS 守卫）。

    本类钉住"接线与边界"（与 k79-T3 对 account_copy 的做法同款）：机制真的存在、
    真的接在两张禁语表上、例外表可枚举、卫生测试在、手写数字被消灭。
    """

    GUARD = REPO / "miniprogram" / "tests" / "k71_privacy_consistency.test.js"

    def _guard(self):
        return self.GUARD.read_text(encoding="utf-8")

    def test_exemption_table_exists_and_is_auditable(self):
        src = self._guard()
        assert "CONTEXT_EXEMPT" in src, "M1 的显式例外表不见了"
        # 三键（文件/规则/整条文案）+ 理由：有界性的载体
        for key in ("file", "ruleRe", "unit", "why"):
            assert key in src, f"例外表条目缺少 {key} 字段（有界性不成立）"
        assert "CONTEXT_EXEMPT_MAX" in src or "length" in src, "例外表没有条目数上界"

    def test_hygiene_and_no_weakening_tests_exist(self):
        src = self._guard()
        assert "例外" in src and "已不再需要" in src, "缺少「例外必须仍然被需要」的卫生测试"
        assert "削弱" in src or "真违规" in src, "缺少「同规则真违规仍红」的反向证明"

    def test_stale_numbers_are_gone(self):
        src = self._guard()
        for stale in ("33469", "34306", "33580"):
            assert stale not in src, f"手写的宽面条数 {stale} 还在（M2：必须消灭手写数字）"
        assert "运行时实测" in src, "宽面条数没有改成「运行时实测」的表述"

    def test_thresholds_not_loosened(self):
        src = self._guard()
        for keep in ("BACKEND.string_constants >= 1000",
                     "BACKEND.folded >= 100",
                     "BACKEND.user_facing.length >= 250"):
            assert keep in src, f"既有阈值被改动/删除：{keep}"


# ══════════════════════════════════════════════════════════════════════
# T5 M3：「路径唯一实现」名副其实
# ══════════════════════════════════════════════════════════════════════

class TestOnePathImplementationForReal:
    def test_json_filename_literal_appears_once(self):
        src = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        needle = 'f"{reading_id}.json"'
        assert src.count(needle) == 1, (
            f"{needle!r} 在 visual_report.py 里出现 {src.count(needle)} 次"
            "（应只在 report_path_in 一处 —— M3：写盘那处内联拼接必须收敛）")

    def test_write_path_uses_report_path_in(self):
        src = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_report_data":
                body = ast.unparse(node)
                assert "report_path_in(" in body, \
                    "写盘没有走 report_path_in（读写路径又分岔了）"
                assert '_DATA_DIR / f"' not in body, "写盘里还有内联拼接"
                return
        raise AssertionError("找不到 generate_report_data")

    def test_path_in_is_the_only_builder(self, tmp_path):
        from src.api.visual_report import report_path_in
        assert report_path_in(tmp_path, "aa000009").name == "aa000009.json"
        assert report_path_in(tmp_path, "aa000009").parent == tmp_path


# ══════════════════════════════════════════════════════════════════════
# T6 M5：/api/share/{id} 的分派判据（全数字 reading_id）
# ══════════════════════════════════════════════════════════════════════

class TestShareDispatchByExistenceNotShape:
    """终验 M5：`reading_id = uuid4().hex[:8]` 全数字的概率 `(10/16)^8 ≈ 2.33%`，
    这些分享链接被 `isdigit()` 误判成咨询 ID → 503/404（真缺陷）。"""

    def test_digit_only_reading_id_resolves_to_the_report(self, tmp_path, monkeypatch):
        client, h = _client(tmp_path, monkeypatch)      # _dao = None（改前必 503）
        rid = "12345678"                                # 8 位、全数字、合法 hex
        _write(tmp_path, rid, {"profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯"},
                               "insights": ["洞察一"]})
        r = client.get(f"/api/share/{rid}", headers=h)
        assert r.status_code == 200, (
            f"全数字 reading_id 的分享卡片 → {r.status_code}（改前被误当咨询 ID）")
        assert r.json()["reading_id"] == rid
        page = client.get(f"/share/{rid}")
        assert page.status_code == 200 and "命运报告" in page.text

    def test_non_digit_reading_id_still_works(self, tmp_path, monkeypatch):
        client, h = _client(tmp_path, monkeypatch)
        rid = "abcd1234"
        _write(tmp_path, rid, {"profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯"},
                               "insights": ["洞察一"]})
        assert client.get(f"/api/share/{rid}", headers=h).status_code == 200

    def test_numeric_consultation_id_still_goes_to_the_dao(self, tmp_path, monkeypatch):
        """正常路径不许改坏：数字 ID 仍走咨询分支（DAO 未装配 → 503 口径不变）。"""
        client, h = _client(tmp_path, monkeypatch)
        r = client.get("/api/share/987654", headers=h)
        assert r.status_code == 503, f"咨询分支的 503 口径变了：{r.status_code}"

        class _Dao:
            def __init__(self):
                self.calls = []

            def get_consultation(self, cid):
                self.calls.append(cid)
                return None

        dao = _Dao()
        client, h = _client(tmp_path, monkeypatch, dao=dao)
        r = client.get("/api/share/987654", headers=h)
        assert dao.calls == [987654], "数字 ID 没走到咨询分支"
        assert r.status_code == 404, r.status_code

    def test_unknown_shape_is_404(self, tmp_path, monkeypatch):
        client, h = _client(tmp_path, monkeypatch)
        for bad in ("zzz", "a" * 300, "notahexid"):
            assert client.get(f"/api/share/{bad}", headers=h).status_code == 404, bad


# ══════════════════════════════════════════════════════════════════════
# T7 交叉印证：k79 的既有判据一条都没被放松
# ══════════════════════════════════════════════════════════════════════

class TestNoExistingAssertionWasLoosened:
    def test_consumers_inventory_still_closed_by_k79_gate(self):
        k79 = (REPO / "tests" / "test_k79_privacy_security_final2.py").read_text(encoding="utf-8")
        assert "needles = {" in k79 and "test_no_new_reader_outside_the_inventory" in k79, \
            "k79 的消费点门禁被删/改名了"

    def test_judge_boundary_still_structural_only(self):
        from src.api.visual_report import report_shape_problem
        # 值层面异常 → 判可展示（k79 的既有断言；本批不许改判据来"绕过"白屏）
        assert report_shape_problem({"profile": {"name": 123}, "insights": ["ok"]}) == ""
        assert report_shape_problem({"charts": {"wuxing_radar": [1, "x", None]},
                                     "profile": {"name": "x"}, "insights": ["ok"]}) == ""
        # 结构不可能 → 判不可展示
        assert report_shape_problem({"profile": [1, 2]})
        assert report_shape_problem({"insights": "notalist"})

    def test_share_page_ttl_and_redaction_still_intact(self, tmp_path, monkeypatch):
        """既有能力不许因为本批改动退化：分享页仍就地渲染、仍剥离个人信息。"""
        client, h = _client(tmp_path, monkeypatch)
        data = _generate(client, h, name="张三", gender="男")
        rid = data["reading_id"]
        page = client.get(f"/share/{rid}")
        assert page.status_code == 200
        assert "张三" not in page.text, "公开页出现了姓名（k76 的剥离被破坏）"
        assert "1976年" not in page.text and "1990年5月20日" not in page.text
        assert "四柱" in page.text or "REPORT" in page.text
