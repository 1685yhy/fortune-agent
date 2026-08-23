"""存量数据直读：对话问'我的档案/解梦/历史/收藏…' → 直读秒回，不重走全流程。"""
import logging
from src.storage.dao import _decrypt_or_plain

logger = logging.getLogger(__name__)

# 类别 → 触发关键词（命中即直读）
CATEGORY_KEYWORDS = {
    "档案": ["档案", "生辰", "出生信息", "我的八字信息", "什么时辰"],
    # 注：'我的盘'/'上次的盘' 由 T5 重看盘直读全权处理（纯重看→T5 直读富文本；
    # 场景问句→T5 _route_by_scenario 排除走全流程），此处保留会导致场景问句
    # 被 _q_排盘 的 chart dump 短路，故不收录（审查 Task 6 缺陷，commit 见修复节）。
    "排盘": ["排过", "排的盘"],
    "解梦": ["解过什么梦", "以前.*梦", "梦的解读", "上次那个梦"],
    "历史": ["之前聊过", "以前说过", "历史对话", "上次聊", "之前说过什么"],
    # D4 修复：原 ['摇过什么签','抽过什么签','我的签'] 漏掉"抽的灵签/哪一支"
    # 等口语问法 → 直读 miss 走 LLM 凭空编造。扩词全部用"已发生"口径
    # （抽的/抽过/求的），不收录祈使式（抽/摇/求签）——"帮我抽一支灵签"
    # 等抽取动作请求不得被直读劫持。
    "灵签": ["摇过什么签", "抽过什么签", "我的签", "抽的签", "抽的灵签",
             "抽的什么签", "哪支签", "签是哪", "求的什么签", "求的签"],
    # D4 修复：原 ['取过什么名','起过什么名','我的名字'] 漏掉"保存过的名笺"；
    # 不收录祈使式"起个名/取个名"（取名动作请求不得被直读劫持）。
    "名笺": ["取过什么名", "起过什么名", "我的名字", "保存过的名笺",
             "取的名笺", "哪张名笺", "名笺是", "名笺叫"],
    "灯语": ["灯语", "昨晚的灯"],
    "择吉": ["选的日子", "吉日", "择吉计划", "办事清单"],
    "晨笺": ["晨笺", "今早的", "早上的运势"],
    # D1 修复：原 ['收藏过','我收藏的'] 漏掉"我收藏了什么"（QA AC-CHAT-011
    # 原句）→ 直读 miss 走 LLM 答"收藏夹是空的"。
    "收藏": ["收藏过", "我收藏的", "我的收藏", "收藏了什么", "收藏了啥",
             "收藏夹", "收藏内容", "收藏的东西", "收藏的"],
    "会员": ["会员", "我花了多少", "充值", "额度"],
}

class RecordQuery:
    def __init__(self, dao, person_dao, session_dao, chart_dao,
                 qian_dao=None, ming_dao=None, lamp_dao=None,
                 zeri_dao=None, jian_dao=None, member_dao=None,
                 fav_dao=None):
        self.dao, self.person_dao = dao, person_dao
        self.session_dao, self.chart_dao = session_dao, chart_dao
        # 轻量 DAO（Task 8/9 依赖）缺省 None —— 对应类别直读直接返回 None
        # （不建表、不伪造实现；None 判断由各类 _q_* 守卫）
        self.qian_dao = qian_dao
        self.ming_dao = ming_dao
        self.lamp_dao = lamp_dao
        self.zeri_dao = zeri_dao
        self.jian_dao = jian_dao
        self.member_dao = member_dao
        self.fav_dao = fav_dao  # Task 9 收藏直读（favorites 表）

    def direct_query(self, user_id: str, msg: str) -> str | None:
        if not msg:
            return None
        import re
        for cat, kws in CATEGORY_KEYWORDS.items():
            for kw in kws:
                if re.search(kw, msg):
                    handler = getattr(self, f"_q_{cat}", None)
                    if handler:
                        out = handler(user_id)
                        if out:
                            return out
        return None

    # —— 各类直读（解密仅服务端内存）——
    def _q_档案(self, user_id):
        p = self.person_dao.get_default_person(user_id)
        if not p:
            return "还没有档案，告诉我出生年月日时我帮你建档。"
        gender = p.get('gender')
        if not gender or gender == "unknown":
            gender = "性别未知"  # 'unknown' 是 truthy，'or' 会被绕过，需显式判断
        return (f"你的档案（命主：{p.get('name')}）："
                f"{p.get('birth_year')}年{p.get('birth_month')}月{p.get('birth_day')}日"
                f"{p.get('birth_hour')}时 · 出生地{p.get('city') or '未填'} · {gender}")

    def _q_排盘(self, user_id):
        if not self.chart_dao:
            return None
        c = self.chart_dao.get_latest_chart(user_id)
        if not c:
            return None
        bazi = c["bazi_json"].get("bazi") or []
        return "最近排过的盘：" + " ".join(bazi) + \
               f"（{c['created_at']}，日主 {c['bazi_json'].get('day_master','')}）"

    def _q_解梦(self, user_id):
        rows = self.dao.get_user_consultations(user_id, intent="dream", limit=5)
        if not rows:
            return None
        parts = [f"解过 {len(rows)} 次梦："]
        for r in rows:
            parts.append(f"· {_decrypt_or_plain(r.get('question',''))[:30]} → "
                         f"{_decrypt_or_plain(r.get('analysis_preview',''))[:50]}")
        return "\n".join(parts)

    def _q_历史(self, user_id):
        if not self.session_dao:
            return None
        s = self.session_dao.get_summary(user_id)
        if not s or not s.get("summary"):
            return None
        mem = ""
        if s.get("memories"):
            mem = "，记得：" + "；".join(str(m) for m in s["memories"][:5])
        return f"之前的聊天摘要：{s['summary'][:200]}{mem}"

    def _q_灵签(self, user_id):
        if not self.qian_dao: return None
        try:
            saves = self.qian_dao.list_history(user_id, limit=10)
            if not saves:
                return None
            # D4 修复：qian_saves 只存 (no, drawn_at)，签诗/吉凶须从签文库
            # QIAN_BY_NO 按 no 反查（与 /api/qian/history 同口径，防编造）。
            try:
                from src.api.qian import QIAN_BY_NO
            except Exception:
                QIAN_BY_NO = {}
            parts = [f"收藏的签：{len(saves)} 支"]
            for s in saves[:10]:
                no = s.get("no")
                entry = QIAN_BY_NO.get(no) if no is not None else None
                head = f"第{no}签"
                if entry:
                    if entry.get("jx"):
                        head += f"（{entry['jx']}）"
                    poem = "".join(entry.get("poem", []))
                    if poem:
                        head += f"「{poem}」"
                parts.append("· " + head)
            return "\n".join(parts)
        except Exception:
            return None

    def _q_名笺(self, user_id):
        if not self.ming_dao: return None
        try:
            saves = self.ming_dao.list_saved(user_id)
        except Exception:
            return None
        if not saves:
            return None
        parts = [f"取过的名字：{len(saves)} 个"]
        for s in saves[:10]:
            style = s.get("style_note") or "未标注风格"
            parts.append(f"· {s.get('full','')}（{s.get('score',0)}分，{style}）")
        return "\n".join(parts)

    def _q_灯语(self, user_id):
        if not self.lamp_dao: return None
        try:
            rows = self.lamp_dao.list_history(user_id, limit=1)
            if not rows:
                return None
            l = rows[0]
            return f"最近一条灯语（{l['date']}）：{l['text']}"
        except Exception:
            return None

    def _q_择吉(self, user_id):
        if not self.zeri_dao: return None
        try:
            plans = self.zeri_dao.list_plans(user_id, limit=3)
        except Exception:
            return None
        return "\n".join(f"· {p['scene']} → {p['lucky_date']}" for p in plans) if plans else None

    def _q_晨笺(self, user_id):
        if not self.jian_dao: return None
        try:
            card = self.jian_dao.get_card(user_id)
            if not card:
                # D3 修复：jian_cards 仅在推送时刻落库（本机无推送 → 空表）。
                # 空表时按当日确定性内容现算现存（与 _send_jian_batch 同一
                # 生成源 _precompute_jian_for + generate_private_line，落库键
                # 结构一致），保证对话直读与 /api/jian/today 逐字一致、且此后
                # 任何路径都能读到同一张卡（AC-CHAT-010）。
                try:
                    import datetime
                    from src.main import _precompute_jian_for
                    from src.engines.jian_private import generate_private_line
                    today = datetime.datetime.now().strftime("%Y-%m-%d")
                    content = _precompute_jian_for(today) or {}
                    card_json = {
                        "date": today,
                        "day_ganzhi": content.get("day_ganzhi", ""),
                        "suitable": content.get("suitable", []),
                        "unsuitable": content.get("unsuitable", []),
                        "quote": content.get("quote", ""),
                        "book": content.get("book", ""),
                        "private_line": generate_private_line(user_id),
                    }
                    self.jian_dao.save_card(user_id, today, card_json)
                    card = {"date": today, "card_json": card_json}
                except Exception:
                    return None
            cj = card["card_json"] or {}
            parts = [f"今日晨笺（{cj.get('day_ganzhi','')}）："]
            if cj.get("suitable"):
                parts.append("宜：" + "、".join(cj["suitable"]))
            if cj.get("unsuitable"):
                parts.append("忌：" + "、".join(cj["unsuitable"]))
            if cj.get("private_line"):
                parts.append(f"私语：{cj['private_line']}")
            if cj.get("quote"):
                book = f"（{cj['book']}）" if cj.get("book") else ""
                parts.append(f"金句：{cj['quote']}{book}")
            return "\n".join(parts)
        except Exception:
            return None

    def _q_收藏(self, user_id):
        if not hasattr(self, "fav_dao") or not self.fav_dao: return None
        try:
            favs = self.fav_dao.list_favorites(user_id, limit=10)  # Task 9 实现
        except Exception:
            return None
        if not favs:
            return None
        # D1 修复：type 英文键 → 中文标签，直读列表可读（AC-CHAT-011 期望
        # "可列出已存收藏"）。
        _TYPE_LABEL = {"chat": "对话", "jian": "晨笺", "qian": "灵签",
                       "ming": "名笺", "lamp": "灯语"}
        parts = [f"你收藏了 {len(favs)} 条内容："]
        for f in favs:
            label = _TYPE_LABEL.get(f["type"], f["type"])
            parts.append(f"· {label}: {f['summary'][:40]}")
        return "\n".join(parts)

    def _q_会员(self, user_id):
        if not self.member_dao: return None
        try:
            m = self.member_dao.get_membership(user_id)
        except Exception:
            return None
        if not m:
            return None
        limit = m.get("queries_limit")
        return f"会员档位：{m.get('plan_label') or m.get('plan')}，额度 {m.get('queries_used', 0)}/{limit if limit is not None else '不限'}"
