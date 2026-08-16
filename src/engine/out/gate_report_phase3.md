# 阶段3 门禁报告

**阶段**：阶段3（紫微/六爻/奇门/六壬 四体系规则库 + 多体系推演链 + e2e 多体系考卷）
**日期**：2026-08-16
**分支**：engine-v1（不 push）

## 门禁结果

| 项 | 结果 |
|---|---|
| 全量回归 | **122/122 全绿**（`tests/test_engine_*.py` + `tests/test_liuren.py`，23 文件，100.87s）|
| 紫微 unit 考卷 | **8/8**（zw_0001~0008，四化3+五行局2+主星分布2+宫序1）|
| 六爻 unit 考卷 | **8/8**（ly_0001~0008，六亲3+世应2+纳甲2+动变1）|
| 奇门 unit 考卷 | **9/9**（qm_0001~0009，八门属性3+九星2+三吉门2+遁局2）|
| 六壬 unit 考卷 | **12/12**（lr_0001~0012，九宗门 贼克/比用/涉害/遥克/昴星/别责/八专/返吟/伏吟 全覆盖）|
| e2e 多体系（fake llm 双注入）| **4/4**（e2e_zw/ly/qm/lr_0001，按 expected.system 路由 + 体系前缀断言 + 链非空 + 断语要点非空）|
| e2e 八字（阶段2 考卷回归）| **25/25 不破** |
| 真实冒烟（四体系真实排盘+规则链）| **4/4 链非空**（各 5 步，末步断语要点非空，见下）|
| LLM 冒烟（key 门控）| **真实调用成功**：compose_report 生产综合层 → FortuneLLM(api_key=settings.claude_api_key)，模型 deepseek-v4-flash，分析文本非空（两轮实测 1246/1381 字符）|

## 真实冒烟行（四体系各 1 条）

- **ziwei**：`ZiweiEngine().calculate(1990-05-20 16:30 北京 女)` → `deduce(system="ziwei")` 5 步
  [ziwei.排盘.calculate → wuxing_ju_of → sihua_of → palace_order → 断语要点.compose]，末步输出非空
- **liuyao**：`LiuyaoEngine().cast(method="random", question="财运如何", seed=42)` → `deduce(system="liuyao")` 5 步
  [liuyao.起卦.cast(本卦 雷火丰，动爻 3 爻) → liuqin_of → shiying_positions → bian_hexagram → 断语要点.compose]，末步输出非空
- **qimen**：`QimenEngine().calculate(2024-07-11 13:30)` → `deduce(system="qimen")` 5 步
  [qimen.排盘.calculate(阴遁2局) → men_attribute → star_attribute → 值符值使 → 断语要点.compose]，末步输出非空
- **liuren**：`LiurenEngine().calculate(1990-08-16 14:30)` → `deduce(system="liuren")` 5 步
  [liuren.排盘.calculate(月将午) → sipan → classify_zongmen(重审课/贼克) → kongwang(旬空寅卯) → 断语要点.compose]，末步输出非空

四体系 coverage 均如实标注"未举证"（证据检索为八字专属，本体系不做伪举证）。

## 降级说明（Task 1-7 已注明的口径取舍，如实留证）

1. **紫微五行局以命宫纳音为准**：任务书"生年纳音定局"口径经实证与 ZiweiEngine 不符——如庚午年纳音路旁土=土五局，而 1990-05-20（庚午年四月廿六申时）命宫乙酉泉中水实为水二局；按计划"考卷断言与 ZiweiEngine 输出一致"的硬要求，规则库以**命宫干支纳音**定局（紫微斗数全书·安星诀，rules/ziwei.py 头部口径说明），生年纳音可经 `wuxing_ju_by_nayin` 查任意干支纳音。
2. **六爻六亲双口径**：规则库按计划接口（Task 4）以**日干五行为"我"**（《火珠林》）；`analyze()/evaluate()` 未给日干时，世爻六亲采排盘结果的**卦宫口径**并在要点中明示（liuqin_note）。推演链固定传日干，口径统一，考卷均走日干口径。
3. **六壬涉害孟仲季降级**：涉害深浅计数未实现，用孟仲季简便法取用并记入 `raw_data["降级"]`；推演链将其如实转记 `add_coverage("未覆盖", "六壬排盘降级：…")`（test_deduce_liuren_chain_degrade_marks_uncovered 钉死）。另有中气缺失时月将降级为亥将，同样记降级。**绝不装懂**。
4. **非八字体系不举证**：evidence.py 证据检索为八字专属（日干 query），四体系推演链 coverage 明示"未举证"，不做伪举证。
5. **规则库只做确定性事实，不产解释性断语**：断语要点步骤为规则要点确定性组装，吉凶解释归 LLM 综合层（阶段3 核心原则）。
6. **LLM 冒烟口径**：settings 读 `ANTHROPIC_API_KEY` 环境变量（本环境有 key），生产路径真实调用成功；`tokens_used` 该路径未填充显示 0，属客户端计数口径，非门禁问题。
