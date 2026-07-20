# Competitor Data

## Overview

This directory contains cleaned and deduplicated competitor data from 13 fortune-telling platforms.
The data was collected via web scraping and processed through a deduplication, classification, and quality-scoring pipeline.

## Data Files

| File | Description | Records |
|------|-------------|--------:|
| `competitors_clean.jsonl` | Main clean dataset (deduplicated) | 53,719 |
| `benchmark_candidates.jsonl` | High-quality reference records for benchmarking | 396 |
| `README.md` | This file | - |

## Schema

Each record in `competitors_clean.jsonl` has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `query` | string | The user's query/fortune-telling question |
| `response` | string | The platform's response/prediction/analysis |
| `platform` | string | Source platform name |
| `url` | string | Original URL |
| `scraped_at` | string | ISO timestamp of scraping |
| `domain` | string | Classified domain (bazi, ziwei, fengshui, dream, mianxiang, qimen, xingming, zeri, general) |
| `domain_confidence` | int | Number of matching keywords |
| `quality_score` | int | Quality score 1-5 |

## Platforms

| Platform | Description |
|----------|-------------|
| zgjm | 周公解梦 (Zhou Gong Dream Interpretation) |
| daosuan | 道算网 (Dao Suan) |
| zhouyihui | 周易汇 (Zhou Yi Hui) |
| smxs | 算命先生 (Suàn Mìng Xiān Shēng) |
| buyiju | 卜易居 (Bu Yi Ju) |
| 12880 | 12880.com (Dream Interpretation) |
| 64gua | 64卦网 (64 Hexagrams) |
| aqioo | Aqioo (Fortune Telling) |
| k366 | K366 (Multi-purpose) |
| dajiazhao | 大家找 (Da Jia Zhao) |
| shen88 | 神88网 (Shen 88) |
| zhouyisuanming | 周易算命 (Zhou Yi Fortune) |

## Domains

- **general**: 37,901 records (70.6%)
- **fengshui**: 9,565 records (17.8%)
- **dream**: 5,528 records (10.3%)
- **bazi**: 398 records (0.7%)
- **xingming**: 258 records (0.5%)
- **zeri**: 41 records (0.1%)
- **ziwei**: 21 records (0.0%)
- **qimen**: 7 records (0.0%)

## Quality Distribution

- **Score 1**: 15 records (0.0%)
- **Score 2**: 168 records (0.3%)
- **Score 3**: 37,969 records (70.7%)
- **Score 4**: 1,082 records (2.0%)
- **Score 5**: 14,485 records (27.0%)

## Usage

### Loading the data in Python

```python
import json

records = []
with open('competitors_clean.jsonl', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            records.append(json.loads(line))

# 53,719 records loaded
```

### Filtering by domain

```python
bazi_records = [r for r in records if r['domain'] == 'bazi']
```

### Filtering by quality

```python
high_quality = [r for r in records if r['quality_score'] >= 4]
```

### Benchmark candidates

```python
benchmarks = []
with open('benchmark_candidates.jsonl', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            benchmarks.append(json.loads(line))
```

## Processing Pipeline

1. **Read**: Load all records from ~30 JSONL source files (12 platforms)
2. **Deduplicate**: Group by URL, keep highest-quality version per group
3. **Classify**: Assign domain labels via keyword matching on query + response text
4. **Score**: Quality scoring (1-5) based on content length, structure, classical references, and encoding quality
5. **Select**: Identify benchmark candidates (high-quality, authoritative platforms, well-distributed across domains)

## Notes

- The original source data contains 60,100 records across 32 files.
- After URL-based deduplication, 53,719 unique records remain.
- The `daosuan_max` source file (37,657 records) has garbled encoding issues; cleaner versions were preferred during dedup where available.
- Domain classification uses keyword matching and may have some misclassifications.