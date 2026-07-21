# Bazi Accuracy Benchmark Report

**Date:** 2026-07-21T01:25:06.694131
**Engine:** BaziEngine (lunar-python)
**Reference:** 问真八字 (Wenzhen)
**Samples tested:** 500

## Overall Result

| Metric | Value |
|--------|-------|
| Overall Accuracy | **100.00%** |
| Acceptance Threshold | 99.5% |
| Status | **✅ PASS** |
| Discrepancies | 0 |

## Per-Dimension Accuracy

| Dimension | Weight | Accuracy | Correct / Total |
|-----------|--------|----------|-----------------|
| pillars | 40% | 100.00% | 500 / 500 |
| shishen | 20% | 100.00% | 2000 / 2000 |
| canggan | 15% | 100.00% | 4519 / 4519 |
| nayin | 10% | 100.00% | 2000 / 2000 |
| dayun | 10% | 100.00% | 3000 / 3000 |
| day_master | 5% | 100.00% | 500 / 500 |

## Scoring Method

Overall = Σ(dimension_accuracy% × weight)

= 100.00% × 40% + 100.00% × 20% + 100.00% × 15% + 100.00% × 10% + 100.00% × 10% + 100.00% × 5%
= **100.00%**

## Discrepancies Found

**Total: 0**


## Notes

- **晚子时 handling:** Records with `time=23:00` (晚子时) have been adjusted by advancing the date by 1 day for pillar calculation, as 23:00 is considered the start of the next day in traditional bazi theory.
- **藏干 (canggan):** Computed using lunar-python's `getXxxHideGan()` methods.
- **大运 (dayun):** Only the first 6 dayun pillars are compared.
- **日主 (day_master):** Only the heavenly stem (天干) of the day pillar is compared.

*Report generated at 2026-07-21T01:25:06.694131*