# Step 1: AI Narrative Layer — Design Spec

**Goal**: Every API response = structured engine data + LLM-generated natural language narrative.
**Principle**: Users feel they're talking to a wise friend, not reading a JSON dump.

## Architecture

```
API Request → Engine (compute) → Structured Data
                              → NarrativeService (LLM Flash) → Natural Language
                              → Combined Response (data + narrative)
```

New file: `src/services/narrative.py`

## NarrativeService API

```python
class NarrativeService:
    def __init__(self, llm: FortuneLLM)

    # Each method: structured_input → prompt → LLM → narrative text
    def hehun(structure: dict) -> str
    def qimen(structure: dict, question: str) -> str
    def xingming(structure: dict) -> str
    def hourly(structure: dict) -> str
    def advisor_enrich(structure: dict) -> str  # enhance existing output
    def xuetang(topic: str, content: str, bazi: dict|None) -> str
```

## Changes per API Router

| Router | Change |
|--------|--------|
| `hehun.py` | `HehunResponse` + `narrative: str` field. Call NarrativeService.hehun() with engine result dict. |
| `qimen.py` | `QimenResponse` + `narrative: str` field. Call NarrativeService.qimen() with chart + question. |
| `xingming.py` | `XingmingResponse` + `narrative: str` field. Call NarrativeService.xingming(). |
| `hourly.py` | Response + `narrative: str` field. Call NarrativeService.hourly(). |
| `advisor.py` | Response already has LLM output, enrich it with NarrativeService.advisor_enrich(). |
| `xuetang.py` | Lesson response + `narrative_insight: str` field. Call NarrativeService.xuetang(). |

## LLM Setup

- Model: `deepseek-flash` (fast, ~3-5s)
- Temperature: 0.7 (creative but grounded)
- Max tokens: 600 (concise narratives)
- Each fortune type has its own system prompt template in `src/llm/prompts.py`

## Wire into main.py

```python
from .services.narrative import NarrativeService
narrative = NarrativeService(llm)
# Pass to each API setup: setup_xxx(engine, narrative)
```

All 6 `setup()` functions gain a `narrative` parameter.
