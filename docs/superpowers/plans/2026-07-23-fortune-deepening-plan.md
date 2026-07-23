# Fortune Feature Deepening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Integrate 6 unused engines into handler + new API endpoints, simplify personality system from 3 modes to 1.

**Architecture:** Each module: Handler calls Engine → structured result → LLM generates narrative. 6 new API routers (`src/api/{module}.py`) expose the engines for direct miniprogram use. Cross-cutting personality simplification removes ~200 lines of mode-switching machinery.

**Tech Stack:** Python/FastAPI, DeepSeek v4, HehunEngine, QimenEngine, XingmingEngine, AdaptiveAdvisor, hourly_fortune module

## Global Constraints
- All 6 engines are already implemented and tested — tasks wire them into handlers/APIs
- Existing `/api/chat` behavior must remain backward-compatible
- New API routes follow pattern: `router = APIRouter(tags=["..."])` with `setup()` function
- Single personality tone: warm, cultured, context-aware
- No user-facing personality switching — remove all switch keywords, mode names, confirmation messages

---

### Task 0: Personality Simplification (Cross-cutting)

**Files:**
- Modify: `src/llm/prompts.py` — merge 3 prompts → 1
- Modify: `src/llm/client.py` — remove personality_mode parameter
- Modify: `src/bot/handler.py` — remove all personality machinery
- Modify: `src/api/calendar.py` — remove personality param
- Modify: `src/engines/face_reader.py` — remove personality param

**Interfaces:**
- Produces: `SYSTEM_PROMPT` (single), `FortuneLLM.analyze()` without personality_mode, `MessageHandler` without `_personality_modes`/`_get_personality_mode`/`_set_personality_mode`/`_detect_personality_switch`/`PERSONALITY_SWITCH_KEYWORDS`/`_add_feedback_prompt` multi-style

**Changes:**

- [ ] **Step 1: Merge 3 prompts into 1 in `src/llm/prompts.py`**

Replace `SYSTEM_PROMPT_SASSY`, `SYSTEM_PROMPT_ANALYST`, `SYSTEM_PROMPT_GENTLE`, `PERSONALITY_PROMPTS` dict with a single `SYSTEM_PROMPT`:

```python
SYSTEM_PROMPT = """你是「易理明灯」AI命理顾问。

## 人格设定
你是一个懂命理的现代朋友。你有文化底蕴，但说人话。用户来找你，你根据语境自然调整：对方难过时多些温暖，问事业时多些分析，日常闲聊时轻松自然。不装腔作势，不搞玄乎的。

## 身份定位
- 你是一个 AI 助手，没有性别
- 自称统一用"我"
- 称呼用户时根据其提供的性别信息：男性→"兄弟"/"大哥"，女性→"姐妹"/"姑娘"，未知→"朋友"/"这位朋友"
- 禁止：叫"宝贝""亲爱的""小友""老夫""老朽"

## 说话风格
- 现代中文，自然不做作
- 可根据语境灵活调节语气（温暖/分析/轻松）
- 善用 emoji 但不过度
- 不说文言文或半文半白

## 你的知识体系
- 八字命理（子平八字）、紫微斗数、易经占卜（六爻）
- 风水（玄空飞星、八宅）、面相手相
- 择日（建除十二神、二十八宿）、奇门遁甲
- 姓名学（五格剖象）、合婚配对（生肖+八字）

## 五大铁律

### 1. 排盘铁律
你绝对不能修改、质疑或"纠正"引擎提供的排盘数据（八字四柱、大运、十神、格局、用神）。引擎数据 100% 准确。

### 2. 引用铁律
每条命理论断必须引用古籍原文作为依据：【《书名》·章节】"原文"。找不到依据时明确说"此问题在现有典籍中未找到明确依据，不便妄断。"

### 3. 表达铁律
通俗白话解释文言文。先结论后依据。用"古籍记载""传统上认为"而非"一定会""绝对"。强调趋势和概率。

### 4. 概率表达
预测用概率化表述（"约60%可能性"），负面预测附带降低风险建议，区分确定性和推测性。

### 5. 领域专注
用户问什么答什么，不主动切换到其他命理领域。首次回复控制在 200 字以内。

## 输出格式
📊 **排盘/占卜信息**
🔍 **分析解读**
📖 **典籍依据**
📌 **综合判断**
💬 还想深入了解：【追问方向】

## 推理指南
1. 理解命盘结构（格局、旺衰、用神）
2. 定位用户问题核心
3. 结合大运流年推导关键时间节点
4. 引用最相关古籍依据
5. 给出切实可行的建议
"""

CHAT_PROMPT = """你是一个懂命理的现代朋友。和用户像朋友聊天。

## 核心原则
1. **情绪优先**：用户表达强烈情绪时，先共情理解再谈命理
2. **轻松自然**：回答简短自然，不长篇大论
3. **现代语言**：不说"小友""老夫"等老气称呼
4. **给建议而非只下结论**：用户焦虑时给出具体可行的建议

## 禁止行为
- ❌ 用户表达情绪时直接索要出生日期
- ❌ 模板化回复
- ❌ 长篇大论
- ❌ 文言文或半文半白
- ❌ 回复控制在 200 字以内
"""
```

Delete: `PERSONALITY_PROMPTS` dict, `PERSONALIZED_CONTEXT_TEMPLATE`. Keep: `CONFIDANT_PROMPT`, `CHAT_PROMPT`, `USER_CONTEXT_TEMPLATE`.

- [ ] **Step 2: Remove personality_mode from `src/llm/client.py`**

Update `FortuneLLM`:
- Remove `_resolve_mood()` method
- `chat()`: remove `personality_mode` parameter, always use `CHAT_PROMPT`
- `chat_conversation()`: remove `personality_mode` parameter, always use `CHAT_PROMPT`
- `analyze()`: remove `personality_mode` parameter, always use `SYSTEM_PROMPT` from prompts
- `_call_deepseek_model()`: remove `custom_prompt` parameter, always use unified prompt

Updated signatures:
```python
def chat(self, user_message: str) -> AnalysisResult:
    return self._call_deepseek_model(user_message, self.model, max_tokens=500)

def chat_conversation(self, history: list) -> str:
    ...

def analyze(
    self,
    chart_data: Union[BaziResult, str],
    references: List[ChunkResult],
    user_question: str,
    use_pro: bool = False,
    extra_system_prompt: str = None,
) -> AnalysisResult:
    system_prompt = SYSTEM_PROMPT
    if extra_system_prompt:
        system_prompt = system_prompt + "\n\n" + extra_system_prompt
    ...
```

Update import: `from .prompts import SYSTEM_PROMPT, CHAT_PROMPT, USER_CONTEXT_TEMPLATE`

- [ ] **Step 3: Remove personality machinery from `src/bot/handler.py`**

Remove:
- `PERSONALITY_SWITCH_KEYWORDS` dict (line ~170-174)
- `self._personality_modes = {}` (line ~205)
- `_get_personality_mode()` method (line ~230-241)
- `_set_personality_mode()` method (line ~243-247)
- `_detect_personality_switch()` method (line ~249-255)
- `_get_preference_hint()` method (line ~334-339) — remove personality style descriptions
- `_add_feedback_prompt()` — simplify to single style:

```python
def _add_feedback_prompt(self, reply: str) -> str:
    return reply + "\n\n———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准"
```

Update all call sites:
- `process()` (line ~518-537): Delete the entire "Step 0: 人格切换检查" block
- `process()` (line ~1210): Change `self._add_feedback_prompt(reply, personality)` → `self._add_feedback_prompt(reply)`
- `_try_face_reading()` (line ~744-749): Remove personality extraction, pass None/default
- All `self.llm.analyze(..., personality_mode=...)` calls: remove `personality_mode=` parameter
- All `self.llm.chat(..., personality_mode=...)` calls: remove `personality_mode=` parameter
- All `self.llm.chat_conversation(..., personality_mode=...)` calls: remove `personality_mode=` parameter
- `_handle_calendar()` (line ~1988-2010): Remove personality extraction

- [ ] **Step 4: Remove personality param from `src/api/calendar.py`**

Find all `personality` parameter references and remove them.

- [ ] **Step 5: Remove personality param from `src/engines/face_reader.py`**

Find `personality` parameter in `generate_report()` and remove it.

- [ ] **Step 6: Verify no breakage**

Run: `cd /mnt/e/fortune-agent && python -c "from src.llm.client import FortuneLLM; from src.llm.prompts import SYSTEM_PROMPT; print('OK')"`

- [ ] **Step 7: Commit**

---

### Task 1: Hehun Handler + API (合婚)

**Files:**
- Modify: `src/bot/handler.py` — rewrite `_handle_hehun`
- Create: `src/api/hehun.py` — new API router
- Modify: `src/main.py` — register router

**Interfaces:**
- Consumes: `HehunEngine.match(bazi1, bazi2) -> HehunResult` from `src/engines/hehun.py`
- Produces: `POST /api/hehun` endpoint, `_handle_hehun` uses engine instead of RAG-only

**Handler rewrite** — `_handle_hehun` in handler.py:

```python
def _handle_hehun(self, msg: str, user_id: str) -> str:
    """合婚配对 - 引擎计算五行/生肖/日柱 + LLM叙事"""
    # Extract both parties' birth info
    parts = re.split(r'[，。,\.\s]+女|女方|对方|对象|伴侣', msg)
    info_a = self._extract_bazi_info(msg)
    
    # Try to extract second person
    info_b = None
    if info_a and len(parts) > 1:
        info_b = self._extract_bazi_info(parts[1] if len(parts) > 1 else '')
    if not info_b:
        # Try alternative split: "男...女..."
        male_match = re.search(r'男[^女]*', msg)
        female_match = re.search(r'女.*', msg)
        if male_match and female_match:
            info_a = self._extract_bazi_info(male_match.group())
            info_b = self._extract_bazi_info(female_match.group())
    
    if not info_a or not info_b:
        return """请提供双方的信息进行合婚分析：

💡 **示例1**：男 1990年5月20日8时 北京，女 1992年8月15日14时 上海
💡 **示例2**：男1990年属马，女1993年属鸡
    
我会分析：五行互补 | 生肖配对 | 日柱关系 | 综合评分"""
    
    # Compute both charts
    from src.engines.bazi import BaziEngine
    bazi_eng = BaziEngine()
    year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a = info_a
    year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b = info_b
    result_a = bazi_eng.calculate(year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a or "男")
    result_b = bazi_eng.calculate(year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b or "女")
    
    # Engine matching
    hehun_result = self.hehun_engine.match(result_a, result_b)
    
    # Build structured chart string
    chart_str = f"""男方八字：{' '.join(result_a.bazi)}  日主{result_a.day_master}  属{result_a.shengxiao}
女方八字：{' '.join(result_b.bazi)}  日主{result_b.day_master}  属{result_b.shengxiao}

五行互补得分：{hehun_result.wuxing_score}/100 — {hehun_result.wuxing_detail}
生肖配对：{hehun_result.shengxiao_type} — {hehun_result.shengxiao_detail}
日柱关系得分：{hehun_result.rizhu_score}/100 — {hehun_result.rizhu_detail}
综合评分：{hehun_result.total_score}/100"""
    
    refs = self.retriever.search(f"合婚 婚姻匹配 {result_a.shengxiao} {result_b.shengxiao}", category="hehun", top_k=15)
    analysis = self.llm.analyze(chart_str, refs, f"分析这对男女的婚姻匹配度，给出3条化解建议")
    
    return analysis.response
```

**Create `src/api/hehun.py`:**

```python
"""合婚配对 API — 五行互补 + 生肖 + 日柱分析."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["hehun"])

class BaziInput(BaseModel):
    year: int; month: int; day: int
    hour: int = 0; minute: int = 0
    city: str = "北京"; gender: str = "男"

class HehunRequest(BaseModel):
    person_a: BaziInput
    person_b: BaziInput

class HehunResponse(BaseModel):
    total_score: int
    wuxing: dict; shengxiao: dict; rizhu: dict
    advice: list; summary: str

_hehun_engine = None; _bazi_engine = None

def setup(hehun_engine, bazi_engine):
    global _hehun_engine, _bazi_engine
    _hehun_engine = hehun_engine; _bazi_engine = bazi_engine

@router.post("/api/hehun", response_model=HehunResponse)
async def hehun_match(req: HehunRequest):
    a = req.person_a; b = req.person_b
    r1 = _bazi_engine.calculate(a.year, a.month, a.day, a.hour, a.minute, a.city, a.gender)
    r2 = _bazi_engine.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender)
    result = _hehun_engine.match(r1, r2)
    return HehunResponse(
        total_score=result.total_score,
        wuxing={"score": result.wuxing_score, "detail": result.wuxing_detail, ...},
        ...
    )
```

**Register in `src/main.py`:**
```python
from src.api.hehun import router as hehun_router, setup as hehun_setup
# In lifespan: hehun_setup(hehun_engine, engine)
app.include_router(hehun_router)
```

- [ ] **Step 8: Commit**

---

### Task 2: Qimen Handler + API (奇门遁甲)

**Files:**
- Modify: `src/bot/handler.py` — rewrite `_handle_qimen`
- Create: `src/api/qimen.py`
- Modify: `src/main.py`

**Handler rewrite:**
Call `QimenEngine.calculate()` to generate full chart → format as structured string → LLM interprets.

**API:** `POST /api/qimen` — returns full 9-palace chart with 用神 analysis.

- [ ] **Step 9: Rewrite handler**
- [ ] **Step 10: Create API router**
- [ ] **Step 11: Register in main.py**
- [ ] **Step 12: Commit**

---

### Task 3: Xingming Handler + API (姓名学)

**Files:**
- Modify: `src/bot/handler.py` — rewrite `_handle_xingming`
- Create: `src/api/xingming.py`
- Modify: `src/main.py`

**Handler rewrite:**
Extract name → `XingmingEngine.analyze()` → five-cell + 三才 + 81-numerology → LLM narrative.

**API:** `POST /api/xingming` — returns 五格, 三才, 81数理, 八字匹配.

- [ ] **Step 13: Rewrite handler**
- [ ] **Step 14: Create API router**
- [ ] **Step 15: Register in main.py**
- [ ] **Step 16: Commit**

---

### Task 4: Hourly Fortune Handler + API (时辰运势)

**Files:**
- Modify: `src/bot/handler.py` — add `_handle_hourly`
- Create: `src/api/hourly.py`
- Modify: `src/main.py`

**Handler:** NEW method. `get_hourly_fortune(user_day_master, day_branch)` → format → LLM summary.

**API:** `GET /api/hourly-fortune?user_id=xxx&date=2026-07-23`

- [ ] **Step 17: Add handler method + route keyword**
- [ ] **Step 18: Create API router**
- [ ] **Step 19: Register in main.py**
- [ ] **Step 20: Commit**

---

### Task 5: Advisor V2 Handler + API (AI建议)

**Files:**
- Modify: `src/bot/handler.py` — add `_handle_advisor`
- Create: `src/api/advisor.py`
- Modify: `src/main.py`

**Handler:** NEW method. `AdaptiveAdvisor.generate(user_bazi, context)` → celebrity matches + domain advice.

**API:** `POST /api/advisor`

- [ ] **Step 21: Add handler method + route keyword**
- [ ] **Step 22: Create API router**
- [ ] **Step 23: Register in main.py**
- [ ] **Step 24: Commit**

---

### Task 6: Xuetang Handler Enhancement + API (学堂)

**Files:**
- Modify: `src/bot/handler.py` — enhance `_handle_xuetang`
- Modify: `src/engines/xuetang.py` — add personalized_lesson()
- Create: `src/api/xuetang.py`
- Modify: `src/main.py`

**Handler enhancement:** Detect if user has saved bazi → personalize lesson examples.

**API:** `GET /api/xuetang/lesson?topic=xxx&user_id=xxx`

- [ ] **Step 25: Enhance handler**
- [ ] **Step 26: Add personalized_lesson to xuetang.py**
- [ ] **Step 27: Create API router**
- [ ] **Step 28: Register in main.py**
- [ ] **Step 29: Commit**

---
