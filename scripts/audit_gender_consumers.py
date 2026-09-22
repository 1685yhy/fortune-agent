#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k82 必修2：**性别消费点的可复跑盘点**（替代"人记清单"）。

## 为什么有这个脚本（根因）
k81 在 `birth_contract.GENDER_ALIASES` 的注释里手写了一份"残留的表"清单（4 条）。
k81 终验证明它**不全**：`bot/handler.py::_gen_instant_reply` 的
`in ("男","女","male","female")` 也是一处内联别名消费点，却没进清单；
k82 用本脚本重盘又发现**另外 3 处**（`_GENDER_CN`、`ming._GENDER_TAG`、
`person_dao` 的非字符串边界）。根因是**判据靠人记**——
人记清单必然漏，且漏了没有任何机制报警。

## 判据（本脚本＝唯一可复跑判据）
对 `src/**/*.py` 做 **AST** 扫描（不是 grep 字面量——见下"为什么必须 AST"），
把"性别字面量集合"分成 4 类消费点：

  | kind        | 形状                                                | 含义                     |
  |-------------|-----------------------------------------------------|--------------------------|
  | `table-in`  | dict 字面量，**≥2 个 key 是性别词**，值也是性别词       | **输入↔规范值 的别名表**  |
  | `table-enc` | dict 字面量，**≥2 个 key 是性别词**，值是别的编码       | 输出侧表（不是入口）      |
  | `membership`| `x in (…性别字面量…)` / `not in`                      | 集合判定                 |
  | `equality`  | `x == "男"` / `!= "女"` 等                            | 等值判定                 |

对 `miniprogram/**/*.js` 用**正则**（本仓无 JS 解析器，判据更弱，**如实标注**）：
只扫**生产文件**（`tests/`、`*.test.js` 不计——它们钉既有行为，不是消费点）、
跳过注释行、且该行要含判定算子（`===`/`indexOf`/`includes`/`:` 等）。它是**上界**
（可能多报），但在"新增消费点必然要写一个新字面量"这一点上足以报警。

### 为什么必须 AST（而不是 grep）
`grep "男"` 会把**科技术语**全捞进来：`engines/zeri.py` 的二十八宿里有宿名
「女」，`engines/ming.py` 的取名用字库里也有单字「男」「女」，
`engines/dream_rules.py` 的梦象词表里有「男孩/女孩」，`dream_sanitize.py` 的
中性化映射里有「先生」。本脚本按**语法形状**取：

  · **dict 表**：**≥2 个 key 是性别词**才算表。宿名表 `{"角":"吉", … "女":"凶"}`
    只有 1 个性别 key ⇒ 不计；`{"name": '男孩', …}` 的性别词在**值**侧且 key 不是
    性别词 ⇒ 不计（k82 曾用"值侧也算"的宽判据，正是它把梦象词表全捞进来了）。
  · **比较**：`男`/`女` 在 `in`/`==` 里**算数**（比较是代码不是数据）；
    但 `m`/`f`/`1`/`0` 一律不算（`gender == "m"` 本仓不存在；
    `_date_groups` 的 `'m'`＝月、`1`/`0`＝计数），**纯 `unknown` 判定也不算**
    （`x == "unknown"` 只判"有没有值"，不做男/女区分 ⇒ 不决定排盘方向）。

## 如实边界（本判据**做不到**的，明写出来，别当成"全覆盖"）
  1. **JS 无 AST**：只到"文件 × 词集"粒度，且是**上界**（可能多报）；
  2. **粒度是"站点"不是"行"**：同一函数里**再复制一条完全相同**（同 kind、同词集）
     的判定不会报警 —— 该情形不引入新词集/新语义，且必在同一函数内、review 可见；
  3. **非字面量写法不在面内**：把性别词从配置/DB/环境变量读进来（如
     `GENDER_WORDS = os.environ[...]`）本判据看不见 —— 本仓现状是字面量，
     若将来改成配置化，**需要新判据**（这一条是判据的失效条件，不是"已覆盖"）；
  4. **`chinese` 之外的词表**（拼音/其它语言）不在 `STRONG`/`WEAK` 里。

## 用法
    python3 scripts/audit_gender_consumers.py            # 打印清单
    python3 scripts/audit_gender_consumers.py --json     # 机器可读
    python3 scripts/audit_gender_consumers.py --check    # 门禁：与 PINNED 清单
                                                         # 逐条比对，有增减即 exit 1

`--check` 的语义：**每一处**性别消费点都必须在 `SITES`（Python，逐站点）/
`JS_FILES`（JS，逐文件）里**显式表态**。
**新增一个性别消费点而不表态 ⇒ 红** —— 这就是 k81 漏掉 handler.py 那处的机制性修法。
站点 key **不带行号**（`文件::作用域::kind::词集`），所以改动无关代码不会误红；
`LEGEND` 里每个表态码都对应一类"为什么它不收敛/不合并"的理由。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent

#: 规范值（`birth_contract.GENDERS`）：出现在 dict 的**值**侧即"归一出规范值"。
CANONICAL = {"男", "女", "unknown"}

#: **强**性别词：多字中文 / 英文词 —— 单独出现即可判定为"在讲性别"。
STRONG = {
    "unknown", "未知", "性别未知", "male", "female", "man", "woman", "boy", "girl",
    "male1", "男性", "女性", "男士", "女士", "先生", "男命", "女命",
    "男生", "女生", "男子", "女子", "男孩", "女孩",
}
#: **弱**性别词：太容易是别的东西（宿名「女」/字库/`m`=month/`1`=计数），
#: 只在**表**（dict）里与其它性别词同时出现才计；**比较**里 `m`/`f`/`1`/`0`
#: 一律不计（`gender == "m"` 这种写法本仓不存在，出现即需要人来表态）。
WEAK = {"男", "女", "m", "f", "1", "0"}

#: 「不知道」类词：**单独出现不算消费点**（`x == "unknown"` 只判"有没有值"，
#: 不做男/女区分 ⇒ 不决定排盘方向）。与男/女同集合出现时才算。
UNKNOWN_ONLY = {"unknown", "未知", "性别未知"}


def _lower_consts(node) -> Optional[set]:
    """取字面量集合里的字符串常量（小写化）；不是集合字面量则返回 None。"""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {e.value.lower() for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value.lower()}
    return None


def _cmp_is_gender(tokens: set) -> bool:
    """比较点判据：集合里有**能区分男女**的词（男侧或女侧）才计。

    纯 `{"unknown","未知"}` 不计（见 `UNKNOWN_ONLY`）；`m`/`f`/`1`/`0` 不计。
    """
    decisive = (tokens & (STRONG | {"男", "女"})) - UNKNOWN_ONLY
    return bool(decisive)


def _scope_of(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> str:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        if isinstance(cur, ast.ClassDef):
            return cur.name
    return "<module>"


def scan_python(path: Path, rel: str) -> List[dict]:
    """AST 扫描一个 .py：返回该文件的性别消费点。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    hits: List[dict] = []

    def add(node, kind, toks, extra=""):
        hits.append({"file": rel, "line": node.lineno, "kind": kind,
                     "scope": _scope_of(node, parents),
                     "tokens": sorted(toks), "note": extra})

    for node in ast.walk(tree):
        # ① dict 字面量：**≥2 个性别 key** ⇒ 一张"性别→某值"的表。
        #    单个性别 key 的 dict **不计** —— 那是数据表（如 `zeri.py` 的二十八宿
        #    `{"角":"吉", … "女":"凶"}`，「女」是宿名不是性别；`ming.py` 的取名
        #    字库同理）。
        if isinstance(node, ast.Dict):
            keys = {k.value.lower() for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            vals = {v.value.lower() for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)}
            gkeys = keys & (STRONG | WEAK)
            if len(gkeys) >= 2:
                # 值全是性别词/规范值 ⇒ 输入侧别名表；值是别的编码 ⇒ 输出侧表。
                enc = bool(vals) and not (vals & (STRONG | WEAK | CANONICAL))
                kind = "table-enc" if enc else "table-in"
                add(node, kind, gkeys,
                    "值是编码（非性别词）⇒ 输出侧表，不是入口" if enc
                    else "值全是性别词/规范值 ⇒ 输入→男/女/unknown 的别名表")
            continue
        # ② 比较：in / not in / == / !=（`男`/`女` 在此**算数**：比较是代码不是数据）
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    toks = _lower_consts(comp)
                    if toks and _cmp_is_gender(toks):
                        add(node, "membership", toks & (STRONG | WEAK | CANONICAL),
                            "集合判定")
                elif isinstance(op, (ast.Eq, ast.NotEq)):
                    for side in (node.left, comp):
                        toks = _lower_consts(side)
                        if toks and _cmp_is_gender(toks):
                            add(node, "equality", toks & (STRONG | WEAK | CANONICAL),
                                "等值判定")
    return hits


#: JS：被引号包起来的性别字面量（判据更弱：**上界**，可能多报）。
_JS_RE = re.compile(
    r"""['"](unknown|未知|性别未知|male1|male|female|man|woman|boy|girl|"""
    r"""男性|女性|男士|女士|先生|男命|女命|男生|女生|男子|女子|男孩|女孩|男|女)['"]""")
#: JS 里"这个字面量参与了判定"的标志（比较 / 集合查询 / 映射取值）。
_JS_OP_RE = re.compile(r"===|!==|==|!=|indexOf|includes|\bin\b|\[|\.get\(|:")
#: **测试文件不计**：它们钉的是既有行为，不是生产消费点。
_JS_EXCLUDE = re.compile(r"(^|/)(tests?|__tests__)/|\.test\.js$|\.spec\.js$")


def scan_js(path: Path, rel: str) -> List[dict]:
    if _JS_EXCLUDE.search(rel):
        return []
    hits: List[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue                       # 注释不算
        toks = {m.group(1).lower() for m in _JS_RE.finditer(line)}
        if (toks - UNKNOWN_ONLY) and _JS_OP_RE.search(line):
            hits.append({"file": rel, "line": i, "kind": "js-literal",
                         "scope": "<js>", "tokens": sorted(toks),
                         "note": "正则上界（无 JS AST）：仅生产文件、非注释、含判定算子"})
    return hits


def collect(root: Optional[Path] = None) -> List[dict]:
    """全仓盘点：`src/**/*.py`（AST）+ `miniprogram/**/*.js`（正则）。"""
    root = root or REPO
    hits: List[dict] = []
    for p in sorted(root.glob("src/**/*.py")):
        hits += scan_python(p, str(p.relative_to(root)))
    for p in sorted(root.glob("miniprogram/**/*.js")):
        hits += scan_js(p, str(p.relative_to(root)))
    return sorted(hits, key=lambda h: (h["file"], h["line"], h["kind"]))


def site_key(h: dict) -> str:
    """**与行号无关**的站点标识：`文件::作用域::kind::词集::×个数`。

    为什么不带行号：行号一动门禁就红 ⇒ 变成噪音 ⇒ 被绕过。这个 key 只在
    **真的多了一处消费点**（新文件/新函数/新词集/同处多一条）时变化。
    """
    return (f"{h['file']}::{h['scope']}::{h['kind']}::"
            f"{'|'.join(h['tokens'])}")


def collapsed(hits: List[dict]) -> Dict[str, dict]:
    """把逐行命中折叠成"站点 ⇒ {行号, 个数}"（同站点多条要计数，防漏报新增）。"""
    out: Dict[str, dict] = {}
    for h in hits:
        k = site_key(h)
        out.setdefault(k, {"lines": [], "count": 0, "tokens": h["tokens"],
                           "note": h["note"]})
        out[k]["lines"].append(h["line"])
        out[k]["count"] += 1
    return out


#: ── 已表态的清单（`--check` 的比对对象）──────────────────────────────────
#: key = `site_key(...)`；值 = **表态码**（下表 LEGEND）。规则：
#:   · **任何一处不在表里的性别消费点 ⇒ `--check` 红**（这就是"防漏"的机制）；
#:   · 表态码不是"通过"，是"已让人看过并给了理由"。新增点时**必须选一个码**
#:     或在 `#:` 注释里写清为什么它自成一类。
#: 本表由 `--check` 的输出生成后**逐条人工表态**（k82 报告里有这份清单）。
LEGEND = {
    "TABLE": "★别名表本体（唯一事实源）——收词改动只允许发生在这里",
    "TABLE-2": "第二张别名表（未收敛，理由见 birth_contract 注释 ⑤）",
    "INPUT": "入参边界：查唯一别名表 ⇒ 不收敛（收敛＝把策略搬来搬去）",
    "NLU": "吃 NLU/前端抽出的 男/女/unknown（已归一的输入）⇒ 不收敛",
    "DOWN": "下游计算：吃已归一值，只做男/女分支（决定顺逆排/神煞/卦）⇒ 不收敛",
    "DISPLAY": "展示/文案层（男命/坤造/男方-女方/称谓注入）⇒ 不收敛",
    "OUTENC": "输出侧编码（喂字库/取名：男→m、女→f）⇒ 不收敛",
    "DIFF": "★刻意与本表相反（问真口径：sex=1 → 男命歌诀，其余 → 女命歌诀）⇒ 不许对齐",
    "CONVERGED": "★k82 已收敛（原 `in (\"男\",\"女\",\"male\",\"female\")` → `in (\"男\",\"女\")`）",
}
SITES: Dict[str, str] = {
    # ── 别名表本体 / 第二张表 ────────────────────────────────────────────
    "src/api/birth_contract.py::<module>::table-in::0|1|boy|f|female|girl|m|male|male1|man|unknown|woman|先生|女|女命|女士|女子|女孩|女性|女生|性别未知|未知|男|男命|男士|男子|男孩|男性|男生": "TABLE",
    "src/bot/handler.py::<module>::table-in::female|male|女|男": "TABLE-2",
    "src/engines/advisor_v2.py::_build_prompt::table-in::female|male|女|男": "DISPLAY",
    "src/main.py::_derive_base_report_content::table-in::female|male": "DISPLAY",
    "src/engines/ming.py::<module>::table-in::女|男": "OUTENC",
    # ── 入参边界 / NLU ───────────────────────────────────────────────────
    "src/api/user.py::_person_birth::membership::女|男": "INPUT",
    "src/bot/handler.py::_extract_bazi_info::membership::女|男": "NLU",
    "src/bot/handler.py::_stash_pending_birth::membership::女|男": "NLU",
    "src/bot/handler.py::_is_gender_correction::membership::女|男": "NLU",
    "src/engines/advisor_v2.py::_build_prompt::membership::女|男": "DISPLAY",
    "src/engines/advisor_v2.py::_build_prompt::equality::男": "DISPLAY",
    "src/bot/handler.py::process::membership::女|男": "NLU",
    "src/bot/handler.py::_gen_instant_reply::membership::女|男": "CONVERGED",
    "src/bot/handler.py::_gen_instant_reply::equality::男": "CONVERGED",
    # ── 下游计算 ─────────────────────────────────────────────────────────
    "src/engines/bazi.py::calculate::equality::女": "DOWN",
    "src/engines/bazi.py::calculate::equality::男": "DOWN",
    "src/engines/bazi.py::_calc_qiyun_start_age::equality::男": "DOWN",
    "src/engines/bazi.py::_calc_dayun_improved::equality::男": "DOWN",
    "src/engines/bazi.py::_fallback_dayun::equality::男": "DOWN",
    "src/engines/shensha.py::_per_pillar_rules::equality::男": "DOWN",
    "src/engines/shensha.py::shensha_of_dayun::equality::男": "DOWN",
    "src/engines/ziwei.py::_calc_dayun::equality::男": "DOWN",
    "src/engines/fengshui.py::_calculate_person_gua::equality::男": "DOWN",
    "src/engines/chenggu.py::chenggu_verse::membership::male|男": "DIFF",
    "src/utils/fact_guard.py::guard_gender_terms::equality::女": "DISPLAY",
    # ── 展示 / 输出编码 ──────────────────────────────────────────────────
    "src/api/paipan.py::_history_item::membership::female|女": "DISPLAY",
    "src/api/mingren.py::_serialize_chart::equality::男": "DISPLAY",
    "src/bot/handler.py::_rehang_gender_echo::equality::女": "DISPLAY",
    "src/bot/handler.py::_rehang_gender_echo::equality::男": "DISPLAY",
    "src/tools/hehun.py::format_hehun_card::equality::男": "DISPLAY",
    "src/tools/hehun.py::format_hehun_card::equality::女": "DISPLAY",
    "src/api/ming.py::ming_generate::equality::男": "OUTENC",
    "src/tools/naming.py::build_pool::equality::男": "OUTENC",
    "src/tools/naming.py::build_pool::equality::女": "OUTENC",
    "src/utils/fact_guard.py::gender_label::membership::m|male|男": "DISPLAY",
    "src/utils/fact_guard.py::gender_label::membership::f|female|女": "DISPLAY",
}

#: JS 侧**按文件**表态（不放行号：JS 只做正则上界，逐行钉会变成噪音）。
#: 值 = 该文件里出现过的词集（元组排序后）。**多一个词集/多一个文件 ⇒ 红**。
JS_FILES: Dict[str, Tuple[str, ...]] = {
    # k84 必修5：`utils/persons.js::payloadOf` 的**前端内联别名表已删除** —— 改为
    # 原样透传（别名判定只发生在后端唯一表 `GENDER_ALIASES`），故该文件的词集从
    # `female|male|unknown|女|男` 缩到 `female|male|女|男`（代码事实：改后 payloadOf 里
    # 只剩 `gl === 'male' ? '男'` / `gl === 'female' ? '女'` 这一对**历史展示英文**
    # 的折中，与同文件 genderCN 的展示兼容同源；`unknown` 字面量不再出现在该行）。
    # 这是**如实反映现状**的词集收缩（不是"把新增消费点藏起来"）：本条改前实测
    # `--check` 报「新增词集 ['female|male|女|男'] / 词集消失 ['female|male|unknown|女|男']」，
    # 逐条按实况更新。若日后有人再往该文件加一张前端别名表，仍会以**新增词集**报警。
    "miniprogram/utils/persons.js": (
        "female|male|女|男", "female|女", "male|男", "unknown|女|男",
    ),
    "miniprogram/utils/api.js": ("female|male", "female|女|男"),
    "miniprogram/pages/bazi/bazi.js": (
        "female|male|女", "female|male|女|男", "female|女|男", "male", "女",
    ),
    "miniprogram/pages/duipan/duipan.js": ("female|女|男", "male"),
    "miniprogram/pages/favorites/favorites.js": ("男",),
    "miniprogram/pages/hehun/hehun.js": ("female", "female|male", "male"),
    "miniprogram/pages/love/love.js": ("female", "male"),
    "miniprogram/pages/ming/ming.js": ("female|male|男", "女", "女|男"),
    "miniprogram/pages/onboarding/onboarding.js": ("女",),
    "miniprogram/pages/paipan/paipan.js": ("female|male", "female|女|男", "male"),
    "miniprogram/pages/persons/persons.js": ("女",),
    "miniprogram/pages/xingming/xingming.js": ("female|male", "male", "male|女|男"),
}
#: JS 侧表态（**统一一条**，因为前端契约只有一个收口点）：
#: 前端只认 `男/女/unknown` 与历史 `male/female`（显示兼容），契约由
#: `miniprogram/tests/gender_contract.test.js` 钉死；**前端不决定排盘**（后端归一
#: 在后端做），故 JS 侧不要求与本表逐词等价 —— 但**新增文件/新增词集必须来表态**。
#:
#: k84 必修5 的边界更新（**如实登记，供下批判据设计参考**）：`utils/persons.js::
#: payloadOf`（前端**唯一**的内联别名表）已改为**原样透传**，别名判定只发生在后端
#: 本表 ⇒ JS 面上这个文件的词集从"别名表"退化成"历史展示英文的折中 + 展示函数"。
#: 后果（与 docstring「如实边界」③ 相关）：JS 侧上界**不再**是"前端在判定性别"的
#: 证据，只能证明"该文件里还有没有性别字面量"；若将来前端改成从配置/接口取词集
#: （连 male/female 折中都不写字面量），JS 面就整体退化为**空转判据**（这是既有的
#: 失效条件，不是本批新引入的；本批只是让它更靠近一步）。新增消费点仍会被
#: "新文件/新词集"报警挡住，机制不变。



def js_files(hits: List[dict]) -> Dict[str, Tuple[str, ...]]:
    """JS 侧折叠到**文件**粒度：`文件 → 该文件出现过的词集（排序）`。"""
    acc: Dict[str, set] = {}
    for h in (x for x in hits if x["kind"] == "js-literal"):
        acc.setdefault(h["file"], set()).add("|".join(h["tokens"]))
    return {f: tuple(sorted(t)) for f, t in acc.items()}


def check(hits: List[dict]) -> Tuple[List[str], List[str]]:
    """返回 (未表态的站点, 已表态但已消失的站点)。Python 逐站点、JS 逐文件。"""
    py = {k: v for k, v in collapsed(
        [x for x in hits if x["kind"] != "js-literal"]).items()}
    added = [f"src: {k} ×{v['count']} 行={v['lines']}"
             for k, v in sorted(py.items()) if k not in SITES]
    removed = [f"src: {k}" for k in SITES if k not in py]
    cur_js = js_files(hits)
    added += [f"js: {f} 词集={list(t)}" for f, t in sorted(cur_js.items())
              if f not in JS_FILES]
    added += [f"js: {f} **新增词集** {sorted(set(t) - set(JS_FILES.get(f, ())))}"
              for f, t in sorted(cur_js.items())
              if f in JS_FILES and set(t) - set(JS_FILES[f])]
    removed += [f"js: {f}（已不在扫描结果里）" for f in JS_FILES if f not in cur_js]
    removed += [f"js: {f} 词集消失 {sorted(set(JS_FILES[f]) - set(cur_js[f]))}"
                for f in JS_FILES if f in cur_js and set(JS_FILES[f]) - set(cur_js[f])]
    return added, removed


def main() -> int:
    ap = argparse.ArgumentParser(description="性别消费点盘点（可复跑判据）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--check", action="store_true", help="与已表态清单比对")
    ap.add_argument("--root", default=str(REPO), help="仓库根（默认脚本所在仓库）")
    args = ap.parse_args()
    hits = collect(Path(args.root))
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return 0
    if args.check:
        added, removed = check(hits)
        if not added and not removed:
            print(f"OK：{len(hits)} 处性别消费点全部已表态")
            return 0
        for a in added:
            print(f"未表态的新增性别消费点: {a}")
        for r in removed:
            print(f"已表态但已消失（请更新清单）: {r}")
        return 1
    for h in hits:
        print(f"{h['file']}:{h['line']} [{h['kind']}] {h['scope']}() {h['tokens']}")
    print(f"TOTAL {len(hits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
