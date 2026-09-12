"""存量数据直读：对话问'我的档案/解梦/历史/收藏…' → 直读秒回，不重走全流程。"""
import logging
from src.storage.dao import _decrypt_or_plain

logger = logging.getLogger(__name__)

# 会员直读支付词守卫（终审缺陷修复）：命中任一支付/引导意图词时，即使消息
# 含"会员"关键词也不直读档位——支付引导全流程（handler 会员精确词分支 +
# LLM 意图路由）不得被 _q_会员 档位 dump 短路（真机必现缺陷）。
# 仅拦"会员"关键词："我花了多少"是纯账户查询词（"我花了多少钱"含"多少钱"
# 但不能被误伤），不受守卫影响。
# 复审缺口修复（支付词守卫补裸动词）：handler 会员精确词分支仅整句等值命中
# （"会员"/"升级"/"付费"/"套餐"/"价格"/"多少钱"），"我想买会员""成为会员"
# "办个会员""开会员""会员卡怎么办"等自然支付句式不落精确词 → 直读子串命中
# "会员" → _q_会员 档位 dump 短路，支付引导入口被吞（与终审缺陷同危害）。
# 增补裸购买动词/问法："买/开/办/成为/续"（"办" 同时覆盖 "怎么办"；
# "怎么买"原表已有，"怎么开/怎么续"由 开/续 覆盖），另补口语 "弄/搞"
# （"弄个会员""搞个会员""会员卡怎么弄"）。误伤评估：守卫仅拦含"会员"的
# 消息，误伤面=已买想查的账务查询（"我买了会员怎么查"）→ 降级 LLM，
# 可接受（降级不劫持）；反向豁免（"买了/已经买"放行）会重新打开
# "我想买了会员"等祈使式劫持面，不加。
# 终审二次封口（支付词守卫最终封口）：两字词拦不住单字——"充个会员"/
# "怎么充会员"（"充值"含"充"但 "充个/怎么充" 不落两字词）→ 档位 dump
# 劫持实锤；同族"领个会员"/"会员卡怎么领"同理未测即必穿。补单字 "充/领"
# 及支付/开通意图动词全集（按"宁可多拦、误伤仅降级"逐词评估）：
#   "冲"——"充"的网络口语变体（"冲个会员"），真实穿透；
#   "收费/花钱/要钱/缴费/缴/交"——"会员收费吗""怎么交会员费"等价格问法
#     （"交"为扫全集发现缺口，"缴费"拦不住"交会员费"）；
#   "订/申请/兑换/购/付"——"订阅会员""申请开通会员""积分兑换会员"
#     "怎么兑换会员""怎么付会员"（"订阅/购买"拦不住 "订个/购个/怎么付"）；
#   "延期/激活"——"会员延期""怎么激活会员"（小程序真实路径）；
#   "会员费/会员卡"——名词兜底（所有"卡"出现均为"会员卡"，裸"卡"太宽不补，
#     按误伤标准评估：纯查询句"我的会员是什么/我花了多少钱/我的会员等级是啥"
#     不落上述任何词，仍直读；"我的会员卡"类查询命中仅降级 LLM，可接受）。
_MEMBER_PAY_WORDS = ("充值", "开通", "升级", "购买", "续费", "付费",
                     "套餐", "价格", "多少钱", "优惠", "优惠券", "怎么买",
                     "便宜",
                     "买", "开", "办", "成为", "续", "弄", "搞",
                     "充", "领", "冲", "收费", "花钱", "要钱", "缴费",
                     "缴", "交", "订", "申请", "兑换", "购", "付",
                     "延期", "激活", "会员费", "会员卡",
                     # k32（A17）：营销/议价/退还类（打折/涨价/降价/促销）与
                     # 社交裂变类（送/拼/抢）支付意图词——真机"会员打折吗"
                     # "会员怎么退"等会被档位 dump 短路、吞掉支付引导全流程。
                     # 口径沿用既有"宁可多拦、误伤仅降级"：纯查询（我的会员
                     # 是什么/我花了多少钱/我的会员等级是啥）不落任何新词仍直读。
                     "打折", "涨价", "降价", "促销", "退", "送", "拼", "抢")

# F1 动作词守卫：档案直读不得劫持 排盘/建档/更新 动作意图（PM 真机反馈：
# "我的出生年月日是啥"曾答非所问——扩关键词后若不做守卫，"我生日是1999年
# 5月13日帮我排盘"/"更正我的生日"等动作句会被 _q_档案 档位 dump 短路）。
# 命中动作词即跳过档案直读（宁漏勿误：直读漏了只是慢，误读劫持才是答非所问）。
# 简报原单：排盘|排一下|排个|重排|重新排|建档|更新|修改|更正|改生日|保存|
# 新档案|登记。补单字（参考 _MEMBER_PAY_WORDS 单字封口先例——两字词拦不住
# "排下盘/改下生日/建个档/存一下"）：
#   "排"——"帮我排下盘"（排盘/排一下 均不落）；
#   "算"——"帮我算生辰八字"（"生辰"关键词真实穿透：排盘请求被档案 dump 劫持）；
#   "测"——"测测我的生辰"（同族穿透）；
#   "改"——"改下生日/改一下生日"（"改生日"不落）；
#   "建"——"建个档"（"建档"不落）；
#   "存/写/填/记/录/设置"——"存下生日/写上生日/填一下/记一下/补录/设置生日"；
#   "快乐/蛋糕"——"生日快乐/生日蛋糕" 是问候/名词而非档案查询（"生日"关键词
#     的误伤面，无守卫会被档位 dump 答非所问）；
#   "弄/搞"（批次 2 B3-26 补）——"帮我弄下生日档案/搞一份档案/把生日弄上" 同族
#     动作穿透（_MEMBER_PAY_WORDS 早已封口 弄/搞，本表补齐同口径）。
# 误伤评估：守卫仅拦 同时含档案关键词+动作词 的消息；纯查询（"我的出生年月日
# 是啥"/"我生日是哪天"/"什么时候出生"）不落任何动作词，仍直读秒回；误伤面
# = 含动作词的查询句（"我的生日改过了吗"/"生日弄错了吗"）→ 降级 LLM，可接受
# （降级不劫持：守卫只会让直读漏判走 LLM，绝不会把查询句档位 dump 答非所问）。
_ARCHIVE_ACTION_WORDS = ("排盘", "排一下", "排个", "重排", "重新排",
                         "建档", "更新", "修改", "更正", "改生日",
                         "保存", "新档案", "登记",
                         "排", "算", "测", "改", "建", "存", "写",
                         "填", "记", "录", "设置", "弄", "搞",
                         "快乐", "蛋糕")

# 农历月名（与 src/api/paipan.py LUNAR_MONTH_CN 同口径：冬月/腊月）——
# 仅 _q_档案 农历问法追加用，本地定义避免重 import 拉入 FastAPI 路由链。
_LUNAR_MONTH_CN = ("正月", "二月", "三月", "四月", "五月", "六月",
                   "七月", "八月", "九月", "十月", "冬月", "腊月")

# 类别 → 触发关键词（命中即直读）
CATEGORY_KEYWORDS = {
    "档案": ["档案", "生辰", "出生信息", "我的八字信息", "什么时辰",
             # F1 修复：日常说法全部漏网 → "我的出生年月日是啥/什么时候出生"
             # 等走 LLM 全流程答非所问。扩词全部用"已发生/询问"口径（不收录
             # 祈使式——"帮我排盘/建档/改生日"由 _ARCHIVE_ACTION_WORDS 守卫
             # 跳过直读走全流程，见 direct_query 守卫）。
             "出生年月日", "出生日期", "生日", "哪天出生", "什么时候出生",
             "哪年出生", "何时出生", "阴历生日", "农历生日", "阳历生日"],
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
    # k40（T053）：补「抽过的签」——原表子串互不包含（「抽过什么签」「抽的签」
    # 都不含「抽过的签」）→ 直读 miss → LLM 闲聊追问（T053 实锤；对照 T051
    # 「我最近抽的灵签」绿）。同批穷举「已发生」口径的同族变形（抽过的灵签/
    # 抽过哪些签/求过的签），祈使式仍不收录（「帮我抽一支灵签」不得被劫持）。
    "灵签": ["摇过什么签", "抽过什么签", "我的签", "抽的签", "抽的灵签",
             "抽的什么签", "哪支签", "签是哪", "求的什么签", "求的签",
             "抽过的签", "抽过的灵签", "抽过哪些签", "抽过那几支签",
             "求过的签", "抽过哪支签"],
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
    # 终审修复（支付入口被吞）：原 ["会员","我花了多少","充值","额度"]——
    # "充值"/"额度"是支付/引导意图词（"怎么充值"/"额度用完了怎么办"）会被
    # _q_会员 档位 dump 短路，吞掉支付引导全流程（handler 会员精确词分支在
    # 直读之前，但仅精确词命中）。故从关键词移除"充值"/"额度"；"会员"关键词
    # 另由 _MEMBER_PAY_WORDS 守卫——"充值会员/开通会员/会员多少钱"等支付意图
    # 即使含"会员"也不直读。纯账户查询（"我的会员是什么"/"我花了多少钱"）不受影响。
    "会员": ["会员", "我花了多少"],
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
                    # 支付/引导词守卫：会员直读不劫持支付意图（见 _MEMBER_PAY_WORDS）
                    if kw == "会员" and any(w in msg for w in _MEMBER_PAY_WORDS):
                        continue
                    # F1 动作词守卫：档案直读不劫持 排盘/建档/更新 动作意图
                    # （见 _ARCHIVE_ACTION_WORDS；命中动作词即跳过，宁漏勿误）
                    if cat == "档案" and any(w in msg for w in _ARCHIVE_ACTION_WORDS):
                        continue
                    handler = getattr(self, f"_q_{cat}", None)
                    if handler:
                        # 仅 _q_档案 需要 msg（农历/阴历问法判定），其余 _q_* 签名不变
                        out = handler(user_id, msg) if cat == "档案" else handler(user_id)
                        if out:
                            return out
        return None

    # —— 各类直读（解密仅服务端内存）——
    def _q_档案(self, user_id, msg=""):
        """档案直读：公历（或农历）出生信息 + 农历/阴历问法追加农历日期。

        F1 增强：
        - 分钟：birth_minute 非空 → 1999年5月13日10:55（并入时分，小时不丢）；
          仅小时 → 1999年5月13日10时；均无 → 1999年5月13日。
        - 农历/阴历问法（msg 含 农历/阴历）：公历档案用 lunar-python 转换
          出生日期为农历追加（同 src/engines/bazi.py 排盘引擎同库同口径）；
          农历档案（calendar=lunar）存的就是农历，主日期直接标注"农历"。
        - 干支不引入：公历/农历 + 时辰即可（PM 非技术背景，干支=绕弯）。
        - 无档案：明确答"查不到"并引导建档，不绕弯。
        """
        p = self.person_dao.get_default_person(user_id)
        if not p:
            return ("还没有你的档案，告诉我出生年月日时（精确到几点几分）、"
                    "出生地和性别，我帮你建档。")
        gender = p.get('gender')
        if not gender or gender == "unknown":
            gender = "性别未知"  # 'unknown' 是 truthy，'or' 会被绕过，需显式判断
        y, m, d = p.get('birth_year'), p.get('birth_month'), p.get('birth_day')
        if not (y and m and d):
            return ("你的档案里还没有完整出生信息，告诉我出生年月日时"
                    "（精确到几点几分）、出生地和性别，我帮你建档。")
        # F1 补强（批次 2 B3-27）：birth_hour/birth_minute 假定 int——DB 脏数据
        # （"5" 字符串 / None / "未知"）会让下方 :02d 抛 TypeError；异常冒泡到
        # direct_query 的大 try 会把 所有类目直读 一并禁用（异常兜底连坐）。
        # 归一化为 int 并写回 p（fail-open：非数字/缺失 → None，按「无时分」展示）；
        # _lunar_birth_text(p) 复用归一化后的值，str 时分不再拖垮农历转换。
        for k in ("birth_hour", "birth_minute"):
            v = p.get(k)
            if v is None or isinstance(v, int):
                continue
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = None
            p[k] = v
        hour, minute = p.get('birth_hour'), p.get('birth_minute')
        if minute is not None:  # 分钟非空 → 并入时分（10:55）
            time_part = f"{hour}:{minute:02d}" if hour is not None else f"{minute}分"
        elif hour is not None:
            time_part = f"{hour}时"
        else:
            time_part = ""
        is_lunar_archive = p.get("calendar") == "lunar"
        date_part = f"{'农历' if is_lunar_archive else ''}{y}年{m}月{d}日{time_part}"
        body = f"你的档案（命主：{p.get('name')}）：{date_part}"
        # 农历/阴历问法 → 追加农历日期；农历档案主日期已标注"农历"，不重复追加
        if ("农历" in msg or "阴历" in msg) and not is_lunar_archive:
            lunar_txt = self._lunar_birth_text(p)
            if lunar_txt:
                body += f" · {lunar_txt}"
        body += f" · 出生地{p.get('city') or '未填'} · {gender}"
        return body

    def _lunar_birth_text(self, p):
        """公历出生日期 → 农历文本（如 农历三月廿八；闰月 → 闰四月）。

        与排盘引擎同用 lunar-python（Solar.fromYmdHms(...).getLunar()，
        src/engines/bazi.py:537 同款调用），晚子时/真太阳时不涉及（出生日期
        不跨日）。农历月名用 _LUNAR_MONTH_CN（冬月/腊月口径与 paipan 一致）。
        """
        y, m, d = p.get('birth_year'), p.get('birth_month'), p.get('birth_day')
        if not (y and m and d):
            return None
        try:
            from lunar_python import Solar
            lunar = Solar.fromYmdHms(int(y), int(m), int(d),
                                     p.get('birth_hour') or 0,
                                     p.get('birth_minute') or 0, 0).getLunar()
        except Exception:
            logger.warning("农历转换失败 user birth=%s-%s-%s", y, m, d)
            return None
        month = lunar.getMonth()  # 闰月为负（lunar-python 口径）
        if month < 0:
            month_text = "闰" + _LUNAR_MONTH_CN[abs(month) - 1]
        else:
            month_text = _LUNAR_MONTH_CN[month - 1]
        return f"农历{month_text}{lunar.getDayInChinese()}"

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
                # k40（T054 同族）：空态不得 return None 把正常链路吞回 LLM
                # （对照 `_q_档案` 写法：确定性文案、零 LLM 零工具、含关键字面）
                return "你还没有收藏过灵签。在「灵签」页抽一支，我就能帮你回看啦～"
            # D4 修复：qian_saves 只存 (no, kind, drawn_at)，签诗/吉凶须从签文库
            # 按 no 反查（与 /api/qian/history 同口径，防编造）。
            # k32（A2）根因修复：反查必须**按签种**取卡——三签种各有独立签诗表，
            # 同号异 kind 内容不同（观音#7 ≠ 关帝#7 ≠ 原版#7）。此前只查
            # QIAN_BY_NO（原版 8 支）→ 观音/关帝/玄武山收藏一律串成原版签诗。
            # 单一事实源：签卡只从 QIAN_KINDS 取（与 /api/qian/history 同一实现）。
            try:
                from src.api.qian import KIND_NAMES, QIAN_KINDS
            except Exception:
                KIND_NAMES, QIAN_KINDS = {}, {}
            parts = [f"收藏的签：{len(saves)} 支"]
            for s in saves[:10]:
                no, kind = s.get("no"), (s.get("kind") or "original")
                entry = None
                if no is not None:
                    entry = next((c for c in QIAN_KINDS.get(kind) or ()
                                  if c.get("no") == no), None)
                    if entry is None:
                        # 未知/脏签种（历史直写行）→ 回退原版签卡（不崩不丢条数）
                        entry = next((c for c in QIAN_KINDS.get("original") or ()
                                      if c.get("no") == no), None)
                # 非原版签种加签种前缀（同号异 kind 并存时可区分，防张冠李戴）
                head = (f"{KIND_NAMES[kind]}第{no}签" if kind != "original"
                        and kind in KIND_NAMES else f"第{no}签")
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
            # k40（T054）：空态 return None 会让直读链整体放弃 → 落 LLM（上轮
            # 是 record_lookup 工具 + 道歉，本轮连工具都没发，回复被兜底成
            # 「抱歉，我还在学习中…」，两种情况都没有「名笺」字面）。对齐
            # `_q_档案` 写法：确定性如实告知（零 LLM 零工具、含关键字面、
            # 不编造「青木笺」类假内容）。
            return "你还没有保存过名笺。在「名笺」页生成一张，我就能帮你回看啦～"
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
