#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练集导出脚本（方案 §7.3/§7.4：批量导出 → 脱敏 → JSONL）

按条件筛选对话（高赞/高收藏/评测高分）+ 脱敏（去 openid/个人信息）+
导出 JSONL（role/content 对，OpenAI 微调格式）。

用法：
  .venv/bin/python scripts/export_training_data.py                    # consultations 好评
  .venv/bin/python scripts/export_training_data.py --source sessions  # 会话历史
  .venv/bin/python scripts/export_training_data.py --intent bazi --since 2026-07-01
  .venv/bin/python scripts/export_training_data.py --out /tmp/train.jsonl --limit 200

输出 JSONL 每行：
  {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
   "meta": {"intent": ..., "emotion": ..., "model": ..., "retrieval_hit": ..., "source": ...}}
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.config import load_settings

# ── 脱敏规则 ─────────────────────────────────────────────────────────────
# 微信 openid（o 开头 28 位）：user_id 一律不导出，仅内容中出现时也抹除
_OPENID_RE = re.compile(r"o[A-Za-z0-9_\-]{20,}")
# 手机号：1[3-9]\d{9}
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 身份证：18 位（末位可为 X）
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 邮箱
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 纯数字日期时间保留（出生日期是命理必需信息），但连续 12+ 位纯数字串抹除
_LONG_NUM_RE = re.compile(r"(?<!\d)\d{12,}(?!\d)")


def desensitize(text: str) -> str:
    """脱敏：去掉 openid / 手机号 / 身份证 / 邮箱等个人信息。"""
    if not text:
        return text
    text = _OPENID_RE.sub("[openid]", text)
    text = _PHONE_RE.sub("[手机号]", text)
    text = _IDCARD_RE.sub("[证件号]", text)
    text = _EMAIL_RE.sub("[邮箱]", text)
    text = _LONG_NUM_RE.sub("[数字]", text)
    return text


def _decrypt_text(text):
    """解密会话/咨询的加密字段（兼容明文）。"""
    from src.storage.session_dao import _decrypt_or_plain
    return _decrypt_or_plain(text)


def _connect(db_path):
    from src.storage.models import connect
    return connect(db_path)


# ── 来源 1：consultations（反馈筛选：高赞/高收藏）────────────────────────
def export_consultations(conn, feedback: str, intent: str, since: str,
                         limit: int) -> list:
    sql = """SELECT user_id, question, analysis, intent, feedback, created_at
             FROM consultations WHERE analysis IS NOT NULL AND analysis != ''"""
    params = []
    if feedback == "positive":
        sql += " AND feedback = 'positive'"
    elif feedback == "negative":
        sql += " AND feedback = 'negative'"
    if intent:
        sql += " AND intent = ?"
        params.append(intent)
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        q = desensitize(_decrypt_text(r[1]))
        a = desensitize(_decrypt_text(r[2]))
        if not q or not a:
            continue
        out.append({
            "messages": [
                {"role": "user", "content": q},
                {"role": "assistant", "content": a},
            ],
            "meta": {
                "intent": r[3] or "",
                "feedback": r[4] or "",
                "created_at": r[5] or "",
                "source": "consultations",
            },
        })
    return out


# ── 来源 2：sessions（对话历史 → 角色对）──────────────────────────────────
def export_sessions(conn, intent: str, emotion: str, since: str,
                    min_rounds: int, limit: int) -> list:
    """按用户聚合会话，切分为 (user → assistant) 轮次对。

    每轮对 = 一条训练样本（带该轮 assistant 消息的 intent/emotion/model）。
    """
    rows = conn.execute(
        """SELECT user_id, role, content, intent, emotion, tool_calls,
                  retrieval_hit, model, created_at
           FROM sessions
           WHERE content IS NOT NULL AND content != ''
           ORDER BY user_id, created_at, id"""
    ).fetchall()
    users: dict = {}
    for r in rows:
        users.setdefault(r[0], []).append(r)

    out = []
    for user_id, msgs in users.items():
        user_msgs = []
        assistant_msgs = []
        for r in msgs:
            if r[1] == "user":
                user_msgs.append(r)
            else:
                assistant_msgs.append(r)
        # 用时间序配对：每条 assistant 回复与其前最近的 user 消息配对
        iu = 0
        for ra in assistant_msgs:
            while iu < len(user_msgs) and user_msgs[iu][8] <= ra[8]:
                iu += 1
            if iu == 0:
                continue
            ru = user_msgs[iu - 1]
            q = desensitize(_decrypt_text(ru[2]))
            a = desensitize(_decrypt_text(ra[2]))
            if not q or not a:
                continue
            if intent and (ra[3] or "") != intent:
                continue
            if emotion and (ra[4] or "") != emotion:
                continue
            if since and (ra[8] or "") < since:
                continue
            out.append({
                "messages": [
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": a},
                ],
                "meta": {
                    "intent": ra[3] or "",
                    "emotion": ra[4] or "",
                    "model": ra[7] or "",
                    "retrieval_hit": ra[6] or "unused",
                    "created_at": ra[8] or "",
                    "source": "sessions",
                },
            })
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="训练集导出（筛选 + 脱敏 + JSONL）")
    ap.add_argument("--db", default="", help="SQLite 路径（默认 settings.db_path）")
    ap.add_argument("--source", choices=["consultations", "sessions"], default="consultations")
    ap.add_argument("--feedback", choices=["positive", "negative", "all"], default="positive",
                    help="consultations 来源的反馈筛选（默认 positive=高赞）")
    ap.add_argument("--intent", default="", help="按意图筛选（bazi/ziwei/free_chat 等）")
    ap.add_argument("--emotion", default="", help="按情绪筛选（sessions 来源）")
    ap.add_argument("--since", default="", help="按时间筛选 YYYY-MM-DD")
    ap.add_argument("--min-rounds", type=int, default=1, help="会话最少轮数（保留兼容）")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--out", default="", help="输出 JSONL 路径（默认 data/exports/…）")
    args = ap.parse_args(argv)

    settings = load_settings()
    db_path = args.db or str(settings.db_path)
    conn = _connect(db_path)

    if args.source == "consultations":
        records = export_consultations(conn, args.feedback, args.intent,
                                       args.since, args.limit)
    else:
        records = export_sessions(conn, args.intent, args.emotion, args.since,
                                  args.min_rounds, args.limit)
    conn.close()

    # 去重（同一对消息只保留一条）
    seen = set()
    uniq = []
    for rec in records:
        key = (rec["messages"][0]["content"][:100], rec["messages"][1]["content"][:100])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(rec)

    out_path = args.out or str(
        PROJECT_DIR / "data" / "exports"
        / f"training_{args.source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in uniq:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"导出完成: {len(uniq)} 条 → {out_path}")
    if uniq:
        print(f"样例:\n{json.dumps(uniq[0], ensure_ascii=False, indent=2)[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
