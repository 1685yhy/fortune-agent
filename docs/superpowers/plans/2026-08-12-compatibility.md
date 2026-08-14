# 双人合盘（缘笺）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现「双人合盘 · 缘笺」：免费钩子（契合分+等级+三维得分条+一句话缘语+墨韵缘笺分享图）+ ¥19.9 深度报告（前世今生/相处模式/矛盾点与化解/契合详情 四章，复用 deep_report 支付通道），hehun 页升级为双人合盘页、love 页并入，三版小程序同步。

**Architecture:** 后端新增四模块：`yuan_quote.py`（缘语管线：模板库+可选 LLM 润色+红线校验+模板兜底）、`union.py`（合盘聚合引擎：合婚引擎×0.6 + 合盘引擎×0.4 归一打分 + 特征提取 + 脱敏缘笺数据）、`yuan_report.py`（深度报告四章管线：古籍 RAG 前世今生/相处模式/矛盾点化解/契合详情）、`src/api/union.py`（POST /api/union 聚合 API：免费档零落库瞬态计算，付费档走 deep_report 购买校验+consultations 归档）。前端 hehun 页升级为双人合盘页（双表单+档案直选+关系标签+免费结果+缘笺+付费墙），love 页并入跳转，today 页加入口卡，对话 hehun 意图扩展触发词并返回引导卡片，shareCard.js 新增 `drawYuanCard`（双色印章+脱敏）。

**Tech Stack:** FastAPI + sqlite（现有 dao/member_dao 模式）、BaziEngine 真实排盘 + HehunEngine + compatibility 规则引擎（纯规则零 LLM 成本）、古籍 RAG（`retriever.search(query, category="hehun")`）、LLM（deepseek，仅缘语润色/前世今生叙事，失败即规则兜底）、小程序原生（三版：miniprogram/miniprogram_simple/miniprogram_fusion）、测试用 scripts/test_*.py（TestClient+断言）。

## Global Constraints

- 所有后端代码遵循现有模式：API 在 `src/api/*.py`（全挂 require_user 鉴权）、引擎在 `src/engines/*.py`、env 经 `src/config.load_env_file(".env")`；测试文件放 `scripts/test_*.py`，全部命令 `cd /mnt/e/fortune-agent` 后执行
- **隐私红线（最高优先级）**：① 对方生辰只存本机 localStorage（key `yuan_ta_birth`），绝不进 persons 云端接口；② 服务器对端生辰仅本次请求内存使用（transient）、**免费档零落库**，付费归档只写脱敏摘要与报告正文，绝不写双方生辰；③ 缘笺/缘语/报告一律脱敏（无时辰/出生地/姓名）；④ 全接口 require_user
- **支付红线**：paid=true 必须校验 deep_report 购买凭证（`member_dao.get_user_purchase`）或体验模式，否则 403；免费档绝不返回报告正文；不造假数据（双引擎真实排盘打分，禁止伪随机）
- **质量红线**：报告/缘语不出现「注定/必离婚/必分手/必成/化灾/改运/斩桃花」类断言与承诺，落款「签文只作心意，不作断言 · 明灯拟」
- **兼容红线**：`/api/hehun`、`/api/love/compatibility`、`/pages/hehun/hehun`、`/pages/love/love` 旧路由全部保留（hehun 页原地升级、love 页跳转并入）；pay.py `PRODUCTS` 不加新商品，深度报告直接复用现有 `deep_report`（19.9）通道
- 前端三版同步：miniprogram（墨韵,正版）/ miniprogram_simple / miniprogram_fusion，结构一致仅皮肤；测试脚本运行 `.venv/bin/python3 scripts/test_xxx.py`
- 测试鉴权模式（参考 scripts/test_p2.py）：`from src.security.auth import AuthHandler, set_auth_handler` → `set_auth_handler(AuthHandler())` → `_auth.create_user_token(uid)`，假 token 由同一实例签发
- 不 commit 敏感配置（.env 已 gitignore）；每个任务 Commit 明确

---

### Task 1: 缘语管线（免费钩子文案）

**Files:**
- Create: `src/engines/yuan_quote.py`
- Test: `scripts/test_yuan_quote.py`

**Interfaces:**
- Consumes: 无（纯规则+外部可选 `polish_fn` 回调）
- Produces: `generate_yuan_quote(level_label: str, features: list, relation: str = "", cliffhanger: bool = False, polish_fn=None) -> dict`；返回 `{main, suffix, cliffhanger, full}`（main ≤24 字，free 档悬念半句，模板兜底）

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_yuan_quote.py`:
```python
"""缘语管线测试：确定性/等级/关系口吻/红线校验/LLM兜底/悬念半句"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.yuan_quote import generate_yuan_quote, _validate

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

FEATS = ["五行互补", "双天乙贵人"]
# 1. 确定性：同输入同输出
q1 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
q2 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
check("同输入结果确定", q1 == q2)

# 2. 等级映射：主句来自对应等级模板库
q3 = generate_yuan_quote("情投意合", FEATS, relation="恋人")
check("等级模板库", "情投意合" not in q3["main"] and len(q3["main"]) > 0)

# 3. 关系口吻：暗恋 vs 夫妻 后缀不同
q4 = generate_yuan_quote("天作之合", FEATS, relation="暗恋")
q5 = generate_yuan_quote("天作之合", FEATS, relation="夫妻")
check("关系口吻差异", q4["suffix"] != "" and q5["suffix"] != "" and q4["suffix"] != q5["suffix"])

# 4. 红线：LLM 返回绝对断言 → 拒绝，保留模板原句
q6 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=lambda t: "你们必成良缘，注定在一起")
check("绝对断言被拒", q6["main"] in ["金玉相逢，良缘可期", "双星交辉，缘分天成", "天时地利，恰逢其人"])

# 5. 兜底：polish_fn 抛异常 → 模板原句
def boom(t):
    raise RuntimeError("llm down")
q7 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=boom)
check("LLM失败模板兜底", len(q7["main"]) > 0)

# 6. 合法润色被采用
q8 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=lambda t: "愿你们两心相印，岁岁年年")
check("合法润色采用", q8["main"] == "愿你们两心相印，岁岁年年")

# 7. 悬念半句：免费档 cliffhanger=True 时 full 以省略号结尾
q9 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
q10 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=False)
check("悬念半句", q9["cliffhanger"].endswith("……") and q10["cliffhanger"] == "")

# 8. 红线校验函数：主句长度 ≤24
check("主句不超24字", all(len(q["main"]) <= 24 for q in (q1, q3, q6, q7, q8, q9)))
check("红线词拒绝", not _validate("你们必成，一定在一起") and _validate("金玉相逢，良缘可期"))

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_yuan_quote.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.yuan_quote'`

- [ ] **Step 3: 实现管线**

创建 `src/engines/yuan_quote.py`:
```python
"""缘语管线：等级+特征+关系标签 → 模板库确定性选取 → 可选 LLM 润色 → 红线校验 → 模板兜底。

免费档红线：
- 2 秒内返回（LLM 润色超时 2s，失败即用模板原句，绝不编造）；
- 不点破具体事件、不涉对方姓名、不含绝对断言（必成/必散/注定/一定/保证/离婚/分手）；
- 主句 ≤24 字（可晒体），不以付费为要挟话术。
"""
import hashlib
import logging

logger = logging.getLogger(__name__)

_QUOTES_BY_LEVEL = {
    "天作之合": ["金玉相逢，良缘可期", "双星交辉，缘分天成", "天时地利，恰逢其人"],
    "情投意合": ["心意相通，如沐春风", "彼此滋养，越处越顺", "一见如故，久处不厌"],
    "相得益彰": ["一刚一柔，互补成章", "差异生趣，相映生辉", "你补我短，我成你长"],
    "和而不同": ["各有天地，仍可同行", "风格不同，心却有约", "慢火细炖，感情愈醇"],
    "细水长流": ["细水长流，历久弥新", "来日方长，温柔以待", "静水流深，情在久处"],
}

_RELATION_SUFFIX = {
    "暗恋": "缘分已起，静待花开",
    "恋人": "此缘正浓，宜温柔相待",
    "夫妻": "共度余生，细水长流",
    "朋友": "相逢是缘，同行是福",
}

_NEGATIVE_MARK = ("六冲", "六害", "相刑", "相克")
_REDLINE_TOKENS = ("必成", "必散", "注定", "一定", "保证", "离婚", "分手", "必离")
_MAX_MAIN_LEN = 24


def _validate(text: str) -> bool:
    """红线校验：非空、≤24 字、无绝对断言、无脏文本。"""
    t = (text or "").strip()
    if not t or len(t) > _MAX_MAIN_LEN:
        return False
    if any(k in t for k in _REDLINE_TOKENS):
        return False
    if "http" in t or "{" in t or "}" in t:
        return False
    return True


def _pick_template(level: str, features: list, relation: str) -> str:
    """按 特征+关系+等级 哈希确定性选取（同一对情侣结果稳定，非随机）。"""
    candidates = _QUOTES_BY_LEVEL.get(level, _QUOTES_BY_LEVEL["细水长流"])
    seed = hashlib.md5(("|".join(sorted(features)) + "|" + relation + "|" + level).encode()).hexdigest()
    return candidates[int(seed, 16) % len(candidates)]


def _pick_cliffhanger(features: list) -> str:
    """悬念半句（免费档付费墙钩子）：负特征 → 化解钩子；否则默契钩子。"""
    if any(k in f for f in features for k in _NEGATIVE_MARK):
        return "只是暗处尚有一克，需一份化解的智慧……"
    return "还有一份暗藏的默契，等着你们亲手揭开……"


def generate_yuan_quote(level_label: str, features: list, relation: str = "",
                        cliffhanger: bool = False, polish_fn=None) -> dict:
    """生成一句话缘语。

    - main: 主句（≤24 字）；polish_fn 返回合法文本时采用润色版，异常/非法 → 模板原句；
    - suffix: 关系口吻后缀（无关系标签为空串）；
    - cliffhanger: 悬念半句（免费档展示，点「展开」见付费墙）；
    - full: 完整拼接文本。
    """
    main = _pick_template(level_label, features or [], relation)
    if polish_fn is not None:
        try:
            polished = polish_fn(main)
            if isinstance(polished, str) and _validate(polished):
                main = polished
        except Exception as e:  # LLM 失败 → 模板兜底（宁缺毋滥）
            logger.warning("缘语润色失败(模板兜底): %s", e)
    suffix = _RELATION_SUFFIX.get(relation, "")
    tail = _pick_cliffhanger(features or []) if cliffhanger else ""
    full = main
    if suffix:
        full += "，" + suffix
    full += "。"
    if tail:
        full += tail
    return {"main": main, "suffix": suffix, "cliffhanger": tail, "full": full}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_yuan_quote.py`
Expected: `ALL PASS (9)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/yuan_quote.py scripts/test_yuan_quote.py
git commit -m "feat(union): 缘语管线(模板库确定性选取+LLM润色可弃+红线校验+悬念半句)"
```

---

### Task 2: 合盘聚合引擎（双引擎归一 + 特征提取 + 脱敏缘笺数据）

**Files:**
- Create: `src/engines/union.py`
- Test: `scripts/test_union_engine.py`

**Interfaces:**
- Consumes: `src/engines/hehun.HehunEngine`（match）、`src/engines/hehun.WUXING_KE`（纳音相克判定）、`src/api/compatibility._compute_match_score`（复用，同 love.py 既有做法）
- Produces: `normalize_score(hehun_score, compat_score) -> int`（0.6/0.4 归一）；`get_level(score) -> dict`（SCORE_LEVELS）；`extract_features(hehun_result, compat_match, bazi1, bazi2) -> list`；`desensitize_birth(year, month, day, day_ganzhi, shengxiao) -> str`；`build_yuan_card(...) -> dict`；`run_union(...) -> dict`（评分/等级/三维得分条/特征/脱敏缘笺/报告素材明细/transient 标记）

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_union_engine.py`:
```python
"""合盘聚合引擎测试：双引擎归一/等级边界/特征提取/缘笺脱敏"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.hehun import HehunEngine
from src.engines.union import (normalize_score, get_level, extract_features,
                               desensitize_birth, run_union)
from src.api.compatibility import _compute_match_score

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

class FakeBazi:
    def __init__(self, bazi, day_master, wuxing, shensha, nayin):
        self.bazi = bazi
        self.day_master = day_master
        self.wuxing = wuxing
        self.shensha = shensha
        self.nayin = nayin

class FakePerson:
    def __init__(self, year, month, day):
        self.year = year; self.month = month; self.day = day

# 子×丑 → 生肖六合；双方皆带天乙贵人；纳音 天上火×大林木（木生火，不相克）
fake1 = FakeBazi(bazi=[["甲","子"],["丙","寅"],["戊","午"],["庚","申"]],
                 day_master="戊土", wuxing={"木":2,"火":2,"土":1,"金":2,"水":1},
                 shensha=["天乙贵人"], nayin=["海中金","炉中火","天上火","石榴木"])
fake2 = FakeBazi(bazi=[["乙","丑"],["丁","卯"],["己","酉"],["辛","亥"]],
                 day_master="己土", wuxing={"木":1,"火":1,"土":2,"金":2,"水":2},
                 shensha=["天乙贵人"], nayin=["海中金","炉中火","大林木","平地木"])

# 1. 双引擎归一
check("归一 100×100", normalize_score(100, 100) == 100)
check("归一 50×50", normalize_score(50, 50) == 50)
check("归一 40×60", normalize_score(40, 60) == 48)
check("归一越界收敛", 0 <= normalize_score(-5, 999) <= 100)

# 2. 等级边界（与 love.py SCORE_LEVELS 一致）
check("90 天作之合", get_level(90)["label"] == "天作之合")
check("89 情投意合", get_level(89)["label"] == "情投意合")
check("60 和而不同", get_level(60)["label"] == "和而不同")
check("59 细水长流", get_level(59)["label"] == "细水长流")

# 3. 特征提取（真实引擎跑 fake 命盘）
hr = HehunEngine().match(fake1, fake2)
cm = _compute_match_score(fake1, fake2)
feats = extract_features(hr, cm, fake1, fake2)
check("生肖六合特征", any("六合" in f for f in feats))
check("双天乙贵人特征", "双天乙贵人" in feats)
check("特征去重保序", len(feats) == len(set(feats)))

# 4. 脱敏显示串：无时辰/无出生地/无姓名，格式固定
birth_a = desensitize_birth(1990, 5, 20, "戊午", "鼠")
check("脱敏格式", re.match(r"^[一-鿿]{2} · \d{4}年\d{1,2}月\d{1,2}日 属[一-鿿]$", birth_a) is not None)
check("脱敏不含时辰", "时" not in birth_a and "23" not in birth_a)

# 5. run_union 聚合：结构完整
u = run_union(hr, cm, fake1, fake2, FakePerson(1990, 5, 20), FakePerson(1992, 8, 15),
              relation="恋人", quote={"full": "金玉相逢，良缘可期"})
check("评分范围", 0 <= u["score"] <= 100 and u["levelLabel"] in ("天作之合", "情投意合", "相得益彰", "和而不同", "细水长流"))
check("三维得分条", u["dimensions"]["wuxing"]["max"] == 40 and u["dimensions"]["shengxiao"]["max"] == 25
      and u["dimensions"]["rizhu"]["max"] == 35)
check("transient 标记", u.get("transient") is True)
check("缘笺数据完整", u["yuan_card"]["sealChar"] == "丑牛" and u["yuan_card"]["disclaimer"] == "签文只作心意，不作断言")
check("缘笺生辰脱敏", "时" not in u["yuan_card"]["birthA"] and "上海" not in u["yuan_card"]["birthB"])
check("报告素材明细", "compat" in u["raw"] and "hehun_wuxing" in u["raw"])

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_union_engine.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.union'`

- [ ] **Step 3: 实现引擎**

创建 `src/engines/union.py`:
```python
"""合盘聚合引擎：双引擎归一打分 + 特征提取 + 等级 + 脱敏缘笺数据。

评分（双引擎真实排盘打分，禁止伪随机/假结果）：
    合婚引擎（五行互补40 + 生肖25 + 日柱35，0-100）× 0.6
  + 合盘引擎（日主生克/夫妻宫六合/天乙贵人/五行互补/纳音，0-100）× 0.4
等级：≥90 天作之合 / ≥80 情投意合 / ≥70 相得益彰 / ≥60 和而不同 / <60 细水长流。

隐私红线：本模块产出的缘笺数据一律脱敏——只含 日柱+年月日+生肖，
不含时辰/出生地/姓名；双色印章仅用 地支+生肖，不涉日期。
"""
import logging

from src.engines.hehun import WUXING_KE

logger = logging.getLogger(__name__)

# 与 src/api/love.py SCORE_LEVELS 保持一致（等级称谓复用）
SCORE_LEVELS = [
    {"min": 90, "label": "天作之合", "sublabel": "Perfect Match"},
    {"min": 80, "label": "情投意合", "sublabel": "Soul Connection"},
    {"min": 70, "label": "相得益彰", "sublabel": "Complementary"},
    {"min": 60, "label": "和而不同", "sublabel": "Harmony in Diversity"},
    {"min": 0, "label": "细水长流", "sublabel": "Gentle Flow"},
]

_HEHUN_W = 0.6   # 合婚引擎权重
_COMPAT_W = 0.4  # 合盘引擎权重


def normalize_score(hehun_score: int, compat_score: int) -> int:
    """双引擎归一 0-100（越界输入收敛）。"""
    return max(0, min(100, round(_HEHUN_W * int(hehun_score) + _COMPAT_W * int(compat_score))))


def get_level(score: int) -> dict:
    for level in SCORE_LEVELS:
        if score >= level["min"]:
            return level
    return SCORE_LEVELS[-1]


def _nayin_wuxing(bazi) -> str:
    """日柱纳音的五行（"天上火"→"火"）；无纳音返回空串。"""
    nayin = getattr(bazi, "nayin", []) or []
    if len(nayin) < 3 or not nayin[2]:
        return ""
    return str(nayin[2])[-1]


def extract_features(hehun_result, compat_match, bazi1, bazi2) -> list:
    """特征词：生肖关系/日支关系/日干关系/日主五行关系/夫妻宫六合/双天乙贵人/五行互补/纳音相克。"""
    features = []
    sd = hehun_result.shengxiao_detail
    rel = (sd or {}).get("relation", "")
    if rel:
        features.append(rel)
    rd = hehun_result.rizhu_detail
    zr = (rd or {}).get("ri_zhi_relation", "")
    if zr:
        features.append(zr)
    gr = (rd or {}).get("ri_gan_relation", "")
    if gr:
        features.append(f"日干{gr}")
    dm = (hehun_result.bazi_match or {}).get("day_master_relation", "")
    if dm:
        features.append(dm)
    if compat_match.get("combo_bonus", 0) > 0:
        features.append("夫妻宫六合")
    if compat_match.get("shensha_bonus", 0) > 0:
        features.append("双天乙贵人")
    if compat_match.get("complement_bonus", 0) > 0:
        features.append("五行互补")
    n1, n2 = _nayin_wuxing(bazi1), _nayin_wuxing(bazi2)
    if n1 and n2 and (WUXING_KE.get(n1) == n2 or WUXING_KE.get(n2) == n1):
        features.append("纳音相克")
    return list(dict.fromkeys(features))


def desensitize_birth(year, month, day, day_ganzhi: str, shengxiao: str) -> str:
    """脱敏显示串：'日柱 · YYYY年M月D日 属X'（无时辰/出生地/姓名）。"""
    return f"{day_ganzhi} · {year}年{month}月{day}日 属{shengxiao}"


def build_yuan_card(score: int, level_label: str, quote: dict,
                    birth_a: str, birth_b: str, seal_char: str) -> dict:
    """缘笺数据（全部脱敏）：双人生辰显示串/契合分/等级/缘语/双色印章字。"""
    return {
        "birthA": birth_a, "birthB": birth_b,
        "score": score, "levelLabel": level_label,
        "quote": (quote or {}).get("full", ""), "quoteParts": quote or {},
        "sealChar": seal_char, "disclaimer": "签文只作心意，不作断言",
    }


def run_union(hehun_result, compat_match, bazi1, bazi2, person_a, person_b,
              relation: str = "", quote: dict = None) -> dict:
    """聚合：评分/等级/三维得分条/特征/缘笺数据/报告素材明细。

    person_a/person_b：含 year/month/day 的入参对象（仅取年月日做脱敏显示）。
    """
    score = normalize_score(hehun_result.score, compat_match.get("final_score", 0))
    level = get_level(score)
    sd = hehun_result.shengxiao_detail
    rd = hehun_result.rizhu_detail

    dimensions = {
        "wuxing": {"score": int(hehun_result.wuxing_score), "max": 40},
        "shengxiao": {"score": int(hehun_result.shengxiao_score), "max": 25,
                      "relation": (sd or {}).get("relation", "")},
        "rizhu": {"score": int(hehun_result.rizhu_score), "max": 35,
                  "relation": (rd or {}).get("ri_zhi_relation", "")},
    }
    features = extract_features(hehun_result, compat_match, bazi1, bazi2)

    birth_a = desensitize_birth(person_a.year, person_a.month, person_a.day,
                                "".join(bazi1.bazi[2]), (sd or {}).get("shengxiao1", ""))
    birth_b = desensitize_birth(person_b.year, person_b.month, person_b.day,
                                "".join(bazi2.bazi[2]), (sd or {}).get("shengxiao2", ""))
    year_zhi_b = bazi2.bazi[0][1] if bazi2.bazi and len(bazi2.bazi[0]) > 1 else ""
    seal_char = f"{year_zhi_b}{(sd or {}).get('shengxiao2', '')}" if year_zhi_b else "缘"

    return {
        "score": score, "levelLabel": level["label"], "levelSublabel": level["sublabel"],
        "dimensions": dimensions, "features": features, "relation": relation,
        "yuan_card": build_yuan_card(score, level["label"], quote or {},
                                     birth_a, birth_b, seal_char),
        # 报告素材（付费档第四章使用）：双引擎明细，仅内存传递
        "raw": {
            "hehun_wuxing": hehun_result.bazi_match,
            "hehun_shengxiao": hehun_result.shengxiao_detail,
            "hehun_rizhu": hehun_result.rizhu_detail,
            "compat": {k: compat_match.get(k) for k in
                       ("base_score", "wuxing_relation", "relation_desc",
                        "complement_bonus", "shensha_bonus", "combo_bonus", "final_score")},
        },
        "transient": True,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_union_engine.py`
Expected: `ALL PASS (14)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/union.py scripts/test_union_engine.py
git commit -m "feat(union): 合盘聚合引擎(双引擎0.6/0.4归一+特征提取+脱敏缘笺数据)"
```

---

### Task 3: 合盘聚合 API（免费档 POST /api/union）

**Files:**
- Create: `src/api/union.py`
- Modify: `src/main.py`（lifespan 注入 setup + include_router）
- Test: `scripts/test_union_api.py`

**Interfaces:**
- Consumes: Task 1 `generate_yuan_quote`、Task 2 `run_union`、`src/api/hehun._resolve_pair/BaziInput`（双契约兼容）、`src/api/compatibility._compute_match_score`、`src.security.auth.require_user`
- Produces: `POST /api/union` body `{person_a/person_b 或 person1/person2, relation?, paid?}` → 免费档 `{score, levelLabel, levelSublabel, dimensions, features, relation, quote, quoteParts, yuan_card, paywall, transient}`（paid=true 分支在 Task 5 接入）

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_union_api.py`:
```python
"""双人合盘聚合 API 测试（TestClient+临时 DB+假 token）"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient
from src.security.auth import AuthHandler, set_auth_handler

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# ── 鉴权与引擎注入（参考 test_p2 模式）──
_auth = AuthHandler()
set_auth_handler(_auth)

import src.api.union as union_mod
from src.engines.hehun import HehunEngine
from src.engines.bazi import BaziEngine

class FakeDao:
    """记录归档调用，验证免费档零落库 / 付费归档脱敏。"""
    def __init__(self):
        self.calls = []
    def save_consultation(self, user_id, question, chart_result=None, analysis="", intent="bazi"):
        self.calls.append({"user_id": user_id, "question": question,
                           "chart": chart_result, "analysis": analysis, "intent": intent})
        return 1

fake_dao = FakeDao()
union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=None, dao=fake_dao)

from src.main import app
client = TestClient(app)
h = {"Authorization": "Bearer " + _auth.create_user_token("union_user")}

# 1. 未登录 401
r = client.post("/api/union", json={})
check("未登录 401", r.status_code == 401)

# 2. 缺一方生辰 400（双契约校验与 /api/hehun 一致）
r = client.post("/api/union", headers=h, json={"person_a": {"year": 1990, "month": 5, "day": 20}})
check("缺对方生辰 400", r.status_code == 400)

# 3. 免费档成功（原生契约 person_a/person_b）
r = client.post("/api/union", headers=h, json={
    "person_a": {"year": 1990, "month": 5, "day": 20, "hour": 8, "city": "北京", "gender": "男"},
    "person_b": {"year": 1992, "month": 8, "day": 15, "hour": 14, "city": "上海", "gender": "女"},
    "relation": "恋人"})
d = r.json()
check("免费档 200", r.status_code == 200)
check("契合分与等级", 0 <= d["score"] <= 100 and d["levelLabel"] in ("天作之合", "情投意合", "相得益彰", "和而不同", "细水长流"))
check("三维得分条", d["dimensions"]["wuxing"]["max"] == 40 and d["dimensions"]["rizhu"]["max"] == 35)
check("缘语含悬念半句", d["quoteParts"]["main"] and d["quoteParts"]["cliffhanger"].endswith("……"))
check("缘语主句≤24字", len(d["quoteParts"]["main"]) <= 24)
check("付费墙指向 deep_report", d["paywall"]["product"] == "deep_report" and d["paywall"]["price"] == 19.9)
check("缘笺生辰脱敏", re.match(r"^[一-鿿]{2} · \d{4}年\d{1,2}月\d{1,2}日 属[一-鿿]$", d["yuan_card"]["birthA"]) is not None
      and "时" not in d["yuan_card"]["birthA"] and "上海" not in d["yuan_card"]["birthB"])
check("免费档零落库", len(fake_dao.calls) == 0)

# 4. 免费档小程序契约（person1/person2 + birthHour 时辰序号）
r = client.post("/api/union", headers=h, json={
    "person1": {"birthYear": 1990, "birthMonth": 5, "birthDay": 20, "birthHour": 5, "gender": "male"},
    "person2": {"birthYear": 1992, "birthMonth": 8, "birthDay": 15, "birthHour": 6, "gender": "female"}})
check("小程序契约兼容", r.status_code == 200 and 0 <= r.json()["score"] <= 100)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_union_api.py`
Expected: `ModuleNotFoundError: No module named 'src.api.union'`（或 404）

- [ ] **Step 3: 实现 API**

创建 `src/api/union.py`:
```python
"""双人合盘聚合 API — POST /api/union（免费档）。

免费档（paid=false）：契合分/等级/三维得分条/一句话缘语/缘笺脱敏数据。
纯规则+模板管线，LLM 仅作可弃的缘语润色（2s 超时，失败即模板兜底）。
付费档（paid=true）由 Task 5 接入：深度报告四章（deep_report 支付校验+归档）。

隐私红线：
- 双方生辰只在本请求内存中使用（transient），免费档全程零落库；
- 缘笺数据/缘语一律脱敏（不含时辰/出生地/姓名）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.engines.hehun import HehunEngine
from src.engines.bazi import BaziEngine
from src.engines.union import run_union
from src.engines.yuan_quote import generate_yuan_quote
from .hehun import BaziInput, _resolve_pair
from .compatibility import _compute_match_score

logger = logging.getLogger(__name__)

router = APIRouter(tags=["union"])

_hehun_engine: Optional[HehunEngine] = None
_bazi_engine: Optional[BaziEngine] = None
_llm_ref = None
_retriever = None
_member_dao = None
_dao = None


def setup(hehun_engine, bazi_engine, llm=None, retriever=None, member_dao=None, dao=None):
    """在主应用生命周期中注入引擎/LLM/检索器/支付/归档依赖。"""
    global _hehun_engine, _bazi_engine, _llm_ref, _retriever, _member_dao, _dao
    _hehun_engine = hehun_engine
    _bazi_engine = bazi_engine
    _llm_ref = llm
    _retriever = retriever
    _member_dao = member_dao
    _dao = dao


class UnionRequest(BaseModel):
    """双人合盘请求：双契约（person_a/b 原生 + person1/2 小程序）+ 关系标签 + 付费标记。"""
    person_a: Optional[BaziInput] = None
    person_b: Optional[BaziInput] = None
    person1: Optional[BaziInput] = None
    person2: Optional[BaziInput] = None
    relation: str = ""   # 恋人/暧昧/夫妻/朋友/暗恋（空=不标注）
    paid: bool = False


def _make_polish_fn():
    """缘语润色：LLM 超时 2s；无 LLM/异常 → 调用方模板兜底（免费档 2 秒内保证）。"""
    if _llm_ref is None or not getattr(_llm_ref, "api_key", ""):
        return None
    def polish(text: str) -> str:
        r = _llm_ref._call_deepseek_model(
            f"把以下缘语润色为一句不超过24个字的中文缘语，只输出润色后的句子：{text}",
            _llm_ref.model, max_tokens=60, timeout=2.0)
        return (r.response or "").strip()
    return polish


@router.post("/api/union")
async def union_match(req: UnionRequest, uid: str = Depends(require_user)):
    """合盘聚合：免费档即时返回；付费档见 Task 5（_require_paid + 报告生成 + 归档）。"""
    if _hehun_engine is None or _bazi_engine is None:
        raise HTTPException(status_code=503, detail="Union service not ready")

    # 双契约解析（与 /api/hehun 相同校验，缺任一方 400）
    a, b = _resolve_pair(req)

    # 双方真实排盘（时辰/出生地缺失由契约层归一为默认+结果标注，不阻断）
    r1 = _bazi_engine.calculate(a.year, a.month, a.day, a.hour, a.minute, a.city, a.gender)
    r2 = _bazi_engine.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender)

    hehun_result = _hehun_engine.match(r1, r2)
    compat_match = _compute_match_score(r1, r2)
    union = run_union(hehun_result, compat_match, r1, r2, a, b,
                      relation=req.relation)

    quote = generate_yuan_quote(union["levelLabel"], union["features"],
                                relation=req.relation, cliffhanger=True,
                                polish_fn=_make_polish_fn())
    union["yuan_card"]["quote"] = quote["full"]
    union["yuan_card"]["quoteParts"] = quote

    return {
        "score": union["score"], "levelLabel": union["levelLabel"],
        "levelSublabel": union["levelSublabel"],
        "dimensions": union["dimensions"], "features": union["features"],
        "relation": union["relation"],
        "quote": quote["full"], "quoteParts": quote,
        "yuan_card": union["yuan_card"],
        "paywall": {"product": "deep_report", "price": 19.9,
                    "message": "解锁深度合盘报告：前世今生 / 相处模式 / 矛盾点与化解 / 契合详情"},
        "transient": True,
    }
```

在 `src/main.py` 挂载（与现有 hehun setup 并列）:
```python
# Phase: 双人合盘聚合 API（免费钩子 + 付费深度报告）
from .api.union import setup as setup_union
from .api.union import router as union_router
setup_union(hehun_engine, engine, llm=llm, retriever=retriever,
            member_dao=member_dao, dao=dao)
app.include_router(union_router)             # POST /api/union
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_union_api.py`
Expected: `ALL PASS (13)`

- [ ] **Step 5: Commit**

```bash
git add src/api/union.py src/main.py scripts/test_union_api.py
git commit -m "feat(union): 合盘聚合 API 免费档(契合分/三维得分条/缘语/缘笺数据,零落库)"
```

---

### Task 4: 深度报告管线（前世今生/相处模式/矛盾点化解/契合详情）

**Files:**
- Create: `src/engines/yuan_report.py`
- Test: `scripts/test_yuan_report.py`

**Interfaces:**
- Consumes: `run_union` 的 `raw` 素材、`retriever.search(query, category="hehun", top_k=10)`（古籍 RAG，mock 于测试）、`llm._call_deepseek_model`（可弃）
- Produces: `build_report(union: dict, bazi1, bazi2, retriever=None, llm=None) -> dict`；返回 `{chapters: [{title, content}×4], citations: [{book, text}], full_text}`；`TRUST_STATEMENT` 落款

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_yuan_report.py`:
```python
"""深度报告管线测试：四章结构/古籍引用/红线/规则兜底/契合详情直出"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.yuan_report import build_report, TRUST_STATEMENT

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

class FakeBazi:
    def __init__(self, bazi, nayin):
        self.bazi = bazi
        self.nayin = nayin

b1 = FakeBazi(bazi=[["甲", "子"], ["丙", "寅"], ["戊", "午"], ["庚", "申"]], nayin=["海中金", "炉中火", "天上火", "石榴木"])
b2 = FakeBazi(bazi=[["乙", "丑"], ["丁", "卯"], ["己", "酉"], ["辛", "亥"]], nayin=["海中金", "炉中火", "大林木", "平地木"])

union = {
    "score": 84, "levelLabel": "情投意合",
    "dimensions": {"wuxing": {"score": 35, "max": 40}, "shengxiao": {"score": 25, "max": 25, "relation": "六合（上等婚配）"},
                   "rizhu": {"score": 17, "max": 35, "relation": "平和（无特殊关系）"}},
    "features": ["六合（上等婚配）", "双天乙贵人", "纳音相克", "五行相克"],
    "relation": "恋人",
    "raw": {
        "hehun_wuxing": {"complement_desc": "五行互补性强", "score_breakdown": {"互补得分": 30, "日主关系得分": 5}},
        "hehun_shengxiao": {"description": "生肖鼠与牛：六合（上等婚配）", "score": 25},
        "hehun_rizhu": {"description": "日支平和，日干比和", "score": 17},
        "compat": {"base_score": 80, "wuxing_relation": "比和", "relation_desc": "金金比和",
                   "complement_bonus": 10, "shensha_bonus": 5, "combo_bonus": 0, "final_score": 95},
    },
    "transient": True,
}

# 1. 无 RAG 无 LLM → 四章完整（全规则兜底）
rep = build_report(union, b1, b2, retriever=None, llm=None)
check("四章齐全", [c["title"] for c in rep["chapters"]] == ["前世今生", "相处模式", "矛盾点与化解", "契合详情"])
check("落款存在", rep["full_text"].endswith(TRUST_STATEMENT))
check("前世今生规则兜底", len(rep["chapters"][0]["content"]) > 30)
check("无引用时 citations 空", rep["citations"] == [])

# 2. 古籍 RAG 引用进入前世今生（书名可见）
class FakeRetriever:
    def search(self, query, category="", top_k=10):
        return [type("H", (), {"text": "申月金旺，逢土生扶，反为有用之才", "source": "穷通宝鉴"})()]
rep2 = build_report(union, b1, b2, retriever=FakeRetriever(), llm=None)
check("引用带书名", rep2["citations"] and rep2["citations"][0]["book"] == "穷通宝鉴")
check("前世今生引原典", "穷通宝鉴" in rep2["chapters"][0]["content"])

# 3. LLM 叙事正常 → 采用 LLM 文本
class FakeLLM:
    api_key = "test"
    model = "deepseek-chat"
    def _call_deepseek_model(self, prompt, model, max_tokens=700, timeout=30):
        return type("R", (), {"response": "前世的一盏灯，化作今生人群里的一眼认出，熟悉感不请自来。"})()
rep3 = build_report(union, b1, b2, retriever=None, llm=FakeLLM())
check("LLM 叙事采用", "前世" in rep3["chapters"][0]["content"])

# 4. LLM 越红线（绝对断言）→ 规则兜底
class BadLLM(FakeLLM):
    def _call_deepseek_model(self, prompt, model, max_tokens=700, timeout=30):
        return type("R", (), {"response": "你们注定离婚，必成怨偶。"})()
rep4 = build_report(union, b1, b2, retriever=None, llm=BadLLM())
check("红线断言被拒回退", "注定" not in rep4["chapters"][0]["content"] and "必离婚" not in rep4["full_text"])

# 5. 矛盾点逐条配化解建议（六合不配建议，纳音相克/五行相克配）
rep5 = build_report(union, b1, b2, retriever=None, llm=None)
ch3 = rep5["chapters"][2]["content"]
check("纳音相克化解", "纳音相克" in ch3)
check("五行相克化解", "相克" in ch3)
check("化解不做承诺", "化灾" not in ch3 and "改运" not in ch3 and "必" not in ch3)

# 6. 契合详情直出（无 LLM）：分数明细可对照
ch4 = rep5["chapters"][3]["content"]
check("契合详情含明细", "40" in ch4 and "25" in ch4 and "35" in ch4 and "情投意合" in ch4)
check("契合详情含双引擎", "合婚引擎" in ch4 and "合盘引擎" in ch4)

# 7. 全文不泄隐私（无出生地/姓名/时辰）
check("全文脱敏", "上海" not in rep5["full_text"] and "北京" not in rep5["full_text"])

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_yuan_report.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.yuan_report'`

- [ ] **Step 3: 实现管线**

创建 `src/engines/yuan_report.py`:
```python
"""深度合盘报告管线（付费后生成）：前世今生 / 相处模式 / 矛盾点与化解 / 契合详情。

管线：
- 前世今生：纳音+日柱+天乙贵人 → 古籍 RAG（category='hehun'）→ 引用书名原文 → LLM 叙事（300-500 字）；
- 相处模式：合婚引擎三维明细 + 合盘引擎特征 → 体感描述（规则 + 特征口诀）；
- 矛盾点与化解：六冲/六害/相刑/纳音相克 规则逐条提取 → 每条配一条化解建议；
- 契合详情：双引擎分数明细直出（无 LLM），可对照复核。
质量红线：不出现「注定/必离婚/必分手/必成/化灾/改运/斩桃花」类绝对断言与承诺。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TRUST_STATEMENT = "签文只作心意，不作断言 · 明灯拟"

_ABSOLUTE_TOKENS = ("注定", "必离婚", "必分手", "必成", "一定", "保证", "化灾", "改运", "斩桃花")

_RESOLVE_ADVICE = {
    "六冲": "六冲主聚少离多，宜把见面变成固定习惯，约定好的日子不轻易改，冲意自缓。",
    "六害": "六害多因小事生隙，宜大事说开、小事翻篇，不翻旧账、不积怨气。",
    "相刑": "相刑主言语摩擦，宜约定争吵后冷处理半小时，气头不过夜。",
    "纳音相克": "纳音相克主气场节奏不同，宜各留个人空间，再以共同爱好作缓冲。",
    "相克": "五行相克主节奏快慢不一，宜把差异当互补，先听后说、慢半拍回应。",
}

_GENERIC_RESOLVE = "命盘未见明显冲害，日常多沟通、多见面，感情自然稳步向前。"

_STYLE_MAP = [
    ("六合", "有六合之缘，天然亲近，多制造共同回忆即可保持热度。"),
    ("三合", "有三合之势，彼此默契度高，相处以自然为主，不必刻意。"),
    ("夫妻宫六合", "夫妻宫六合，感情根基深厚，遇事多商量即稳。"),
    ("双天乙贵人", "互为贵人，一方低谷时另一方总在关键处拉一把。"),
    ("五行互补", "五行互补，一方的强项恰好补足另一方的短板。"),
    ("六冲", "相处宜固定节奏，把见面变成习惯。"),
    ("六害", "小摩擦较多，宜大事说开、小事翻篇。"),
    ("相刑", "言语易起摩擦，约定冷处理时限会很有用。"),
    ("纳音相克", "气场节奏不同，各留空间反而更长久。"),
    ("相克", "节奏不一，把差异当互补、先听后说。"),
]


def _validate_text(text: str) -> bool:
    return not any(k in (text or "") for k in _ABSOLUTE_TOKENS)


def _retrieve_classics(retriever, bazi1, bazi2) -> list:
    """古籍 RAG 检索：纳音+日柱+天乙贵人 主题。返回 [{book, text}]（书名去重，最多 3 条）。"""
    if retriever is None:
        return []
    day1 = "".join(bazi1.bazi[2]) if len(bazi1.bazi) > 2 else ""
    day2 = "".join(bazi2.bazi[2]) if len(bazi2.bazi) > 2 else ""
    n1 = (bazi1.nayin or [""] * 4)[2] if getattr(bazi1, "nayin", None) else ""
    n2 = (bazi2.nayin or [""] * 4)[2] if getattr(bazi2, "nayin", None) else ""
    query = f"{n1} {n2} {day1} {day2} 姻缘 贵人 前世"
    try:
        hits = retriever.search(query, category="hehun", top_k=10)
    except Exception as e:
        logger.warning("合盘古籍检索失败: %s", e)
        return []
    refs, seen = [], set()
    for hit in hits or []:
        book = (getattr(hit, "source", "") or "").strip()
        text = (getattr(hit, "text", "") or "").strip().replace("\n", "")
        if not book or not text or book in seen:
            continue
        seen.add(book)
        refs.append({"book": book, "text": text})
        if len(refs) >= 3:
            break
    return refs


def _build_qianshi(union: dict, bazi1, bazi2, refs: list, llm) -> str:
    """前世今生：古籍原典引用 + LLM 叙事；无 LLM/越红线 → 规则叙事（引用原句）。"""
    day1 = "".join(bazi1.bazi[2]) if len(bazi1.bazi) > 2 else ""
    day2 = "".join(bazi2.bazi[2]) if len(bazi2.bazi) > 2 else ""
    quotes = "；".join(f"《{r['book']}》『{r['text'][:40]}』" for r in refs[:2])
    fallback = (
        f"你们的日柱{day1}与{day2}各自带着独立的命运轨迹，而纳音与贵人星把两条线牵到了一处。"
        + (f"古籍原典可作印证：{quotes}。" if quotes else "")
        + "缘分的来处不必深究，重要的是它把你们带到了彼此面前——这份相遇，本身就是一段前缘的续写。")
    if llm is None or not getattr(llm, "api_key", ""):
        return fallback
    try:
        r = llm._call_deepseek_model(
            "你是命理叙事师。请用 300-500 字写一段'前世今生'的叙事体姻缘解读，"
            f"必须引用以下古籍原典金句（书名+原文）：{quotes or '无可用引文，可略过'}。"
            f"背景：日柱{day1}与{day2}。要求：温暖不玄幻，不出现'注定/必离婚/必分手/化灾改运'等断言，"
            "不涉及姓名与具体事件。",
            llm.model, max_tokens=700, timeout=30)
        text = (r.response or "").strip()
        return text if _validate_text(text) else fallback
    except Exception as e:
        logger.warning("前世今生叙事失败(规则兜底): %s", e)
        return fallback


def _build_xiangchu(union: dict) -> str:
    """相处模式：三维明细 + 特征口诀（规则，无 LLM 依赖）。"""
    d = union["dimensions"]
    parts = [
        f"你们的相处，先从五行讲起：互补得分 {d['wuxing']['score']}/{d['wuxing']['max']}，"
        f"生肖关系{d['shengxiao']['relation']}，日柱关系{d['rizhu']['relation']}。",
    ]
    matched = [desc for kw, desc in _STYLE_MAP if any(kw in f for f in union["features"])]
    if matched:
        parts.append("相处要诀：" + "".join(dict.fromkeys(matched))[:200])
    else:
        parts.append("相处要诀：顺其自然，多见面、多分享日常。")
    return "".join(parts)


def _build_maodun(union: dict) -> str:
    """矛盾点与化解：规则提取负特征，逐条配化解建议。"""
    items = []
    for f in union["features"]:
        for kw, advice in _RESOLVE_ADVICE.items():
            if kw in f:
                items.append(f"· {f}：{advice}")
                break
    if not items:
        return _GENERIC_RESOLVE
    return "以下矛盾点可逐一化解：\n" + "\n".join(items)


def _build_qihe(union: dict) -> str:
    """契合详情：双引擎分数明细直出（无 LLM，可对照复核）。"""
    raw = union.get("raw", {})
    hw = raw.get("hehun_wuxing", {}) or {}
    hs = raw.get("hehun_shengxiao", {}) or {}
    hr = raw.get("hehun_rizhu", {}) or {}
    cp = raw.get("compat", {}) or {}
    return "\n".join([
        f"总分：{union['score']} 分（合婚引擎 ×0.6 + 合盘引擎 ×0.4），等级「{union['levelLabel']}」。",
        f"一、合婚引擎：五行互补 {union['dimensions']['wuxing']['score']}/40（{hw.get('complement_desc', '—')}）；"
        f"生肖 {union['dimensions']['shengxiao']['score']}/25（{hs.get('description', '—')}）；"
        f"日柱 {union['dimensions']['rizhu']['score']}/35（{hr.get('description', '—')}）。",
        f"二、合盘引擎：日主五行关系 {cp.get('wuxing_relation', '—')}（基准 {cp.get('base_score', '—')} 分）；"
        f"五行互补加成 +{cp.get('complement_bonus', 0)}；双天乙贵人加成 +{cp.get('shensha_bonus', 0)}；"
        f"夫妻宫六合加成 +{cp.get('combo_bonus', 0)}；综合 {cp.get('final_score', '—')} 分。",
        "以上明细均来自双方真实排盘的双引擎计算，可对照复核。",
    ])


def build_report(union: dict, bazi1, bazi2, retriever=None, llm=None) -> dict:
    """四章报告 + 引用 + 全文。每章独立兜底，单章失败不阻断其余章节。"""
    refs = _retrieve_classics(retriever, bazi1, bazi2)
    chapters = [
        {"title": "前世今生", "content": _build_qianshi(union, bazi1, bazi2, refs, llm)},
        {"title": "相处模式", "content": _build_xiangchu(union)},
        {"title": "矛盾点与化解", "content": _build_maodun(union)},
        {"title": "契合详情", "content": _build_qihe(union)},
    ]
    full_text = "\n\n".join(f"【{c['title']}】\n{c['content']}" for c in chapters)
    full_text += f"\n\n{TRUST_STATEMENT}"
    return {"chapters": chapters, "citations": refs, "full_text": full_text}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_yuan_report.py`
Expected: `ALL PASS (13)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/yuan_report.py scripts/test_yuan_report.py
git commit -m "feat(union): 深度报告管线(前世今生古籍RAG/相处模式/矛盾点化解/契合详情,红线校验)"
```

---

### Task 5: 深度报告 API（paid=true + deep_report 支付校验 + 归档）

**Files:**
- Modify: `src/api/union.py`（付费分支：`_require_paid` + `_archive_report` + 端点接入）
- Test: `scripts/test_union_api.py`（追加用例）

**Interfaces:**
- Consumes: Task 4 `build_report`、`src.api.pay` 的 `_member_dao.get_user_purchase(uid, "deep_report")`（复用 deep_report 商品，不加新商品）、`src.config.is_experience_mode`、`_dao.save_consultation`（consultations 归档，供报告页回看）
- Produces: paid=true → `{score, levelLabel, report: {chapters, citations, full_text}, reportId, purchased}`；未购买 403；防绕过：免费档绝不返回报告正文

- [ ] **Step 1: 追加失败测试**

在 `scripts/test_union_api.py` 末尾追加（文件头补 `import json`）:
```python
# ── Task 5: 付费档（deep_report 校验 + 归档脱敏）──

class FakeMemberDAO:
    def __init__(self, purchased):
        self.purchased = purchased
    def get_user_purchase(self, user_id, product_id):
        return {} if self.purchased else None

union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=FakeMemberDAO(False), dao=fake_dao)
PAID_BODY = {
    "person_a": {"year": 1990, "month": 5, "day": 20, "hour": 8, "city": "北京", "gender": "男"},
    "person_b": {"year": 1992, "month": 8, "day": 15, "hour": 14, "city": "上海", "gender": "女"},
    "relation": "恋人", "paid": True,
}

# 5. 未购买 → 403（防绕过）
r = client.post("/api/union", headers=h, json=PAID_BODY)
check("未购买 403", r.status_code == 403)
check("付费墙文案指向 deep_report", "deep_report" in r.json()["detail"])

# 6. 已购买 → 四章报告 + 归档
fake_dao.calls.clear()
union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=FakeMemberDAO(True), dao=fake_dao)
r = client.post("/api/union", headers=h, json=PAID_BODY)
d = r.json()
check("付费档 200 四章", r.status_code == 200 and d["purchased"] is True
      and [c["title"] for c in d["report"]["chapters"]] == ["前世今生", "相处模式", "矛盾点与化解", "契合详情"])
check("归档产生 reportId", d["reportId"] == "1")
check("归档 intent=hehun", fake_dao.calls and fake_dao.calls[0]["intent"] == "hehun")
check("归档 question 脱敏", "1990" not in fake_dao.calls[0]["question"] and "5月20日" not in fake_dao.calls[0]["question"])
check("归档 chart 脱敏", "1990" not in json.dumps(fake_dao.calls[0]["chart"]) and "1992" not in json.dumps(fake_dao.calls[0]["chart"]))
check("归档 analysis 无出生地", "上海" not in fake_dao.calls[0]["analysis"])

# 7. 免费档绝不返回报告正文（免费调用后再断言零归档）
r = client.post("/api/union", headers=h, json={k: v for k, v in PAID_BODY.items() if k != "paid"})
check("免费档无报告字段", "report" not in r.json() and "reportId" not in r.json())
print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_union_api.py`
Expected: 新增 6 项 FAIL（paid=true 未校验/未返回报告，403 不存在）

- [ ] **Step 3: 实现付费分支**

在 `src/api/union.py` 增加:
```python
from src.config import is_experience_mode


def _require_paid(uid: str):
    """复用 deep_report 商品：已购 or 体验模式 → 放行；否则 403（防绕过）。"""
    if is_experience_mode():
        return
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="支付服务未就绪")
    if _member_dao.get_user_purchase(uid, "deep_report") is None:
        raise HTTPException(status_code=403, detail="请先解锁深度合盘报告（¥19.9，deep_report 通道）")


def _archive_report(uid: str, union: dict, full_text: str) -> str:
    """归档（隐私红线：只写脱敏摘要与报告正文，绝不写双方生辰）。返回 report_id。"""
    question = f"双人合盘：契合{union['score']}分（{union['levelLabel']}）" + (
        f"·{union['relation']}" if union.get("relation") else "")
    chart = {"type": "yuan_union", "score": union["score"],
             "level": union["levelLabel"], "relation": union.get("relation", "")}
    rid = _dao.save_consultation(uid, question, chart_result=chart,
                                 analysis=full_text, intent="hehun")
    return str(rid)
```

在 `src/api/union.py` 端点内、`run_union` 之后追加付费分支:
```python
    # ── 付费档：深度报告四章（支付校验 → 生成 → 归档）──
    if req.paid:
        _require_paid(uid)
        from src.engines.yuan_report import build_report
        report = build_report(union, r1, r2, retriever=_retriever, llm=_llm_ref)
        report_id = ""
        if _dao is not None:
            report_id = _archive_report(uid, union, report["full_text"])
        return {
            "score": union["score"], "levelLabel": union["levelLabel"],
            "report": report, "reportId": report_id, "purchased": True,
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_union_api.py`
Expected: `ALL PASS (19)`

- [ ] **Step 5: Commit**

```bash
git add src/api/union.py scripts/test_union_api.py
git commit -m "feat(union): 深度报告付费分支(deep_report 校验防绕过+consultations 脱敏归档)"
```

---

### Task 6: 缘笺分享图 drawYuanCard（双色印章 + 脱敏数据）

**Files:**
- Modify: `miniprogram/utils/shareCard.js`、`miniprogram_simple/utils/shareCard.js`、`miniprogram_fusion/utils/shareCard.js`（三版同步追加）

**Interfaces:**
- Consumes: `/api/union` 返回的 `yuan_card`（全部为服务端脱敏数据：`birthA/birthB/score/levelLabel/quoteParts/sealChar`）、复用 drawInkCard 墨韵资产（宣纸底 #F5EFE1 / 墨字 #3A2C1E / 朱砂 #A93A2C / 楷体）与 `drawQRPlaceholder`
- Produces: `drawYuanCard(data, canvas, callback)`（750x1200，2x PNG 导出）；`module.exports` 追加 `drawYuanCard`

**实现要点（追加在 drawInkCard 之后）**:
```js
/**
 * 绘制双人缘笺分享卡（墨韵：宣纸底 · 墨字 · 双色印章 · 双人生辰）
 * @param {Object} data - { birthA, birthB, score, levelLabel, quoteParts, sealChar }（服务端已脱敏）
 * @param {Object} canvas - canvas 2d 节点
 * @param {Function} callback - (tempFilePath)
 * 隐私：data 全部为服务端脱敏数据（无时辰/出生地/姓名）
 */
function drawYuanCard(data, canvas, callback) {
  const ctx = canvas.getContext('2d');
  const W = 750, H = 1200;
  const INK = '#3A2C1E', PAPER = '#F5EFE1', CINNABAR = '#A93A2C', DAIQING = '#3E5C4E';
  const MUTED = '#6C5B45', LIGHT = '#9A8B71', FAINT = '#BBAE92';

  // 1. 宣纸底 + 细框 + 顶线（复用 drawInkCard 底稿）
  ctx.fillStyle = PAPER; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = 'rgba(58,44,30,.45)'; ctx.lineWidth = 4;
  ctx.strokeRect(28, 28, W - 56, H - 56);
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(28, 54); ctx.lineTo(W - 28, 54); ctx.stroke();
  ctx.textAlign = 'center';

  // 2. 标题
  ctx.fillStyle = INK;
  ctx.font = '600 34px "PingFang SC", sans-serif';
  ctx.fillText('双人合盘 · 缘分契合', W / 2, 120);

  // 3. 双人生辰（脱敏）竖排两行
  ctx.fillStyle = MUTED;
  ctx.font = '28px "PingFang SC", sans-serif';
  ctx.fillText(data.birthA || '', W / 2, 185);
  ctx.fillText(data.birthB || '', W / 2, 235);

  // 4. 契合分大字 + 等级
  ctx.fillStyle = INK;
  ctx.font = 'bold 96px "PingFang SC", sans-serif';
  ctx.fillText(String(data.score || 0), W / 2, 400);
  ctx.fillStyle = CINNABAR;
  ctx.font = '26px "PingFang SC", sans-serif';
  ctx.fillText('分', W / 2 + 58, 392);
  ctx.fillStyle = CINNABAR;
  ctx.font = '600 32px "PingFang SC", sans-serif';
  ctx.fillText(data.levelLabel || '', W / 2, 452);

  // 5. 菱形分隔
  ctx.save();
  ctx.translate(W / 2, 502);
  ctx.rotate(Math.PI / 4);
  ctx.fillStyle = CINNABAR;
  ctx.fillRect(-7, -7, 14, 14);
  ctx.restore();

  // 6. 缘语两行（楷体，可晒体；主句一行 + 后缀/悬念一行，自动换行）
  const qp = data.quoteParts || {};
  const line1 = qp.main || '';
  const line2 = [qp.suffix, qp.cliffhanger].filter(Boolean).join('，');
  ctx.fillStyle = INK;
  ctx.font = '40px "Kaiti SC", "STKaiti", serif';
  let wrapY = wrapText(ctx, line1, 80, 580, W - 160, 52);
  if (line2) wrapY = wrapText(ctx, line2, 80, wrapY + 22, W - 160, 52);

  // 7. 双色印章（右下）：朱砂「缘」印 + 黛青生肖印（如「午马」，视觉隐喻双人）
  const sy = 880, ss = 96;
  roundRect(ctx, 470, sy, ss, ss, 8);
  ctx.fillStyle = CINNABAR; ctx.fill();
  ctx.fillStyle = '#FBF6E8';
  ctx.font = '56px "Kaiti SC", serif';
  ctx.fillText('缘', 470 + ss / 2, sy + 70);
  roundRect(ctx, 590, sy, ss, ss, 8);
  ctx.fillStyle = DAIQING; ctx.fill();
  ctx.fillStyle = '#FBF6E8';
  ctx.font = '44px "Kaiti SC", serif';
  ctx.fillText(data.sealChar || '缘', 590 + ss / 2, sy + 68);

  // 8. 品牌落款（左下）+ 小程序码占位
  ctx.textAlign = 'left';
  ctx.fillStyle = INK;
  ctx.font = '600 30px "PingFang SC", sans-serif';
  ctx.fillText('易理明灯', 70, 940);
  ctx.fillStyle = LIGHT;
  ctx.font = '22px "PingFang SC", sans-serif';
  ctx.fillText('三秒内，为你掌灯', 70, 985);
  drawQRPlaceholder(ctx, 560, 1020, 100);

  // 9. 底部小字
  ctx.textAlign = 'center';
  ctx.fillStyle = FAINT;
  ctx.font = '20px "PingFang SC", sans-serif';
  ctx.fillText('签文只作心意，不作断言', W / 2, H - 40);

  // 10. 导出 2x PNG（同 drawInkCard）
  wx.canvasToTempFilePath({
    canvas, width: W, height: H, destWidth: W * 2, destHeight: H * 2,
    fileType: 'png', quality: 1,
    success: (res) => { if (callback) callback(res.tempFilePath); },
    fail: (err) => { console.error('[ShareCard] yuan card error:', err); if (callback) callback(null); },
  });
}
```
`module.exports` 追加 `drawYuanCard`。

**验证**:
- [ ] 三版 auto-preview 编译全绿
- [ ] IDE 自动化：合盘页生成缘笺 → 预览图 750x1200 完整渲染（双生辰/契合分/缘语/朱砂缘印/黛青生肖印/品牌/底部小字）→ 保存相册成功；截图+vision 复核（无重叠/破图/色块）

**Commit**:
```bash
git add miniprogram/utils/shareCard.js miniprogram_simple/utils/shareCard.js miniprogram_fusion/utils/shareCard.js
git commit -m "feat(union): 缘笺分享图 drawYuanCard 三版(双色印章+脱敏生辰+契合分)"
```

---

### Task 7: 前端 · 合盘页改版（hehun 升级为双人合盘 + love 并入 + 本机缓存）

**Files:**
- Modify: `miniprogram/pages/hehun/hehun.{js,json,wxml,wxss}`、`miniprogram/pages/love/love.{js,wxml}`、`miniprogram/utils/api.js`（三版同步；love 页仅 js 改跳转）
- Modify: `miniprogram/pages/hehun/hehun.json`（标题改「双人合盘」，路由 `/pages/hehun/hehun` 保留）

**Interfaces:**
- Consumes: `POST /api/union`（api.js 新增 `union(payload)`）、`GET /api/persons`（档案直选，已有）、`payment.purchase('deep_report')`（已有）、`drawYuanCard`（Task 6）、`saveCardToAlbum/shareCard`（已有）、`wx.setStorageSync('yuan_ta_birth')`（本机缓存）
- Produces: 合盘页：双表单（年月日必填/时辰出生地选填）+ 档案直选 + 关系标签 → 免费结果（契合分/等级/三维得分条/缘语+悬念半句）→ [生成墨韵缘笺]（脱敏确认弹窗）→ [解锁深度报告 ¥19.9]（四章展示+归档提示）；love 页并入跳转；TA 生辰只存本机

**实现要点**:
- **api.js** 新增:
```js
function union(data) {
  return request('/api/union', {
    method: 'POST',
    header: { 'Content-Type': 'application/json' },
    data,
  });
}
```
（module.exports 追加 union；请求头参考现有 hehun/love 封装，走同一鉴权头）

- **hehun.wxml 结构**: 标题「双人合盘 · 缘分契合」+ 副文案「输入两人生辰，看看你们合不合」；两张人卡（我方/TA）：表单上方「从档案选择」按钮（wx.showActionSheet 列 persons 姓名 → 点选填充年月日/时辰/性别），年/月/日 picker（必填），时辰 picker（选填，标注「选填，填了更准」；未填 → 默认子时 + 结果页标注「时辰未填，仅供参考」），出生地 picker（选填）；关系标签 5 chips 单选（恋人/暧昧/夫妻/朋友/暗恋）；提交按钮 → loading「推演两人命盘中…」→ 结果区
- **结果区（免费）**: 契合分大数字 + 等级称谓；三维得分条「五行 x/40 · 生肖 x/25 · 日柱 x/35」（数值可见、明细锁定：点击 toast「明细见深度报告·契合详情章」）；缘语（main+suffix）+"展开"半句（点击 → 付费墙弹层）；按钮行：`[生成墨韵缘笺(免费)]` `[解锁深度报告 ¥19.9]` `[换一个人再测]` `[重新输入]`
- **缘笺流程**: 点「生成墨韵缘笺」→ wx.showModal「图片将包含脱敏后的生日信息（年/月/日+生肖+日柱，不含时辰与出生地），确认生成？」→ drawYuanCard(yuan_card 数据, canvas) → 预览弹层（image + 保存相册 saveCardToAlbum / 分享 shareCard）
- **付费流程**: 点「解锁深度报告」→ `payment.purchase('deep_report')` → success → `api.union({...同一份生辰, paid: true})` → 四章墨韵弹层（参照 reports 页弹层样式）→ toast「已存入报告页」+ 按钮跳 `/pages/reports/reports`（报告回看走现有 /api/reports 归档）
- **本机缓存（隐私）**: 提交成功后 `wx.setStorageSync('yuan_ta_birth', {year, month, day, hour, city, gender, relation, ts})`（标记「他/她」）；onLoad 若有缓存则回填 TA 表单（带「上次记录」标注）；该 key 永不写入 persons 云端接口；persons 页「他/她」类条目保持现状不动
- **love 页并入**: love.js onLoad 改为 `wx.reLaunch({ url: '/pages/hehun/hehun' })`（保留页面文件与路由，旧 URL 不失效）；love 页 ¥9.9 摘要档随并入下线（后端 /api/love/compatibility 保留兼容旧客户端）
- **onLoad 回填我方**: `api.getPersons()` → default person → 自动填充我方表单（对话触发进入时「带已填的我方生辰」即由此实现，不在 URL 传生辰，避免隐私外泄）

**验证**:
- [ ] 三版编译全绿
- [ ] IDE 自动化：输入 → 免费结果 → 缘笺生成（确认弹窗文案正确）→ 预览保存；解锁深度报告（mock 支付）→ 四章展示 + 报告页可见归档；love 页进入自动跳合盘页；TA 表单提交后重进页面回填（storage 存在）；console 无报错
- [ ] 截图+vision 复核：输入页/结果页/缘笺预览/付费墙/四章弹层（墨韵视觉无破图）

**Commit**:
```bash
git add miniprogram/pages/hehun miniprogram/pages/love miniprogram/utils/api.js miniprogram_simple/pages/hehun miniprogram_simple/pages/love miniprogram_simple/utils/api.js miniprogram_fusion/pages/hehun miniprogram_fusion/pages/love miniprogram_fusion/utils/api.js
git commit -m "feat(union): 合盘页改版三版(双表单+档案直选+关系标签+免费结果+缘笺+付费墙),love并入,TA生辰仅本机缓存"
```

---

### Task 8: 前端 · 入口（今日页入口卡 + 对话触发）

**Files:**
- Modify: `miniprogram/pages/today/today.{js,wxml,wxss}`（三版：入口卡）
- Modify: `src/engines/intent_classifier.py`（hehun 意图扩展触发词）
- Modify: `src/bot/handler.py`（`_handle_hehun` 信息不全时返回引导卡片）
- Modify: `miniprogram/pages/chat/chat.{js,wxml,wxss}`（三版：识别回复中的 `/pages/` 路径 → 渲染跳转按钮）

**Interfaces:**
- Consumes: hehun 意图（现有）、chat 回复渲染管线
- Produces: today 页顶部第二入口卡「双人合盘 · 缘分契合」；聊天中「我和TA合不合/合盘/我们配不配」→ hehun 意图 → 引导文案+跳转按钮 → `/pages/hehun/hehun`（onLoad 自动带我方 default person）

**实现要点**:
- **today.wxml**: 晨笺卡下方加入口卡（墨韵视觉：朱砂「缘」印章 + 标题「双人合盘 · 缘分契合」+ 副文案「输入两人生辰，看看你们合不合」+ brush-arrow）→ `onYuanEntry` → `wx.navigateTo('/pages/hehun/hehun')`；today.js 加 `onYuanEntry` 与数据字段；today.wxss 三版同步皮肤
- **intent_classifier.py**: `INTENT_CLASSIFY_PROMPT` 中 hehun 行扩为 `"hehun" — 合婚, relationship compatibility, 配对, 婚姻匹配, 双人合盘, 合盘, 我和TA合不合, 看看我们配不配, 缘分契合`；分类规则补一条：消息同时指向两人（我/我们 + 他/她/TA）且语义为「合/配/缘分」→ hehun
- **handler.py `_handle_hehun`**: `not info_a or not info_b` 分支的返回文案改为引导卡片：
```
想看看你们合不合？给我双方生辰即可直接测算；
或进入「双人合盘」页，从档案一键选人，还能生成墨韵缘笺：
/pages/hehun/hehun
```
- **chat.js**: 渲染回复时检测行内 `/pages/[a-z_]+/[a-z_]+` 路径 → 在气泡末尾渲染「进入双人合盘 →」按钮（tap → wx.navigateTo）；无路径则保持纯文本渲染（不破坏现有气泡）

**验证**:
- [ ] 三版编译全绿
- [ ] IDE 自动化：today 页点入口卡 → 合盘页；聊天输入「我和TA合不合」→ 回复含引导卡 + 按钮 → 点击进合盘页且我方表单已回填 default person；console 无报错
- [ ] 截图+vision 复核（today 入口卡/聊天引导卡）

**Commit**:
```bash
git add miniprogram/pages/today miniprogram/pages/chat miniprogram_simple/pages/today miniprogram_simple/pages/chat miniprogram_fusion/pages/today miniprogram_fusion/pages/chat src/engines/intent_classifier.py src/bot/handler.py
git commit -m "feat(union): 入口三处(今日页入口卡+对话hehun意图扩展引导卡片+导航位保留)"
```

---

### Task 9: 隐私协议更新 + 集成验证 + 上线准备

**Files:**
- Modify: `miniprogram/pages/privacy/privacy.wxml`、`miniprogram/pages/agreement/agreement.wxml`（三版：补充对方信息仅本机保存条款）

**实现要点**:
- 隐私协议新增条款：「双人合盘功能中，对方（他/她）的出生信息仅保存在您的设备本地（仅用于本次合盘计算），不会上传、不用于任何其他用途；分享图默认脱敏，不包含时辰、出生地与姓名」
- 用户协议同步一句：深度报告内容仅作心意参考，不作断言（对齐 TRUST_STATEMENT）

- [ ] 全量回归：
```bash
cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_yuan_quote.py && .venv/bin/python3 scripts/test_union_engine.py && .venv/bin/python3 scripts/test_union_api.py && .venv/bin/python3 scripts/test_yuan_report.py && .venv/bin/python3 scripts/test_p2.py
```
全绿（`ALL PASS` 且 test_p2 无 FAIL）
- [ ] 三版 auto-preview 编译全绿 + 关键屏截图 vision 复核（today 入口卡/合盘输入页/免费结果页/缘笺预览/付费墙/四章弹层/聊天引导卡）
- [ ] 服务重启（8767）+ `curl /api/health` + `/api/union` 免费档与付费档（mock 支付）实测
- [ ] 交付清单：无新增 env；深度报告沿用 `deep_report` 商品（19.9）；love ¥9.9 摘要档下线说明（旧客户端仍兼容 `/api/love/compatibility` 与 `/pages/love/love`）；隐私协议发布走现有小程序审核流程
- [ ] Commit 收尾：
```bash
git add miniprogram/pages/privacy miniprogram/pages/agreement miniprogram_simple/pages/privacy miniprogram_simple/pages/agreement miniprogram_fusion/pages/privacy miniprogram_fusion/pages/agreement
git commit -m "docs(union): 隐私协议/用户协议补充对方信息仅本机保存条款(双人合盘)"
```
