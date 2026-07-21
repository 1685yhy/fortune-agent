# Competitor Data Cleanup Report

Generated: 2026-07-21 00:21:27

## Summary

- **Total raw records**: 60,100
- **Unique records (after dedup)**: 53,719
- **Duplicates removed**: 6,381 (10.6%)
- **Source files**: 32
- **Platforms**: 13

## Platforms

| Platform | Raw Records | Clean Records | Duplicates Removed | Dedup % |
|----------|------------:|--------------:|-------------------:|--------:|
|      daosuan |      38157 |      37657 |             500 |    1.3% |
|    zhouyihui |       9820 |       9320 |             500 |    5.1% |
|         zgjm |       9984 |       4927 |            5057 |   50.7% |
|        12880 |        478 |        469 |               9 |    1.9% |
|         smxs |        590 |        436 |             154 |   26.1% |
|       buyiju |        413 |        268 |             145 |   35.1% |
|        aqioo |        260 |        260 |               0 |    0.0% |
|    dajiazhao |        129 |        129 |               0 |    0.0% |
| zhouyisuanming |         97 |         97 |               0 |    0.0% |
|        64gua |         73 |         73 |               0 |    0.0% |
|         k366 |         68 |         68 |               0 |    0.0% |
|       shen88 |         28 |         14 |              14 |   50.0% |
|      wenzhen |          3 |          1 |               2 |   66.7% |

## Domain Distribution

| Domain | Count | Percentage |
|--------|------:|----------:|
|         general | 37901 |    70.6% |
|        fengshui |  9565 |    17.8% |
|           dream |  5528 |    10.3% |
|            bazi |   398 |     0.7% |
|        xingming |   258 |     0.5% |
|            zeri |    41 |     0.1% |
|           ziwei |    21 |     0.0% |
|           qimen |     7 |     0.0% |

## Quality Distribution

| Score | Count | Percentage |
|-------|------:|----------:|
|     1 |    15 |     0.0% |
|     2 |   168 |     0.3% |
|     3 | 37969 |    70.7% |
|     4 |  1082 |     2.0% |
|     5 | 14485 |    27.0% |

**Low-quality records (score 1-2)**: 183 (0.3%)

## Benchmark Candidates

- **Total candidates**: 396

| Domain | Count |
|--------|------:|
|            bazi |    80 |
|           dream |    80 |
|        fengshui |    80 |
|        xingming |    80 |
|            zeri |    37 |
|         general |    24 |
|           ziwei |    12 |
|           qimen |     3 |

## Data Quality Notes

- **Mojibake**: Records from `daosuan_max_20260719.jsonl` (37,657 records) have garbled encoding. When cleaner versions exist for the same URL, they were preferred.
- **Empty fields**: Some records have empty query or response fields; these are retained but likely score low on quality.
- **URL normalization**: Deduplication is based on exact URL match. Some platforms may have semantically identical pages with different URLs (counted as separate records).
- **Domain classification**: Based on keyword matching; some records may be misclassified or belong to multiple domains.

## Files

- **Clean data**: `/home/a/fortune-agent/data/competitor_data/competitors_clean.jsonl`
- **Benchmark candidates**: `/home/a/fortune-agent/data/competitor_data/benchmark_candidates.jsonl`
- **This report**: `/tmp/competitor_cleanup_report.md`
- **README**: `/home/a/fortune-agent/data/competitor_data/README.md`