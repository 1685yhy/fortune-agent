# L1 排盘引擎完整化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 排盘引擎三项补全——起运分解 100% 对齐问真、神煞补全问真独有 28 种、干支关系分析——使排盘结果与问真同级完整。

**Architecture:** 基于 src/engines/bazi.py（BaziEngine，已对齐问真全字段）扩展：①起运节气时刻改用问真提取的 1799-2100 节气表（data/jieqi_qz.json）；②shensha.py 补 28 种问真独有神煞（规则计算，对齐问真口径）；③新增 src/engines/ganzhi_rel.py 干支关系模块（伏吟/反吟/盖头/截脚/争合/妒合）。每项带对齐测试。

**Tech Stack:** Python 3.12、pytest、lunar-python（仅四柱/非起运路径）、问真提取资产（/tmp/qz_extracted/、data/calibrate_qz_250.json）

## Global Constraints

- 红线：对话向量库只增不改；本计划不触碰任何 RAG/向量库代码
- 问真 API 限速：任何校准脚本请求间隔 ≥1s、每 50 例暂停 30s、遇 429/403/验证码立即停止
- 回归红线：tests/test_bazi_qz_full.py（200 案例）与 tests/test_bazi_jiaoyun.py（23 例）必须全绿
- 生产 DB 不触碰（本计划无 DB 变更）
- 工作目录：/mnt/e/fortune-agent-deploy（git main），venv：/home/a/fortune-run/.venv
- 提交信息格式：feat(bazi)/fix(bazi)/feat(shensha) 前缀，中文说明

---

### Task 1: 起运分解 100%（问真节气表）

**Files:**
- Create: `data/jieqi_qz.json`（问真节气表提取自 /tmp/qz_extracted/，1799-2100 年 24 节气精确时刻）
- Modify: `src/engines/bazi.py`（`_calc_qiyun_start_age` 及起运分解路径：节气时刻来源换为问真表）
- Modify: `scripts/calibrate_qz_250.py`（校准流水线复用）
- Test: `tests/test_bazi_qiyun_full.py`

**Interfaces:**
- Consumes: `data/calibrate_qz_250.json`（250 案例含问真 qiyunarr 期望值）；问真节气表数据（/tmp/qz_extracted/ 内 eOvQ 模块或 data/ 下节气文件）
- Produces: `bazi.py` 内 `_jieqi_time(year, jie_name) -> datetime`（问真表查询，越界回退 lunar）；起运分解输出（年/月/日/时/分）与问真 qiyunarr 对齐

- [ ] **Step 1: 提取并固化问真节气表**

从 /tmp/qz_extracted/ 找到节气表数据（ALGORITHMS.md 所述 eOvQ 1.6MB 模块或 data/ 目录），解析为 JSON 写入 `data/jieqi_qz.json`（格式：`{"1799": {"小寒": "1799-01-05 15:42:00", ...24节气}, ...2100}`），并写提取脚本 `scripts/extract_jieqi_qz.py` 固化流程（可复跑）。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_bazi_qiyun_full.py
import json, sys
sys.path.insert(0, '.')
from src.engines.bazi import BaziEngine

def test_qiyun_decompose_matches_qz():
    cases = json.load(open('data/calibrate_qz_250.json'))
    mism = 0
    for c in cases:
        r = BaziEngine().calculate(c['year'], c['month'], c['day'], c['hour'], c['minute'], '', c['gender'])
        got = r.qiyun_decompose  # (年,月,日,时,分)
        exp = tuple(c['qiyunarr'][:5])
        if got != exp:
            mism += 1
    assert mism / len(cases) <= 0.02, f"起运分解不一致率 {mism}/{len(cases)}"
```

- [ ] **Step 3: 运行验证失败**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qiyun_full.py -v`
Expected: FAIL（当前不一致率 ~22%，断言 2% 不满足）；同时确认 `qiyunarr` 字段名与校准数据一致（若字段名不同，修正测试读取方式）

- [ ] **Step 4: 实现问真节气表查询 + 起运分解改用问真节气**

在 `src/engines/bazi.py` 新增：

```python
_JIEQI_CACHE = {}
def _jieqi_time(year: int, jie_name: str):
    """问真节气表查询（1799-2100）；越界或缺失回退 lunar-python。"""
    import json, datetime
    if not _JIEQI_CACHE:
        _JIEQI_CACHE.update(json.load(open('data/jieqi_qz.json', encoding='utf-8')))
    entry = _JIEQI_CACHE.get(str(year), {}).get(jie_name)
    if entry:
        return datetime.datetime.strptime(entry, '%Y-%m-%d %H:%M:%S')
    # 回退 lunar
    from lunar_python import Lunar
    return Lunar.fromYmd(year, 1, 1).getJieQiTable()[jie_name].getSolar().toYmdHms()
```

将 `_calc_qiyun_start_age` 及起运分解中"到最近节气时刻"的计算改为优先 `_jieqi_time`（顺排取下节、逆排取上节，节名用 12 节：立春/惊蛰/清明/立夏/芒种/小暑/立秋/白露/寒露/立冬/大雪/小寒），保留原 lunar 路径为回退。

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qiyun_full.py -v`
Expected: PASS（不一致率 ≤2%；剩余差异应为表覆盖边界）

- [ ] **Step 6: 回归全绿**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qz_full.py tests/test_bazi_jiaoyun.py tests/test_bazi_qiyun_full.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add data/jieqi_qz.json scripts/extract_jieqi_qz.py src/engines/bazi.py tests/test_bazi_qiyun_full.py
git commit -m "fix(bazi): 起运分解用问真节气表——分解对齐100%（P0插队项）"
```

---

### Task 2: 神煞补全（问真独有 28 种）

**Files:**
- Modify: `src/engines/shensha.py`（新增 28 种规则函数 + 并入 shensha_of 输出）
- Modify: `src/engines/bazi.py`（BaziResult.shensha_detail 兼容）
- Test: `tests/test_shensha_qz_full.py`

**Interfaces:**
- Consumes: 校准子代理报告的 28 种清单（德秀贵人/童子煞/月德合/月德贵人/天德合/天德贵人/飞刃/红艳煞/血刃/流霞/天医/十灵日/阴差阳错/十恶大败/八专日/九丑日/孤鸾煞/六秀日/魁罡日/金神/天罗地网/四废日/天转日/天赦日/地转日/拱禄/三奇贵人等）；问真计算规则从校准数据（data/calibrate_qz_250.json 含问真 szshensha 字段）反推
- Produces: `shensha_of()` 输出扩展（新增神煞名），`ShenshaItem` 保持 {name, source, luck}

- [ ] **Step 1: 写失败测试（锚点案例）**

```python
# tests/test_shensha_qz_full.py
import sys; sys.path.insert(0, '.')
from src.engines.shensha import shensha_of

def test_qz_only_shensha_present():
    # 用校准数据中问真含"德秀贵人"等的案例断言
    names = {s.name for s in shensha_of('己卯', '乙丑', ['己','己','乙','壬'], ['卯','巳','丑','午'])}
    assert '德秀贵人' in names  # 若该案例问真确有德秀
```

从 data/calibrate_qz_250.json 中挑选 5 个含问真独有神煞的案例作为锚点（每案例断言其问真 szshensha 中独有项出现在我们输出中）。

- [ ] **Step 2: 运行验证失败**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_shensha_qz_full.py -v`
Expected: FAIL（德秀贵人等未实现）

- [ ] **Step 3: 实现 28 种神煞规则**

在 `src/engines/shensha.py` 按问真规则实现（参考校准数据反推 + 标准命理规则）：

```python
# 示例：德秀贵人（春木火/夏火土/秋金水/冬水土 四时取德秀——按问真口径从校准数据反推确认）
def _dexui(year_gan, day_gan, season):
    # 依校准数据反推的规则实现
    ...
# 各规则注册到 _CALC_RULES: Dict[str, Callable]
```

逐一实现：德秀贵人、童子煞、月德合、月德贵人、天德合、天德贵人、飞刃、红艳煞、血刃、流霞、天医、十灵日、阴差阳错、十恶大败、八专日、九丑日、孤鸾煞、六秀日、魁罡日、金神、天罗地网、四废日、天转日、天赦日、地转日、拱禄、三奇贵人（每种的判定输入：四柱干支/纳音/节气季节，按问真口径）。并入 `shensha_of` 输出（source='计算'，luck 按通用认知标注）。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_shensha_qz_full.py tests/test_shensha_qz.py -v`
Expected: 新测试 PASS + 原神煞测试不破坏

- [ ] **Step 5: 批量校准验证（250 案例神煞交集）**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 scripts/calibrate_qz_250.py --field shensha 2>&1 | tail -5`
Expected: 问真全部神煞名（含新增 28 种）在我们输出中的覆盖率 ≥95%（问真特有但规则未明的记录缺失清单）

- [ ] **Step 6: 回归 + Commit**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qz_full.py tests/test_shensha_qz.py tests/test_shensha_qz_full.py -v`
Expected: 全绿

```bash
git add src/engines/shensha.py src/engines/bazi.py tests/test_shensha_qz_full.py
git commit -m "feat(shensha): 神煞补全问真独有28种（德秀/月德/天德/红艳/魁罡/金神等，P0-2）"
```

---

### Task 3: 干支关系分析

**Files:**
- Create: `src/engines/ganzhi_rel.py`
- Modify: `src/engines/bazi.py`（BaziResult 集成 ganzhi_rel，含原局/大运/流年关系）
- Test: `tests/test_ganzhi_rel.py`

**Interfaces:**
- Consumes: 问真 newgetGZRelaction 接口语义（探索报告：输入 流年干支+大运干支+四柱 → 争合/妒合/伏吟/反吟/盖头/截脚等）；标准命理规则
- Produces: `analyze_relations(gan_a, gan_b, pillars) -> list[RelationItem]`；`RelationItem = {type, desc, pillars_implied}`；BaziResult.ganzhi_rel

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ganzhi_rel.py
import sys; sys.path.insert(0, '.')
from src.engines.ganzhi_rel import analyze_relations, RelationItem

def test_fuyin():
    rels = analyze_relations('乙丑', '乙丑', ['己卯','己巳','乙丑','壬午'])
    assert any(r.type == '伏吟' for r in rels)

def test_fanyin():
    rels = analyze_relations('乙丑', '己未', ['己卯','己巳','乙丑','壬午'])
    assert any(r.type == '反吟' for r in rels)  # 乙己天克+丑未地冲

def test_gaitou():
    rels = analyze_relations('甲子', '乙亥', ['己卯','己巳','乙丑','壬午'])
    assert any(r.type == '盖头' for r in rels)  # 天干克地支

def test_jiejiao():
    rels = analyze_relations('甲子', '甲辰', ['己卯','己巳','乙丑','壬午'])
    assert any(r.type == '截脚' for r in rels)  # 地支克天干
```

- [ ] **Step 2: 运行验证失败**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_ganzhi_rel.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现干支关系模块**

```python
# src/engines/ganzhi_rel.py
TIANGAN = '甲乙丙丁戊己庚辛壬癸'
DIZHI = '子丑寅卯辰巳午未申酉戌亥'
# 六冲对
CHONG = {'子':'午','午':'子','丑':'未','未':'丑','寅':'申','申':'寅',
         '卯':'酉','酉':'卯','辰':'戌','戌':'辰','巳':'亥','亥':'巳'}
# 天干五合
HE = {'甲':'己','己':'甲','乙':'庚','庚':'乙','丙':'辛','辛':'丙',
      '丁':'壬','壬':'丁','戊':'癸','癸':'戊'}

def analyze_relations(a, b, pillars):
    """a=流年/大运干支, b=被作用干支(原局/大运柱), pillars=四柱。返回关系列表。"""
    out = []
    ag, az = a[0], a[1]; bg, bz = b[0], b[1]
    if a == b: out.append(RelationItem('伏吟', f'{a}见{a}'))
    if TIANGAN.index(ag) % 2 == TIANGAN.index(bg) % 2 and CHONG.get(az) == bz:
        out.append(RelationItem('反吟', f'{ag}克{bg} + {az}冲{bz}'))
    # 盖头：天干克地支（本柱内）
    if _gan_ke_zhi(ag, az): out.append(RelationItem('盖头', f'{ag}克{az}'))
    if _zhi_ke_gan(bz, bg): out.append(RelationItem('截脚', f'{bz}克{bg}'))
    # 争合/妒合：多干合一干
    ...
    return out
```

实现完整规则（伏吟/反吟/盖头/截脚/争合/妒合/天克地冲/干支自刑等，含五合十干生克表），BaziResult 集成（原局四柱间关系 + 大运/流年与原局关系——输出简洁化：主关系列表）。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_ganzhi_rel.py -v`
Expected: PASS

- [ ] **Step 5: 问真接口锚点验证（可选，限速）**

若需锚点：用问真 newgetGZRelaction 接口 5 个案例对比（间隔 2s+），记录一致率；不一致的按问真口径修正规则。

- [ ] **Step 6: 回归 + Commit**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qz_full.py tests/test_ganzhi_rel.py -v`
Expected: 全绿

```bash
git add src/engines/ganzhi_rel.py src/engines/bazi.py tests/test_ganzhi_rel.py
git commit -m "feat(bazi): 干支关系分析（伏吟/反吟/盖头/截脚/争合/妒合，P0-3）"
```

---

### Task 4: L1 收尾验证

**Files:**
- Test: `tests/test_bazi_qz_full.py`（既有）

- [ ] **Step 1: 全量回归**

Run: `cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_bazi_qz_full.py tests/test_bazi_jiaoyun.py tests/test_bazi_qiyun_full.py tests/test_shensha_qz_full.py tests/test_ganzhi_rel.py tests/test_bazi_solar_time.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 同步运行副本 + 重启服务**

```bash
cp src/engines/bazi.py src/engines/shensha.py src/engines/ganzhi_rel.py data/jieqi_qz.json /home/a/fortune-run/src/engines/ /home/a/fortune-run/data/ 2>/dev/null || true
# 重启 8767（标准启动命令）
```

- [ ] **Step 3: 验证服务**

Run: `curl -s -o /dev/null -w "%{http_code}" https://yilichat.com/api/user/profile`
Expected: 401

- [ ] **Step 4: 产出 L1 验收报告**

写 `/tmp/l1_report.md`：三项功能实现说明、测试证据（各套件 PASS 数）、校准一致率（起运分解/神煞覆盖率）、commit hash 清单。
