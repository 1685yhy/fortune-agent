# Sub-project A: Fortune Feature Deepening — Design Spec

**Date**: 2026-07-23
**Status**: Draft
**Scope**: 6 engine integrations + personality simplification + 6 new API endpoints

---

## 0. Personality Simplification (Cross-cutting)

### Current Problem
3 personality modes (sassy/analyst/gentle) require manual user switching via keywords. Users cannot distinguish the modes. The system generates unnecessary complexity and forces users into a "settings" mental model.

### Design
Merge 3 system prompts into 1. The single persona is:
- Warm, cultured, like a knowledgeable fortune-telling friend (温和有文化感)
- Context-aware tone adjustment: sad user → more comforting, career question → more analytical, casual chat → lighter
- No mode awareness for the user — it's just good conversational AI

### Changes
| Delete | Replace |
|--------|---------|
| `PERSONALITY_SWITCH_KEYWORDS` dict | Removed entirely |
| `_detect_personality_switch()` | Removed entirely |
| `_set_personality_mode()` | Removed entirely |
| `_get_personality_mode()` | Removed entirely |
| `self._personality_modes` dict | Removed |
| 3 system prompts in `llm/prompts.py` | 1 unified prompt |
| All `personality_mode=` parameters | Removed from all function signatures |
| `_add_feedback_prompt()` 3-style branching | Simplified to single style |
| `_get_preference_hint()` | Adjusted to single style |

### Files affected
- `src/bot/handler.py` — remove personality machinery, simplify all call sites
- `src/llm/prompts.py` — 3→1 system prompt
- `src/llm/client.py` — remove `personality_mode` parameter
- `src/api/calendar.py` — remove personality parameter
- `src/engines/face_reader.py` — remove personality parameter

---

## 1. Hehun (Marriage Matching) — Perfection Tier

### Current: RAG+LLM only, engine unused

### Target
`HehunEngine.match()` computes 3-dimensional compatibility:
- **五行互补 (25%)**: Five-element mutual generation/control between two charts
- **生肖配对 (30%)**: 六合/三合/六冲/六害 calculation
- **日柱关系 (45%)**: Day-pillar stem-branch interaction analysis

### Handler: `_handle_hehun`
- Extract both parties' birth info from message
- If both found → `HehunEngine.match(bazi1, bazi2)` → structured result → LLM narrative
- If only one found → prompt for second person's info with guided questions
- Output: structured compatibility report with scores, strengths, weaknesses, resolution advice

### New API: `POST /api/hehun`
```json
// Request
{
  "person_a": {"year": 1990, "month": 5, "day": 20, "hour": 8, "minute": 0, "city": "北京", "gender": "男"},
  "person_b": {"year": 1992, "month": 8, "day": 15, "hour": 14, "minute": 0, "city": "上海", "gender": "女"}
}
// Response
{
  "total_score": 78,
  "wuxing": {"score": 85, "detail": "...", "complement": "...", "deficiency": "..."},
  "shengxiao": {"score": 90, "type": "六合", "detail": "..."},
  "rizhu": {"score": 68, "relation": "相生", "detail": "..."},
  "advice": ["第1条化解建议", "第2条", "第3条"],
  "summary": "整体评价..."
}
```

### Perfection criteria
- [ ] Individual sub-scores + composite with weighted formula
- [ ] Resolution advice tailored to specific conflicts (六冲 → 风水调节, 五行缺 → 颜色/方位补偿)
- [ ] Structured response with emoji-free professional formatting

---

## 2. Qimen (奇门遁甲) — Perfection Tier

### Current: RAG+LLM only, engine unused

### Target
`QimenEngine.calculate()` computes full 时家转盘奇门:
- 地盘 (Dipan): 六仪三奇 in 9 palaces
- 天盘 (Tianpan): Stars rotated to 值符 position
- 八门 (Bamen): Doors rotated to 值使 position
- 九星 (Jiuxing): 9 stars distributed
- 八神 (Bashen): 8 deities distributed (阳遁顺/阴遁逆)
- 天盘奇仪: Heaven-disk stem placement

### Handler: `_handle_qimen`
- Extract date/time and question from user message
- `QimenEngine.calculate(year, month, day, hour)` → full chart
- Interpret: which palace matches user's question → analysis
- LLM generates strategic advice based on 用神 (useful god) interpretation

### New API: `POST /api/qimen`
```json
// Request
{"year": 2026, "month": 7, "day": 23, "hour": 10, "question": "投资决策"}
// Response
{
  "chart": {
    "dun_type": "阳遁三局",
    "dipan": {"1":"戊","2":"己","3":"庚",...},
    "tianpan": {"1":"天蓬","2":"天芮",...},
    "bamen": {"1":"休门","2":"死门",...},
    "jiuxing": {"1":"天蓬","2":"天芮",...},
    "bashen": {"1":"值符","2":"螣蛇",...}
  },
  "analysis": {
    "yongshen_palace": 3,
    "yongshen_name": "震宫",
    "door_quality": "吉",
    "star_quality": "平",
    "timing_window": "3-5天内",
    "strategy": "运筹建议..."
  }
}
```

### Perfection criteria
- [ ] Full 9-palace chart with all 5 layers
- [ ] 用神 (useful god) identification by question type
- [ ] 运筹 timing window with specific action recommendations
- [ ] Printable chart format via `print_chart()`

---

## 3. Xingming (姓名学) — Perfection Tier

### Current: RAG+LLM only, engine unused

### Target
`XingmingEngine.analyze()` computes Five-Cell Profile (五格剖象法):
- **天格 (Heaven)**: Surname+1 stroke → ancestral influence
- **人格 (Person)**: Surname+given first char → core destiny
- **地格 (Earth)**: Given name chars → early life
- **外格 (External)**: Total-persona+1 → social relationships
- **总格 (Total)**: All strokes → life trajectory
- **三才配置**: Heaven/Person/Earth five-element interaction
- **81数理**: Each cell's numerology interpretation
- **八字补益**: Compare five-element needs from bazi with name's wuxing profile

### Handler: `_handle_xingming`
- Extract name from message (via regex for Chinese characters)
- If bazi provided → `XingmingEngine.analyze(surname, given_name, bazi=...)` with element matching
- If name only → basic five-cell analysis
- For naming requests (起名) → generate 5 name candidates with bazi element supplementation

### New API: `POST /api/xingming`
```json
// Request
{"surname": "张", "given_name": "伟", "bazi": {"year":1990,"month":5,"day":20,"hour":8,"gender":"男"}}
// Response
{
  "wuge": {"tian": 12, "ren": 15, "di": 12, "wai": 9, "zong": 23},
  "numerology": {
    "tian": {"score": 85, "meaning": "..."},
    "ren": {"score": 95, "meaning": "..."},
    ...
  },
  "sancai": {"wuxing": ["木","土","木"], "quality": "吉", "detail": "..."},
  "bazi_match": {"needed_element": "金", "name_element": "火", "score": 60, "suggestion": "..."},
  "overall": "综合评分 82，中等偏上..."
}
```

### Perfection criteria
- [ ] 500+ character stroke table coverage
- [ ] Five-cell + 三才 + 81-numerology + bazi-element-matching, all 4 layers
- [ ] Naming mode: generate 5 candidates with element explanation
- [ ] Renaming mode: suggest alternatives that fix current name's weaknesses

---

## 4. Hourly Fortune (时辰运势) — Perfection Tier

### Current: Engine exists, no handler, no API

### Target
12 two-hour slots per day, each with:
- 吉凶等级 (3 levels: 吉/平/凶)
- 宜 (recommended activities)
- 忌 (activities to avoid)
- 个性化: based on user's day-master five-element and day's branch

### Handler: `_handle_hourly` (NEW)
- Route by keywords: "几点", "什么时候", "时辰", "今天什么时候"
- `get_hourly_fortune(user_day_master, day_branch)` → 12-slot array
- `format_hourly_card()` → formatted message
- LLM adds natural-language explanation of best/worst slots

### New API: `GET /api/hourly-fortune?user_id=xxx&date=2026-07-23`
```json
// Response
{
  "date": "2026-07-23",
  "day_master": "甲木",
  "day_branch": "午",
  "slots": [
    {"name": "子时", "time": "23:00-01:00", "branch": "子", "level": "平",
     "yi": ["休息", "冥想"], "ji": ["重要决策"], "detail": "子午冲..."},
    {"name": "卯时", "time": "05:00-07:00", "branch": "卯", "level": "吉",
     "yi": ["起床", "锻炼", "开始新项目"], "ji": [], "detail": "卯木生午火..."},
    ...
  ],
  "best_slots": ["卯时", "巳时"],
  "worst_slots": ["子时", "未时"],
  "summary": "今日午火当令，木性日主得生..."
}
```

### Perfection criteria
- [ ] All 12 slots with personalized ratings based on user's bazi
- [ ] Best/worst slot identification with specific activity guidance
- [ ] 地支 relation explanation for each slot (合/冲/刑/害)
- [ ] Integration into daily calendar endpoint

---

## 5. Advisor V2 (AI行动建议) — Perfection Tier

### Current: Engine complete but not wired into handler

### Target
`AdaptiveAdvisor.generate()`:
1. Compute user's bazi → find top 3 matching celebrities
2. For each of 5 life domains (事业/财运/感情/健康/个人成长), generate 3 specific, actionable suggestions
3. Suggestions grounded in bazi patterns + celebrity case studies

### Handler: `_handle_advisor` (NEW)
- Route by keywords: "建议", "怎么办", "有什么建议", "帮我分析"
- `AdaptiveAdvisor.generate(user_bazi, context)` → structured result
- Present: top 3 celebrity matches + domain-specific advice

### New API: `POST /api/advisor`
```json
// Request
{"user_id": "xxx", "context": "最近工作压力大，想换工作", "domains": ["事业", "财运"]}
// Response
{
  "celebrity_matches": [
    {"name": "马云", "similarity": 0.87, "bio": "...", "similarity_reason": "..."},
    ...
  ],
  "advice": {
    "事业": [
      {"suggestion": "...", "reasoning": "基于你八字中...", "timing": "2026年冬季"},
      ...
    ],
    "财运": [...]
  },
  "summary": "综合建议..."
}
```

### Perfection criteria
- [ ] Top 3 celebrity matches with similarity scores and explanations
- [ ] 5 domains × 3 suggestions each, all with bazi-based reasoning
- [ ] Timing recommendations tied to 大运/流年
- [ ] Fallback advice when API fails (already in engine)

---

## 6. Xuetang (八字学堂) — Perfection Tier

### Current: Basic keyword search for curriculum topics

### Target
3-level curriculum with personalized teaching:
- **入门**: 什么是八字, 天干地支, 五行生克, 阴阳学说
- **进阶**: 十神分析, 格局判断, 用神取法, 大运流年
- **专题**: 财运, 感情, 事业, 健康
- Each lesson pulls from RAG + personalizes with user's own chart as example

### Handler: `_handle_xuetang` (enhanced)
- "什么是正官" → RAG search + "以你的八字为例，正官在你的 X 柱..." 
- "教我看八字" → progressive curriculum guided flow

### New API: `GET /api/xuetang/lesson?topic=十神分析&user_id=xxx`
```json
// Response
{
  "topic": "十神分析",
  "level": "进阶",
  "content": "markdown content...",
  "personalized_example": "以你的八字为例：日主甲木，月干庚金为七杀...",
  "related_topics": ["正官七杀", "用神取法"],
  "quiz": {"question": "...", "options": [...], "answer": 2}
}
```

### Perfection criteria
- [ ] Full 3-level curriculum with 12 topics
- [ ] Every lesson personalized with user's bazi as example
- [ ] Interactive quiz at end of each lesson
- [ ] Related topic suggestions for guided learning path

---

## Files Changed

| File | Action | Est. Lines |
|------|--------|-----------|
| `src/bot/handler.py` | Refactor + 6 handlers | ~800Δ |
| `src/llm/prompts.py` | 3→1 prompt + module system instructions | ~300Δ |
| `src/llm/client.py` | Remove personality_mode param | ~30Δ |
| `src/api/hehun.py` | **NEW** | ~150 |
| `src/api/qimen.py` | **NEW** | ~180 |
| `src/api/xingming.py` | **NEW** | ~150 |
| `src/api/hourly.py` | **NEW** | ~120 |
| `src/api/advisor.py` | **NEW** | ~130 |
| `src/api/xuetang.py` | **NEW** | ~100 |
| `src/main.py` | Register 6 new routers | ~50 |
| `src/engines/xuetang.py` | Enhance lessons | ~100 |
| `src/api/calendar.py` | Remove personality param | ~20 |
| `src/engines/face_reader.py` | Remove personality param | ~10 |
| **Total** | | **~2200** |

## Not Changed
- Existing API endpoints (backward compatible)
- Miniprogram (will be Sub-project B)
- Deploy/Scraper (will be Sub-projects C, D)

## Testing
- Each engine: unit test verifies algorithm output
- Each API: integration test verifies endpoint contract
- Handler regression: existing 八字/紫微/六爻 flows unchanged
- Full `POST /api/chat` end-to-end for all 6 new intents
