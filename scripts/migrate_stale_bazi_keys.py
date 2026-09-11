#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k19：存量 users.bazi_info「孤儿/矛盾 bazi 键」清理迁移（2026-09-10）。

背景（21:44 事故族，k8 后续项）：k8 起 dao 写守卫已防新增矛盾 bazi 键
（birth 键与 bazi 四柱矛盾 → 丢弃），但**存量行**仍在：历史事故把「他人/
择时盘四柱」以 bazi 键残留在 users.bazi_info（08-16 起被 G1 自愈沿用至
09-04 的形态；persons/chart_records 已分裂）。这些键 k8 后不再被任何
显示方消费，但构成数据污染与未来误读风险，本脚本负责主动清理。

清理判定（bazi 键只在「可证伪」时删，宁可少删不可误删）：
- 无 bazi 键 / 空 → 无事（幂等）；
- bazi_info birth y/m/d 齐全 → 用 BaziEngine 按 birth 复算四柱比对：
  - 与待清理 bazi 相等 → **自证一致**（真实本人盘镜像）→ 保留；
  - 不一致 → **stale_contradicts_birth**（21:44 畸形组装族）→ 清理；
  - 复算失败 → 退 chart_records 盘证：存在 birth 匹配（y/m/d + calendar
    + hour 有无，同 k8 _chart_birth_matches 口径）且 bazi 相等的行 →
    保留；否则 **stale_unverifiable** → 清理（宁清不残留：消费方自 k8
    起本就不读该键）；
- bazi_info 无完整 birth y/m/d（孤儿键）→ 无 birth 可自证/匹配 →
  **stale_orphan**（08-16 污染族形态）→ 清理。

清理动作 = 只删 bazi 键，birth 8 键（year/month/day/hour/minute/city/
gender/calendar）及其余镜像键原样保留。直接 SQL 写密文（绕过
save_user_bazi——避免 consultation_count+1 副作用与写守卫复算；守卫只防
新增，清理侧消费面自 k8 起已封口）。

安全设计：
- 默认 dry-run（只列出将清理行：user / birth 摘要 / 判定 / persons 一致
  性备注），零写入；k31 收口：备注读口径亦只读——persons 一律走
  `list_persons`，不触发 `get_default_person` 的建卡/提升默认自愈写
  （旧代码 dry-run 会因备注读静默建 person，属「dry-run 写库」契约违背）；
- --execute 必须显式 + 必须 --backup <路径>（执行前整库备份，目标已存在
  则拒绝——幂等保护）+ 必须 --audit <jsonl>（逐行变更审计）；
- 不认 FORTUNE_DB_PATH 等环境变量，只认显式 --db（防误碰生产库）；
- 幂等：执行后重跑 dry-run 应为 0 行待清理。

执行纪律：本批不真跑（执行留待用户确认）。用法：
  dry-run : python scripts/migrate_stale_bazi_keys.py --db <fortune.db> [--users u1,u2]
  execute : python scripts/migrate_stale_bazi_keys.py --db <fortune.db> --execute \
                --backup <prewipe-k19-xxx.db> --audit <k19-migrate.jsonl> [--users ...]
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

# 允许直接运行与 tests 单测 import：仓库根入 path 才能导 src（开发环境
# 也用同一布局；部署时以仓库根为 cwd 运行即可）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.storage.dao import _encrypt_text, _decrypt_or_plain, _is_ciphertext

# birth 键（四柱以外的画像键全保留；清理只删 bazi）
_BIRTH_KEYS = ("year", "month", "day", "hour", "minute",
               "city", "gender", "calendar")

STALE_ORPHAN = "stale_orphan"                 # bazi 键存在但 birth 键不齐
STALE_CONTRADICT = "stale_contradicts_birth"  # bazi 与本人 birth 复算矛盾
STALE_UNVERIFIABLE = "stale_unverifiable"     # birth 齐但复算失败且无盘证
KEEP_SELF = "keep_self_corroborated"          # 与本人 birth 复算一致
KEEP_CHART = "keep_chart_corroborated"        # 与匹配 chart_records 盘一致
KEEP_NO_KEY = "keep_no_bazi_key"              # 无 bazi 键（无事）

STALE_VERDICTS = (STALE_ORPHAN, STALE_CONTRADICT, STALE_UNVERIFIABLE)


# ────────────────────────────────────────────────────────────────────
# 判定函数（纯逻辑，tests 直测；engine/chart 结果由调用方注入）
# ────────────────────────────────────────────────────────────────────

def normalize_pillars(pillars) -> Optional[list]:
    """四柱 → 4 元素字符串列表（无法归一 → None）。"""
    if isinstance(pillars, str):
        pillars = pillars.split()
    if not isinstance(pillars, (list, tuple)) or len(pillars) != 4:
        return None
    return [str(s).strip() for s in pillars]


def has_bazi_key(bazi_info) -> bool:
    """行内是否携带非空 bazi 键。"""
    return (isinstance(bazi_info, dict)
            and normalize_pillars(bazi_info.get("bazi")) is not None)


def has_complete_birth(bazi_info) -> bool:
    """birth 年/月/日是否齐全（hour/minute/city/gender 可缺）。"""
    return (isinstance(bazi_info, dict)
            and all(bazi_info.get(k) not in (None, "")
                    for k in ("year", "month", "day")))


def stale_verdict(bazi_info, engine_pillars=None, chart_pillars=None) -> str:
    """单行清理判定（纯函数；bazi 只在「可证伪」时判清理）。

    Args:
        bazi_info: users.bazi_info 解密 dict。
        engine_pillars: 按本人 birth 复算的 4 柱；无完整 birth / 复算失败
            → None。
        chart_pillars: 与 birth 匹配的最近 chart_records 行 4 柱；无 → None。
    Returns:
        KEEP_* / STALE_* 判定字符串。
    """
    if not has_bazi_key(bazi_info):
        return KEEP_NO_KEY
    key = normalize_pillars(bazi_info.get("bazi"))
    if not has_complete_birth(bazi_info):
        # 无完整 birth → 无自证/盘证基准（08-16 孤儿污染族）→ 清理
        return STALE_ORPHAN
    exp = normalize_pillars(engine_pillars)
    if exp is not None:
        return KEEP_SELF if exp == key else STALE_CONTRADICT
    ch = normalize_pillars(chart_pillars)
    if ch is not None:
        return KEEP_CHART if ch == key else STALE_UNVERIFIABLE
    return STALE_UNVERIFIABLE


def _chart_birth_matches(chart_birth, info) -> bool:
    """chart_records 行 birth 是否与 bazi_info birth 匹配（k8 同口径：
    y/m/d + calendar + hour 有无；hour 具体值不参与——档案时辰代表整点
    与盘实际时钟小时可差 1 小时而仍属同时辰）。"""
    if not isinstance(chart_birth, dict) or not chart_birth.get("year"):
        return False
    for k in ("year", "month", "day"):
        if chart_birth.get(k) != info.get(k):
            return False
    if (chart_birth.get("calendar") or "solar") != (info.get("calendar") or "solar"):
        return False
    a, b = chart_birth.get("hour"), info.get("hour")
    return (a in (None, "", 0)) == (b in (None, "", 0))


def persons_note(pdao, user_id: str, bazi_info, auto_migrate: bool = False) -> str:
    """persons 与 bazi_info 一致性备注（展示用；不参与判定）。

    k31（dry-run 零写入契约，k30 审查 A）：读口径默认**只读**——绝不触发
    `get_default_person` 的两处自愈写：
      (a) 无默认行但有其他 person → 把最早者提升为默认（UPDATE is_default=1）；
      (b) 无任何 person 且 auto_migrate=True → `migrate_legacy_bazi` 建「我」
          （INSERT）。旧代码走 (a)(b)，dry-run 后静默多出 person 行，与脚本
          打印的「[dry-run] 未写入任何数据」不符。
    只读实现 = `list_persons`（`is_default DESC, created_at ASC, id ASC`，首行
    即默认行；与 `get_default_person` 首查同序，差异仅在「多默认行」这一
    is_default 唯一性被破坏的畸形形态——备注为展示用，不影响判定）。
    auto_migrate=True 仅供 `--execute` 复用旧行为（扫描期允许自愈/迁移写）。
    """
    try:
        if not pdao:
            p = None
        elif auto_migrate:
            p = pdao.get_default_person(user_id)
        else:
            persons = pdao.list_persons(user_id)
            p = persons[0] if persons else None
    except Exception:
        p = None
    if not p:
        return "无默认命主"
    pb = p.get("birth_year")
    bb = bazi_info.get("year") if isinstance(bazi_info, dict) else None
    if pb and bb and pb != bb:
        return f"persons默认={pb} 与 bazi_info birth={bb} 不一致"
    return f"persons默认={pb} 与 bazi_info 一致"


# ────────────────────────────────────────────────────────────────────
# 引擎复算 / 盘证查询（脚本运行期依赖，判定函数不依赖）
# ────────────────────────────────────────────────────────────────────

def recompute_pillars(bazi_info) -> Optional[list]:
    """按本人 birth 复算 4 柱（lunar 先单点转公历，同 k8 守卫口径）。

    返回 list[4] 或 None（缺 birth / 转换失败 / 引擎异常——调用方退盘证）。
    """
    try:
        if not has_complete_birth(bazi_info):
            return None
        y, m, d = (int(bazi_info["year"]), int(bazi_info["month"]),
                   int(bazi_info["day"]))
        if str(bazi_info.get("calendar") or "solar") == "lunar":
            from src.storage.birth_profile import to_solar_date
            sol = to_solar_date(bazi_info)
            if sol is None:
                return None
            y, m, d = sol
        hour = int(bazi_info.get("hour") or 0)
        minute = int(bazi_info.get("minute") or 0)
        city = str(bazi_info.get("city") or "")
        gender = bazi_info.get("gender") or "unknown"
        from src.engines.bazi import BaziEngine
        return list(BaziEngine().calculate(
            y, m, d, hour, minute, city, gender).bazi)
    except Exception:
        return None


def chart_corroboration(conn, user_id: str, bazi_info) -> Optional[list]:
    """与本人 birth 匹配的最近盘 4 柱（匹配口径见 _chart_birth_matches）。"""
    try:
        rows = conn.execute(
            "SELECT birth_enc, bazi_enc FROM chart_records "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 50", (user_id,)
        ).fetchall()
    except Exception:
        return None
    for r in rows:
        try:
            birth = json.loads(_decrypt_or_plain(r[0]) or "{}")
            if not _chart_birth_matches(birth, bazi_info):
                continue
            bj = json.loads(_decrypt_or_plain(r[1]) or "{}")
            return normalize_pillars((bj or {}).get("bazi"))
        except Exception:
            continue
    return None


# ────────────────────────────────────────────────────────────────────
# 扫描 / 清理执行
# ────────────────────────────────────────────────────────────────────

def _iter_user_ids(conn, only):
    if only:
        return [u for u in only
                if conn.execute("SELECT 1 FROM users WHERE user_id = ?",
                                (u,)).fetchone()]
    return [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]


def _birth_summary(info) -> str:
    if not isinstance(info, dict) or not has_complete_birth(info):
        return "birth 键不齐"
    s = "%s-%s-%s" % (info.get("year"), info.get("month"), info.get("day"))
    if info.get("hour") not in (None, "", 0):
        s += " %s时%s分" % (info.get("hour"), info.get("minute") or 0)
    if str(info.get("calendar") or "solar") == "lunar":
        s += " 农历"
    return s


def row_verdict(conn, user_id: str, info) -> str:
    """完整判定（复算 → 失败退盘证）；供扫描与执行双检共用。"""
    if not has_bazi_key(info):
        return KEEP_NO_KEY
    if has_complete_birth(info):
        verdict = stale_verdict(info, engine_pillars=recompute_pillars(info))
        if verdict == STALE_UNVERIFIABLE:
            verdict = stale_verdict(
                info,
                chart_pillars=chart_corroboration(conn, user_id, info))
    else:
        verdict = STALE_ORPHAN
    return verdict


def scan_stale(conn, pdao, only=None, auto_migrate: bool = False):
    """全量扫描 → (stale_rows, keep_rows)。

    行结构：{user_id, verdict, birth_summary, bazi, note}

    auto_migrate（k31）：透传给 persons_note 的读口径；默认 False = 只读，
    dry-run 绝不在扫描期建 person / 提升默认（`main` 仅在 --execute 时置 True）。
    """
    stale, keep = [], []
    for uid in _iter_user_ids(conn, only):
        row = conn.execute("SELECT bazi_info FROM users WHERE user_id = ?",
                           (uid,)).fetchone()
        if not row or not row[0]:
            continue
        raw = row[0]
        try:
            if _is_ciphertext(raw):
                info = json.loads(_decrypt_or_plain(raw) or "{}")
            else:
                info = json.loads(raw)
        except (ValueError, TypeError):
            keep.append({"user_id": uid, "verdict": "keep_decrypt_failed",
                         "birth_summary": "解密失败", "bazi": "",
                         "note": "行无法解密 → 不处理（需人工核查）"})
            continue
        if not isinstance(info, dict) or not has_bazi_key(info):
            continue
        verdict = row_verdict(conn, uid, info)
        item = {
            "user_id": uid,
            "verdict": verdict,
            "birth_summary": _birth_summary(info),
            "bazi": "/".join(normalize_pillars(info.get("bazi")) or []),
            "note": persons_note(pdao, uid, info, auto_migrate=auto_migrate),
        }
        (stale if verdict in STALE_VERDICTS else keep).append(item)
    return stale, keep


def cleanup_execute(conn, stale_rows) -> int:
    """执行清理：逐行删 bazi 键（birth 键及其余键保留），直接 SQL 写密文。

    幂等：执行后重跑扫描时这些行已无 bazi 键 → 不再出现。
    双检：执行时刻重判一次（防扫描与执行间行变化误删）。
    """
    n = 0
    for it in stale_rows:
        uid = it["user_id"]
        row = conn.execute("SELECT bazi_info FROM users WHERE user_id = ?",
                           (uid,)).fetchone()
        if not row or not row[0]:
            continue
        raw = row[0]
        try:
            if _is_ciphertext(raw):
                info = json.loads(_decrypt_or_plain(raw) or "{}")
            else:
                info = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(info, dict) or not has_bazi_key(info):
            continue
        if row_verdict(conn, uid, info) not in STALE_VERDICTS:
            continue  # 双检不通过 → 跳过（保留）
        kept = {}
        for k, v in info.items():
            if k == "bazi":
                continue  # 只删 bazi 键
            if v is not None:
                kept[k] = v
        new_enc = _encrypt_text(json.dumps(kept, ensure_ascii=False))
        conn.execute(
            "UPDATE users SET bazi_info=?, updated_at=? WHERE user_id=?",
            (new_enc, datetime.now().isoformat(), uid))
        it["removed_bazi"] = it.get("bazi")
        n += 1
    conn.commit()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="存量 bazi_info bazi 键清理（dry-run 默认；"
                    "执行需 --backup/--audit）")
    ap.add_argument("--db", required=True,
                    help="SQLite 库路径（显式必填，不认环境变量）")
    ap.add_argument("--users", default="",
                    help="限定 user_id（逗号分隔；缺省全量）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将清理行")
    ap.add_argument("--execute", action="store_true",
                    help="执行清理（需 --backup + --audit）")
    ap.add_argument("--backup", default="",
                    help="执行前整库备份目标路径（已存在则拒绝）")
    ap.add_argument("--audit", default="", help="执行审计 jsonl 输出路径")
    args = ap.parse_args(argv)

    if not args.execute:
        args.dry_run = True
    if args.execute:
        if not args.backup:
            ap.error("--execute 必须带 --backup <备份路径>")
        if not args.audit:
            ap.error("--execute 必须带 --audit <审计 jsonl>")
    if not os.path.exists(args.db):
        ap.error(f"--db 不存在：{args.db}")

    only = [u.strip() for u in args.users.split(",") if u.strip()] or None
    try:
        conn = sqlite3.connect(args.db)
    except Exception as e:
        ap.error(f"无法打开 --db {args.db}: {e}")
    try:
        from src.storage.person_dao import PersonDAO
        pdao = PersonDAO(args.db)
    except Exception:
        pdao = None

    # k31：dry-run 的零写入契约——扫描期只读（不建 person / 不提升默认）；
    # --execute 保持旧行为（扫描期允许 persons 自愈/兼容迁移写）。
    stale, keep = scan_stale(conn, pdao, only=only,
                             auto_migrate=not args.dry_run)
    print("== 判定结果 ==")
    for it in keep:
        if it["verdict"] == KEEP_NO_KEY:
            continue
        print(f"[保留] {it['verdict']:26s} user={it['user_id'][:16]} "
              f"{it['birth_summary']} bazi={it['bazi']} {it['note']}")
    for it in stale:
        print(f"[清理] {it['verdict']:26s} user={it['user_id'][:16]} "
              f"{it['birth_summary']} bazi={it['bazi']} {it['note']}")
    print(f"== 合计：待清理 {len(stale)} 行 / 保留 {len(keep)} 行 ==")

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据。确认后执行：--execute --backup "
              "<新备份路径> --audit <新审计路径>")
        return 0
    if os.path.exists(args.backup):
        print(f"[execute] 拒绝：备份目标已存在 {args.backup}"
              "（幂等保护，请换新路径）")
        return 2
    if not stale:
        print("[execute] 无待清理行（幂等：无需操作）")
        return 0
    print(f"[execute] 整库备份 → {args.backup}")
    with sqlite3.connect(args.db) as src, sqlite3.connect(args.backup) as dst:
        src.backup(dst)
    n = cleanup_execute(conn, stale)
    with open(args.audit, "w", encoding="utf-8") as f:
        for it in stale:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"[execute] 完成：清理 {n} 行；审计 → {args.audit}")
    print("[execute] 提醒：仅动 users.bazi_info；persons/chart_records 未动；"
          "请核对审计后重启服务")
    return 0


if __name__ == "__main__":
    sys.exit(main())
