"""Reading versioning — ensures reproducibility and traceability of readings."""

READING_VERSION = "5.0.0"
BUILD_DATE = "2026-07-21"


def get_version_footer() -> str:
    """Return the version footer string with current timestamp in CST.

    Every bazi analysis response should end with this footer to ensure
    traceability and demonstrate deterministic reproducibility.
    """
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    # v2026-08-17：去 emoji（PM：回复 emoji 过多显 low）——页脚随每条
    # 分析回复展示，🔖/📌 去掉，保留版本与可复现说明
    return (
        f"解读版本: v{READING_VERSION} | 生成时间: {now}\n"
        "同一八字同一问题，结果始终一致"
    )
