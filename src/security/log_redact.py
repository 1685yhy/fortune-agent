"""日志脱敏（k86 必修2）：**用户对话正文一律不落日志明文**。

背景（k85 实测 → k86 修）
------------------------
本仓多处 `logger.*` 直接把**用户输入正文**或**由提问生成的检索词**打进日志，
落盘在两处：
  · `logs/app.log`   —— 按天滚动、保留最近 14 份（`src/logging_config.py:21,50-57`）；
  · `logs/audit.log` —— 按容量滚动 100MB × 10、**无时间上限**，且每行还带
    `user_id` + 客户端 IP + User-Agent（`src/security/audit.py:120-173`）。
用户对话属于个人信息，不该以明文进入运维日志。运维排障真正需要的是
"**哪一类事件、谁、几次、是不是同一次输入**"，而不是"说了什么"。

口径
----
`redact(text)` 返回一个**不可反推**的短标记：`[len=42 h=1f3a9c7b2d04]`
  · `len` —— 正文长度（判断"超长注入"这类**形态**问题够用）；
  · `h`   —— HMAC-SHA256 前 12 位十六进制**指纹**：同一部署内**同文同指纹**，
             所以"这 6 条告警是不是同一段输入触发的"这类关联判断仍然成立；
             密钥不随日志下发 ⇒ 不持有密钥**无法**由指纹反推正文，
             也**无法**做跨部署的字典比对。

密钥来源：环境变量 `LOG_FINGERPRINT_KEY`。未设置时退化为**进程内随机**密钥
——此时指纹仅在本进程生命周期内可比对。这是刻意的安全默认：宁可少一点跨重启
关联，也不留一处"离线可爆破"的可逆痕迹。

与安全控制的关系（**不是削弱**）
------------------------------
本模块**只改日志文本**，不参与任何检测/拦截判定：
  · `sanitizer` 的命中判定与阻断**一字未改**（改的只是它顺带打的那行日志）；
  · `security_audit` 的**记录动作**保留，`user_id` / `ip` / `user_agent` /
    `attack_type` / `result` / 时间戳**全部原样**保留。
被替换掉的只有"输入正文片段"。安全信号（谁、何时、什么类型、拦没拦住）
一条不少，且多了"同源关联"这个此前没有的能力。

用法
----
    from src.security.log_redact import redact
    logger.warning("SQL injection detected: %s", redact(text))   # ← 不要写 text[:80]
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

#: HMAC 密钥：环境变量优先；未设置则用**进程内随机**（不做持久化、不落盘）。
_KEY: bytes = (os.getenv("LOG_FINGERPRINT_KEY") or "").strip().encode("utf-8") \
    or secrets.token_bytes(32)


def fingerprint(text) -> str:
    """正文的**不可反推**指纹（同部署内同文同值）。空值给固定短标记。"""
    if text is None:
        return "none"
    s = str(text)
    if not s:
        return "empty"
    return hmac.new(_KEY, s.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:12]


def redact(text) -> str:
    """日志用的脱敏标记：`[len=42 h=1f3a9c7b2d04]`。

    **永远不要**用 `text[:80]` 这类"截断"代替它 —— 截断只是缩短明文，
    前 80 个字往往已经足以还原用户问了什么。
    """
    if text is None:
        return "[none]"
    s = str(text)
    return f"[len={len(s)} h={fingerprint(s)}]"
