# 易理推理内核 阶段3：其他体系规则库（紫微/六爻/奇门/六壬）实施计划

> For agentic workers: 本计划由子代理按 TDD 执行。每个 Task 完成后勾选 `- [ ]`。
> 纪律：只新增文件，不改现有 src；每任务一个 commit；验证留证据。

## Goal

阶段2 推演链引擎只覆盖八字。本阶段补齐**紫微 / 六爻 / 奇门 / 六壬**四个体系的确定性规则库（排盘复用现有引擎 + 新写六壬排盘），使推演链可按体系分支推演，各体系考卷全过。

## Architecture

- **只读复用**：`src/engines/ziwei.py`（ZiweiEngine.calculate）、`src/engines/liuyao.py`（LiuyaoEngine.cast，seed 固定）、`src/engines/qimen.py`（QimenEngine.calculate + print_chart）
- **新写**：`src/engines/liuren.py`（六壬排盘，现有引擎没有，lunar-python 已验证：日干支/节气可用）
- **规则库**：`src/engine/rules/` 下每体系一个文件，仿 `geju.py` 模式：纯函数 + `evaluate(pills_or_extra) -> dict`
- **推演链**：`DeductionStep.rule` 是自由字符串（体系无关），`deduce()` 按体系分支追加步骤段；`coverage.py` 追加 phase3 段
- **核心原则（沿用设计文档）**：只做**确定性规则/查表**（排盘事实、经典口诀、查表规则——可验证）；解释性断语一律归 LLM 综合层，规则库**不产断语**

## Tech Stack

- pytest（项目 venv：/mnt/e/fortune-agent/.venv/bin/python）
- lunar-python（Solar.fromYmdHms → getLunar()）
- 标准库 json

## Global Constraints

1. 分支 **engine-v1**，只新增：`src/engine/rules/{ziwei,liuyao,qimen,liuren}.py`、`src/engine/cases/{ziwei,liuyao,qimen,liuren}_cases.jsonl`、`src/engines/liuren.py`、`tests/test_engine_{ziwei,liuyao,qimen,liuren}.py`、`tests/test_liuren.py`、本设计相关文档
2. **零修改现有 src**（含 src/engines/ziwei.py 等）；唯一例外：`src/engine/coverage.py` 追加 phase3 段（属本工程）、`src/engine/deduction.py` 追加体系分支（属本工程）
3. 复用接口签名已钉死（勘察确认）：`ZiweiEngine().calculate(year, month, day, hour, minute, city, gender)`、`QimenEngine().calculate(year, month, day, hour, minute=0, city="北京")`、`LiuyaoEngine().cast(method="random", question="", seed=None)`——**勿臆造**
4. 考卷 quality 只允许 `unit` / `e2e`；`expected` 自由字段（loader 不校验），unit 考卷 `expected: {"<key>": <值>}`
5. 中文注释；提交 message `feat(engine): ...`；不 add logs/audit.log 与 docs/superpowers/plans/ 下旧未跟踪 plan；**不 push 远程**
6. 冒烟门控沿用阶段2：嵌入冒烟受 `SKIP_EMBEDDING_TEST` 门控、LLM 冒烟受 key 门控，**失败只记录原因不硬过**

## File Structure

```
src/engines/liuren.py            # 新写六壬排盘（Task 1）
src/engine/rules/ziwei.py        # 紫微规则库（Task 3）
src/engine/rules/liuyao.py       # 六爻规则库（Task 4）
src/engine/rules/qimen.py        # 奇门规则库（Task 5）
src/engine/rules/liuren.py       # 六壬规则库（Task 2）
src/engine/cases/{ziwei,liuyao,qimen,liuren}_cases.jsonl
src/engine/deduction.py          # 加体系分支（Task 6，允许修改）
src/engine/coverage.py           # 加 phase3 段（Task 8，允许修改）
src/engine/e2e_eval.py           # 多体系断言参数化（Task 7，允许修改）
tests/test_liuren.py             # 排盘引擎测试（Task 1）
tests/test_engine_{ziwei,liuyao,qimen,liuren}.py
```

---

## Task 1：六壬排盘引擎（src/engines/liuren.py）

**Files:**
- Create: `src/engines/liuren.py`、`tests/test_liuren.py`

**Interfaces:**
```python
@dataclass
class LiurenResult:
    year_gan, year_zhi, month_gan, month_zhi, day_gan, day_zhi, hour_zhi: str
    yuejiang: str            # 月将（如"亥"）
    tianpan: dict[str, str]  # 地盘宫 -> 天盘将，如 {"子": "亥"}
    sipan: list[dict]        # 四课，每课 {"位置": "干支"} 或简化 4 段干支
    sanchuan: list[str]      # 三传（初/中/末传），如 ["申", "午", "辰"]
    guiren: str              # 贵人所在宫位（干支）
    xunkong: list[str]       # 旬空地支，如 ["寅", "卯"]
    # 其余中间量进 raw_data

class LiurenEngine:
    def calculate(self, year, month, day, hour, minute=0, city="北京") -> LiurenResult
```

**算法要点（确定性规则，实现须对照古籍口径）：**
1. **月将**：以中气定将——雨水后亥将、春分后戌将、谷雨酉、小满申、夏至未、大暑午、处暑巳、秋分辰、霜降卯、小雪寅、冬至丑、大寒子（用 `getCurrentQi` 判断）
2. **天地盘**：月将加占时（时支），顺时针布十二宫（地盘子丑寅……卯），天盘将随月将落占时位顺布
3. **四课**：日干寄宫（甲寄寅、乙寄辰、丙戊寄巳、丁己寄未、庚寄申、辛寄戌、壬寄亥、癸寄丑）取干上神为第一课，课上神递生第二课；日支取支上神为第三课，递生第四课
4. **三传（九宗门，本任务实现贼克/比用/涉害/遥克/昴星/别责/八专/返吟/伏吟的确定性判定）**：先看四课上下克——有下贼上取"贼克"（上克下取"重审"，一克用克神，多克先下贼后上克，比用法去阴阳不同者）；无克依次遥克/昴星/别责/八专/返吟/伏吟
5. **贵人**：口诀"甲戊庚牛羊，乙己鼠猴乡，丙丁猪鸡位，壬癸兔蛇藏，六辛逢马虎"（昼贵/夜贵按时辰定），贵人顺逆看其在地盘阴阳（贵人落地盘阳宫顺行、阴宫逆行，布天将十二将）
6. **旬空**：日柱所在旬空亡二字
7. 无法确定处（如涉害取课未实现），**如实降级并记入 raw_data["降级"]**，不伪造

**TDD 步骤：**
1. Step 1 写失败测试：已知命例（如 1990-08-16 14:30 北京，日柱癸丑，庚午年甲申月——查古籍或已知起课结果断言月将/三传/旬空字段存在且类型正确；对可手算的断言精确值，如旬空：癸丑日属甲辰旬，旬空=寅卯）
2. Step 2 跑测试确认失败（红）
3. Step 3 实现 src/engines/liuren.py
4. 验证：`pytest tests/test_liuren.py -v` 全绿
5. 提交：`feat(engine): 六壬排盘引擎——月将加时/天地盘/四课三传/贵人旬空(阶段3)`（git add 仅 liuren.py + test_liuren.py）

- [x] Task 1 完成

---

## Task 2：六壬规则库（src/engine/rules/liuren.py）

**Files:**
- Create: `src/engine/rules/liuren.py`、`src/engine/cases/liuren_cases.jsonl`、`tests/test_engine_liuren.py`

**Interfaces:**
```python
def analyze(chart: dict) -> list[str]   # 确定性要点：三传是否贼克/比用/涉害等、旬空落传、贵人顺逆
def evaluate(chart: dict) -> dict       # 返回 {"要点": [...]}，供跑分断言
```

**规则内容（只做确定性）：**
1. 三传宗门判定（贼克/比用/涉害/遥克/昴星/别责/八专/返吟/伏吟九宗门分类）
2. 旬空是否落三传（空亡入传）、落四课
3. 贵人顺行/逆行
4. 三传五行（干支纳五行）

**考卷**：8 条 unit 合成样例（手算核实的经典案例，如涉害课/返吟课各 1），`expected: {"三传": [...], "宗门": "涉害", ...}`；audit 注明合成口径

**TDD 步骤：** 同 Task 1 模式；验证 `pytest tests/test_engine_liuren.py -v` 全绿；提交 `feat(engine): 六壬规则库——九宗门/旬空/贵人顺逆+考卷(阶段3)`

- [x] Task 2 完成

---

## Task 3：紫微规则库（src/engine/rules/ziwei.py）

**Files:**
- Create: `src/engine/rules/ziwei.py`、`src/engine/cases/ziwei_cases.jsonl`、`tests/test_engine_ziwei.py`

**Interfaces:**
```python
def wuxing_ju_of(lunar_month: int, time_zhi: str) -> str   # 五行局查表（水二/木三/金四/土五/火六）
def sihua_of(gan: str) -> dict[str, str]                   # 生年四化表：{"禄": 星, "权": 星, "科": 星, "忌": 星}
def palace_of_minggong(...)                               # 命宫/身宫计算（复用 ziwei 结果字段时直接断言其值）
def analyze(result) -> list[str]                          # 确定性要点：五行局/命宫宫位/四化星曜/身宫
def evaluate(result) -> dict
```

**规则内容（只做确定性查表）：**
1. 五行局查表（生年纳音五行的局，用 lunar-python 纳音或 ziwei raw_data 已有 wuxing_ju——规则库直接查表实现，考卷断言与 ZiweiEngine 输出一致）
2. 生年四化表：甲廉破武阳 / 乙机梁紫阴 / 丙同机昌廉 / 丁阴同机巨 / 戊贪阴弼机 / 己武贪梁曲 / 庚阳武阴同 / 辛巨阳曲昌 / 壬梁紫左武 / 癸破巨阴贪
3. 十二宫定序（命宫→兄弟→夫妻→……→父母，顺行/逆行按生年阴阳——复现 ziwei 已有口径）
4. 主星分布要点：紫微系（紫微→天机→太阳→武曲→天同→廉贞）与天府系（天府→太阴→贪狼→巨门→天相→天梁→七杀→破军）顺序规则（查表）

**考卷**：8 条 unit（各干支年四化 3 条 + 五行局 2 条 + 主星分布 2 条 + 宫序 1 条），`expected: {"四化禄": "廉贞", ...}`；audit 注明查表口径（渊海子平/紫微斗数全书）

**TDD 步骤：** 同前；验证 `pytest tests/test_engine_ziwei.py -v` 全绿；提交 `feat(engine): 紫微规则库——五行局/四化/主星分布+考卷(阶段3)`

- [x] Task 3 完成

---

## Task 4：六爻规则库（src/engine/rules/liuyao.py）

**Files:**
- Create: `src/engine/rules/liuyao.py`、`src/engine/cases/liuyao_cases.jsonl`、`tests/test_engine_liuyao.py`

**Interfaces:**
```python
def liuqin_of(day_gan: str, line_gan_or_wuxing) -> str     # 六亲计算（生我父母/克我官鬼/我克妻财/同我兄弟/我生子孙）
def shiying_positions(hexagram_name: str) -> tuple[int, int]  # 世应位（八宫卦表）
def najia_dizhi(upper: str, lower: str) -> list[str]       # 纳甲地支（复用/复刻 liuyao.get_line_dizhi 口径）
def analyze(result) -> list[str]                           # 确定性要点：六亲用事/世应/动爻数/变卦
def evaluate(result) -> dict
```

**规则内容（只做确定性）：**
1. 六亲生克计算（以日干五行为我）
2. 世应位置（八宫卦世应口诀：天同二世天变五，地同四世地变初……或八宫表直接查）
3. 纳甲表（乾金甲子外壬午、坎水戊寅外戊申……标准纳甲）
4. 动爻变卦（老阴/老阳动，变爻阴阳互变）

**考卷**：8 条 unit（六亲 3 条 + 世应 2 条 + 纳甲 2 条 + 动变 1 条），audit 注明口径（火珠林/卜筮正宗）

**TDD 步骤：** 同前；验证全绿；提交 `feat(engine): 六爻规则库——六亲/世应/纳甲/动变+考卷(阶段3)`

- [x] Task 4 完成

---

## Task 5：奇门规则库（src/engine/rules/qimen.py）

**Files:**
- Create: `src/engine/rules/qimen.py`、`src/engine/cases/qimen_cases.jsonl`、`tests/test_engine_qimen.py`

**Interfaces:**
```python
def dun_style(...) -> str                      # 阳遁/阴遁判定（冬至后阳遁、夏至后阴遁）
def men_attribute(men: str) -> str             # 八门属性（开门乾金/休门坎水/生门艮土/伤门震木/杜门巽木/景门离火/死门坤土/惊门兑金）
def star_attribute(star: str) -> str           # 九星属性（天蓬水/天任土/天冲木/天辅木/天英火/天芮土/天柱金/天心金/天禽土）
def san_ji_men(men: str) -> bool               # 三吉门（休/生/开）
def analyze(result) -> list[str]
def evaluate(result) -> dict
```

**规则内容（只做确定性查表）：**
1. 八门五行属性表、九星五行属性表、八神序（值符→螣蛇→太阴→六合→白虎→玄武→九地→九天）
2. 三吉门判定（休生开）
3. 阴阳遁判定（以节气为界）
4. 值符值使所在宫（排盘给出，规则库断言其存在）

**考卷**：8 条 unit（八门属性 3 条 + 九星 2 条 + 三吉门 2 条 + 遁局 1 条），audit 注明口径（烟波钓叟歌）

**TDD 步骤：** 同前；验证全绿；提交 `feat(engine): 奇门规则库——八门九星属性/三吉门/遁局+考卷(阶段3)`

- [x] Task 5 完成

---

## Task 6：推演链多体系接入（deduction.py）

**Files:**
- Modify: `src/engine/deduction.py`（允许，属本工程）
- Test: `tests/test_engine_deduction.py`（扩展）

**Interfaces / 行为：**
- `deduce(pills, engine_result=None, question="", system="bazi")` —— 新增 `system` 参数（默认 "bazi" 保持阶段2 行为不变）
- system="ziwei"：步骤段 = 排盘(紫微) → 五行局 → 四化 → 命宫/十二宫 → 断语要点（规则要点组装）
- system="liuyao"：步骤段 = 起卦(seed 固定注明) → 六亲 → 世应 → 动变 → 断语要点
- system="qimen"：步骤段 = 排盘(遁局/局数) → 八门 → 九星 → 值符值使 → 断语要点
- system="liuren"：步骤段 = 排盘(月将/天地盘) → 四课 → 三传(宗门) → 旬空/贵人 → 断语要点
- 非八字体系不加证据检索（evidence.py 日干 query 是八字专属，阶段3 举证对四体系**降级为"未举证"**，coverage 明示）
- 未实现处走 `add_coverage("未覆盖", ...)`，**绝不装懂**

**TDD：** 每体系至少 2 条链构建测试（fake engine_result 用真实引擎输出）+ 1 条"未覆盖明示"测试；验证 `pytest tests/test_engine_deduction.py -v` + 全部 test_engine_*.py 回归不破坏；提交 `feat(engine): 推演链多体系分支——紫微/六爻/奇门/六壬接入(阶段3)`

- [x] Task 6 完成

---

## Task 7：e2e 多体系考卷与断言（e2e_eval.py 参数化）

**Files:**
- Modify: `src/engine/e2e_eval.py`（允许；断言段按体系参数化）
- Create: `src/engine/cases/e2e_phase3_cases.jsonl`
- Test: `tests/test_engine_e2e.py`（扩展）

**内容：**
- e2e_phase3_cases.jsonl：紫微 1 条 + 奇门 1 条 + 六壬 1 条（公历生日完整输入 `expected["birth"]`；六壬需 hour）+ 六爻 1 条（`expected["seed"]` 固定随机，question 必填）
- e2e_eval：按体系路由（system 字段），断言该体系步骤前缀存在（如 `liuren.` / `ziwei.` / `liuyao.` / `qimen.`），链非空；fake LLM 双注入
- 验证：`pytest tests/test_engine_e2e.py -v` 全绿 + 全量回归
- 提交：`feat(engine): e2e多体系考卷——紫微/六爻/奇门/六壬端到端(阶段3)`

- [x] Task 7 完成

---

## Task 8：门禁收官

**门禁内容（全部执行并留证据）：**
1. 全量回归：`pytest tests/test_engine_*.py -v` 全绿（含 test_liuren.py）
2. 各体系 unit 考卷全过：ziwei/liuyao/qimen/liuren 各 ≥8 条
3. e2e 多体系全过（fake llm）
4. 真实冒烟（门控）：四体系各 1 条真实排盘+规则链冒烟（嵌入/LLM 按既有门控，失败只记录原因）
5. `coverage.py` 追加 phase3 段（`"phase3": {"rules": {...各体系规则数}, "cases": {...}, "gate": "..."}`）
6. 生成 `src/engine/out/gate_report_phase3.md`（阶段/各体系考卷结果/冒烟行/降级说明）
7. 提交：`feat(engine): 阶段3收官——四体系规则库+多体系推演链+门禁(阶段3)`（gate 产物随包）

**自审记录**：完成 `## 自审记录`（规格覆盖/诚实边界/类型一致性/依赖）于本计划文档末尾。

- [x] Task 8 完成

---

## 自审记录

（2026-08-16 Task 8 门禁收官填写）

**规格覆盖**：Task 1-8 全部完成——六壬排盘引擎（月将加时/天地盘/四课三传/贵人旬空，涉害孟仲季降级如实标注）、四规则库（紫微 11 函数/六爻 11/奇门 10/六壬 9，均只做确定性查表不产断语）、四 unit 考卷（8/8/9/12 条，与真实排盘引擎输出交叉验证）、deduce 多体系分支（system 参数，bazi 默认行为阶段2 完全一致，四体系各 5 步链 + 未覆盖明示）、e2e 多体系考卷与断言参数化（4 条 + 体系前缀路由）。门禁：回归 122/122、unit 8/8/9/12、e2e 4/4、八字 25/25 不破、真实冒烟四体系链非空、LLM 冒烟真实调用成功。coverage.py 追加 phase3 段（含 phase2 段保持可再生成），gate_report_phase3.md 已出。

**诚实边界**：四体系不做证据检索（evidence.py 八字专属），链 coverage 明示"未举证"；六壬涉害深浅未实现 → 孟仲季简便法 + raw_data["降级"] + 链未覆盖转记，绝不装懂；紫微五行局以命宫纳音为准（实证与引擎一致，任务书生年纳音口径不符已注明）；六爻六亲双口径在要点中明示；LLM/嵌入冒烟按既有门控，失败只记录原因——本次 LLM 冒烟真实成功（deepseek-flash 分析文本非空），无任何伪造。

**类型一致性**：deduce(pills, engine_result, question, system) 签名兼容阶段2（默认 "bazi" 行为不变）；各引擎结果 dataclass（ZiweiResult/LiuyaoResult/QimenResult/LiurenResult）与规则库 evaluate/analyze 输入一致（另兼容 dict）；e2e 断言参数化按 expected.system 路由，SYSTEM_PREFIXES 覆盖 bazi/ziwei/liuyao/qimen/liuren 五体系；coverage.py write_coverage 原签名兼容（新增可选 phase3 参数，test_engine_coverage.py 全绿）。

**依赖**：只读复用 src/engines/{ziwei,liuyao,qimen}.py 与 lunar-python（Solar 取四柱口径统一）；新写 src/engines/liuren.py（lunar-python 已验证日干支/节气可用）；对阶段2 依赖（case_loader/evidence/report）未改其接口；不 push 远程；logs/audit.log 与旧未跟踪 plan 不入库。
