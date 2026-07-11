# Fortune Agent - 算命风水AI代理

## Overview

Fortune Agent is an AI-powered Chinese fortune-telling and Feng Shui analysis system built with FastAPI. It integrates traditional Chinese metaphysics (八字命理, 紫微斗数, 风水, etc.) with a large language model (Claude) and a RAG (Retrieval-Augmented Generation) pipeline over classical Chinese texts.

The system exposes an HTTP API that can be integrated with WeChat bots via chatgpt-on-wechat (CoW), enabling users to get fortune readings through WeChat messages.

## Architecture

```
┌──────────────┐     HTTP POST      ┌──────────────────────────────────┐
│  WeChat Bot   │  /api/chat        │       Fortune Agent (FastAPI)    │
│  (CoW)        │ ──────────────►   │                                  │
│               │                   │  ┌──────────┐  ┌──────────────┐  │
│  config in    │                   │  │  Handler  │─►│ BaziEngine   │  │
│  cow-config/  │                   │  │ (routing) │  │(lunar-python)│  │
└──────────────┘                    │  └────┬─────┘  └──────┬───────┘  │
                                    │       │               │          │
                                    │       │               ▼          │
                                    │       │        ┌──────────────┐  │
                                    │       │        │  Retriever   │  │
                                    │       │        │  (ChromaDB   │  │
                                    │       ├───────►│   + BGE-zh)  │  │
                                    │       │        └──────┬───────┘  │
                                    │       │               │          │
                                    │       │               ▼          │
                                    │       │        ┌──────────────┐  │
                                    │       └───────►│  FortuneLLM  │  │
                                    │                │  (Claude)    │  │
                                    │                └──────────────┘  │
                                    │                                  │
                                    │  ┌──────────┐ ┌──────────────┐  │
                                    │  │  UserDAO │ │  Chunker/    │  │
                                    │  │ (SQLite) │ │  Embedder    │  │
                                    │  └──────────┘ └──────────────┘  │
                                    └──────────────────────────────────┘
```

## Project Structure

```
fortune-agent/
├── cow-config/            # WeChat bot configuration templates
│   └── config.json        # ChatGPT-on-WeChat config
├── config/
│   └── settings.yaml      # Application settings
├── scripts/
│   ├── build_index.py     # Build vector index from classical texts
│   └── test_llm.py        # End-to-end LLM analysis test script
├── src/
│   ├── main.py            # FastAPI entry point (port 8765)
│   ├── config.py          # Settings loader (file + env vars)
│   ├── bot/
│   │   ├── handler.py     # Message handler with intent routing
│   │   └── formatter.py   # WeChat message formatting (split long messages)
│   ├── engines/
│   │   └── bazi.py        # 八字排盘 engine (lunar-python)
│   ├── llm/
│   │   ├── client.py      # Claude API client
│   │   └── prompts.py     # System prompts (三大铁律, output format)
│   ├── rag/
│   │   ├── chunker.py     # Text chunking for classical Chinese
│   │   ├── embedder.py    # BGE embedding model wrapper
│   │   └── retriever.py   # ChromaDB semantic search
│   └── storage/
│       ├── dao.py         # Data access object (SQLite)
│       └── models.py      # Database schema
├── tests/
│   ├── test_bazi.py       # Bazi engine tests
│   ├── test_bot.py        # Handler & formatter tests
│   └── test_rag.py        # RAG pipeline tests
├── pyproject.toml         # Project metadata & dependencies
└── README.md
```

## Quick Start

### Prerequisites

- Python 3.12+
- Claude API key (set via `ANTHROPIC_API_KEY` env var or `config/settings.yaml`)

### Installation

```bash
# Clone and install
cd fortune-agent
pip install -e .

# Or install dependencies directly
pip install fastapi uvicorn chromadb sentence-transformers anthropic jieba rank-bm25 pillow matplotlib pyyaml httpx lunar-python
```

### Configuration

Edit `config/settings.yaml`:

```yaml
data_dir: /mnt/d/fortune-data
claude_model: claude-sonnet-5
claude_api_key: sk-ant-xxx  # Or set ANTHROPIC_API_KEY env var
```

### Build Vector Index

```bash
# Build ChromaDB vector index from classical texts
python scripts/build_index.py
```

This downloads the BGE embedding model (~1.3GB on first run) and indexes the core classical texts.

### Start the Server

```bash
python -m src.main
```

The server starts on `http://0.0.0.0:8765` with auto-reload.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message and get a fortune reading |
| GET | `/api/health` | Health check with user stats |
| GET | `/api/stats` | User statistics |

#### Chat Request

```json
POST /api/chat
{
  "message": "帮我看看八字 1990年5月20日15点 北京 男",
  "user_id": "wechat_user_123"
}
```

#### Chat Response

```json
{
  "reply": "📊 **命盘信息**\n八字：庚午 辛巳 乙酉 甲申\n日主：乙木\n...",
  "parts": ["📊 **命盘信息**...", "🔍 **分析解读**..."]
}
```

The `parts` field contains the same content split into WeChat-friendly segments (under 500 characters each).

## WeChat Integration

### Option 1: chatgpt-on-wechat (CoW)

1. Clone and install CoW:

```bash
cd /home/a
git clone https://github.com/zhayujie/chatgpt-on-wechat.git cow-fortune
cd cow-fortune && pip install -r requirements.txt
```

2. Copy the config template:

```bash
cp /home/a/fortune-agent/cow-config/config.json ./config.json
```

3. Start Fortune Agent: `python -m src.main`
4. Start CoW: `python app.py`

### Configuration Reference

- **CoW official repo**: https://github.com/zhayujie/chatgpt-on-wechat
- **API endpoint**: `http://127.0.0.1:8765/api/chat`
- The config uses CoW's `custom` model type to proxy requests to Fortune Agent's API

## Supported Intent Categories

| Intent | Keywords | Status |
|--------|----------|--------|
| 八字 bazi | 八字, 命理, 命格, 运势, 算命, 排盘, 四柱 | Implemented |
| 紫微斗数 ziwei | 紫微, 斗数, 十二宫 | Planned (Phase 2) |
| 六爻占卜 liuyao | 六爻, 卦, 占卜, 起卦 | Planned (Phase 2) |
| 风水 fengshui | 风水, 阳宅, 家居, 布局 | Planned (Phase 2) |
| 择日 zeri | 择日, 吉日, 搬家, 开业, 结婚日子 | Planned (Phase 2) |
| 面相 mianxiang | 面相, 手相, 看相 | Planned (Phase 2) |
| 奇门遁甲 qimen | 奇门, 遁甲 | Planned (Phase 3) |
| 姓名学 xingming | 名字, 起名, 姓名, 改名 | Planned (Phase 3) |
| 合婚 hehun | 合婚, 配对, 婚姻匹配 | Planned (Phase 3) |

## Data Paths

| Data | Default Path |
|------|-------------|
| Classical texts (古籍) | `/mnt/d/fortune-data/books/` |
| Vector database | `/mnt/d/fortune-data/vectordb/` |
| User database (SQLite) | `/mnt/d/fortune-data/userdata/fortune.db` |

Configure via `config/settings.yaml` or the `data_dir` key. All paths are relative to `data_dir`.

## Key Design Decisions

### 三大铁律 (Three Cardinal Rules)

The system prompt enforces three non-negotiable rules for the LLM:
1. **排盘铁律**: Never question or modify the computed Bazi chart data
2. **引用铁律**: Every claim must cite at least one classical text reference
3. **表达铁律**: Use accessible language, avoid absolutism, provide follow-up directions

### RAG Pipeline

- **Embedding**: BAAI/bge-large-zh-v1.5 for high-quality Chinese text embeddings
- **Vector Store**: ChromaDB with cosine similarity search
- **Chunking**: Sentence-aware chunking with overlap, designed for classical Chinese text
- **Hybrid Search**: Semantic + keyword extraction for relevant classical text retrieval

### Message Handler Flow

1. Intent detection (keyword-based)
2. Information extraction (birth details from message text)
3. Bazi chart computation (lunar-python)
4. Classical text retrieval (ChromaDB)
5. LLM analysis with context (Claude)
6. Response formatting (WeChat-compatible splitting)

## Testing

```bash
# Run all tests
cd /home/a/fortune-agent
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_bazi.py -v
python -m pytest tests/test_bot.py -v
python -m pytest tests/test_rag.py -v

# Skip embedding model download (offline)
SKIP_EMBEDDING_TEST=1 python -m pytest tests/ -v
```

## Development

### Running in Development Mode

```bash
# Auto-reload enabled by default
python -m src.main
# The server watches for file changes and reloads automatically
```

### Adding a New Engine

1. Create `src/engines/<name>.py` with a class following the `BaziEngine` pattern
2. Add intent keywords to `handler.py` `INTENT_KEYWORDS`
3. Add handling logic in `MessageHandler.process()`
4. Write tests in `tests/`

## Future Plans

### Phase 2 (Extension)
- 紫微斗数 engine (iztro integration)
- 六爻占卜 engine
- 风水飞星/八宅 engine
- 面相五行 analysis
- 择日 engine
- Chart image generation (matplotlib)
- Knowledge base expansion (OCR pipeline + 500+ texts)
- Multi-system intent routing

### Phase 3 (Polish)
- 奇门遁甲 engine
- 姓名学五格剖象
- 合婚 engine
- Daily fortune push (Cron)
- Full 2000+ text knowledge base
- User data mining
- Voice input support
- Image recognition (floor plan -> Feng Shui)

## License

Internal use - TAL Education Group
