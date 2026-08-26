# 阶段4 门禁报告

**阶段**：阶段4（多体系合成层——共识/分歧/不可比较三分类 + 多体系合成报告 + 合成考卷）
**日期**：2026-08-16
**分支**：engine-v1（不 push）

## 门禁结果

| 项 | 结果 |
|---|---|
| 全量回归 | **138/138 全绿**（`tests/test_engine_*.py` + `tests/test_liuren.py`，25 文件，175.36s）|
| 合成层单测（test_engine_synth.py）| **12/12**（三分类判定 8 + 考卷 2 + e2e 真实链 2）|
| 多体系报告单测（test_engine_report.py）| **7/7**（原 compose_report 3 项回归不破 + 合成报告 4 项）|
| 合成考卷（synth_cases.jsonl）| **8/8**（共识 3：五行/时间/多体系；分歧 3：五行/时间/两说并存；不可比较 2：六爻vs八字/缺体系空链）|
| e2e 真实体系链合成 | **2/2**（四链合成三分类非空无硬造共识 + ziwei/qimen 同生日真实分歧）|
| 阶段2/3 考卷回归 | **不破**（e2e 八字 25/25、四体系 unit 8/8/9/12 等，见全量回归）|
| 真实冒烟（真实排盘+推演链 → synthesize）| **成功**：bazi 7 步 + ziwei 5 步 + liuyao 5 步 + qimen 空链（未提供排盘结果）→ 共识 1 / 分歧 1 / 不可比较 3，见下 |
| LLM 冒烟（key 门控）| **真实调用成功**：compose_multi_report 追加「LLM 综合解读」，deepseek-v4-flash，报告 4864 字符（含确定性三分类节 + LLM 解读节）|

## 真实冒烟行（合成层）

- **bazi**：`BaziEngine().calculate(1990-05-20 16:30 北京 女)` → `deduce(system="bazi")` 7 步
  [排盘引擎.calculate → shishen.detect_combos → geju.determine_geju → qiongtong_table[乙][巳]
  → shensha.shensha_of → 大运流年.engine → 断语要点.compose]，穷通格言取"用癸水" → 用神五行 水
- **ziwei**：`ZiweiEngine().calculate(1990-05-20 16:30 北京 女)` → `deduce(system="ziwei")` 5 步
  [ziwei.排盘.calculate → wuxing_ju_of(水二局) → sihua_of → palace_order → 断语要点.compose]
- **liuyao**：`LiuyaoEngine().cast(method="random", question="财运如何", seed=42)` → 5 步
  [liuyao.起卦.cast(雷火丰) → liuqin_of(世爻地支申) → shiying_positions → bian_hexagram → 断语要点.compose]
- **qimen**：未提供排盘结果 → 空链 0 步（coverage 明示"未举证/未覆盖"）→ 如实入不可比较

**合成结果（真实数据，非构造）**：
- 共识 1：**五行一致：水**（八字用神癸水 vs 紫微水二局，evidence 各带穷通宝鉴/紫微斗数全书出处）
- 分歧 1：**五行不同：水、金**（六爻世爻申金为官鬼，三说并存各带出处，note 固定说明）
- 不可比较 3：八字/紫微/六爻 与 奇门（空链）均"无公共比较维度…不硬造共识"

**LLM 冒烟**：key 有 → `compose_multi_report(llm=FortuneLLM(deepseek-v4-flash))` 真实调用成功，
报告 = 各体系节 + 共识节 + 分歧节 + 不可比较节 + LLM 综合解读节（4864 字符），解读开头：
"先说说结论：**今年财运有起色，但钱来得不轻松…**"（真实模型输出）。

## 降级说明（阶段4 口径取舍，如实留证）

1. **可比较键归一化口径**：时间键统一取**起始年份**（八字流年 2026:丙午 → 2026；紫微大限 2026-2035 → 2026），
   只比较同单位年份；每体系每键取**首个断言**为规范值（流年列表取首年，避免多值噪音，其余保留在要点中展示）。
2. **六壬三传时机未纳入时间键**：当前六壬推演链无三传应期步骤，无可抽取的时间断言，如实不纳入（不硬造）；
   六壬五行键取三传五行**众数**（并列取先出现，如 子亥戌→水水土→水）。
3. **奇门局五行取值符星五行**：奇门遁局本身无直接五行属性，以值符星五行（值符为遁局统帅）为确定性口径，
   与紫微五行局、八字用神同列五行键比较。
4. **六爻用事五行**：以世爻地支五行（火珠林口径，日干为「我」）为值；世爻无地支时六爻无任何可比较键，
   与其它体系如实入不可比较（syn_0007 钉死）。
5. **空链成员如实入不可比较**：未提供排盘结果的体系（engine_result=None）推演链为空、无事实要点，
   与其体系对如实产生"无公共比较维度"，绝不硬造共识（syn_0008 与冒烟 qimen 空链均为真实形态）。
6. **LLM 冒烟口径**：compose_multi_report 的 LLM 节为附加解读，确定性三分类节不依赖 LLM；
   无 key 或调用失败时只记录原因，报告仍为完整确定性合成结果（test_compose_multi_report_llm_failure_recorded 钉死）。
