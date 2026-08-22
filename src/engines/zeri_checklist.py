"""办事清单管线 (择吉日 Task 3) — 7场景模板 + LLM 定制 + 免费/会员切分。

职责:
  - CHECKLIST_TEMPLATES: 7 场景（嫁娶/搬家/开业/晋升/出行/提车/签约）办事清单模板,
    每场景 8-12 项, 覆盖 提前3天/提前1天/当天 三个阶段;
    ChecklistItem = {stage, text, core} 纯 dict（JSON 友好）;
    core=True 的 5 项为免费精简版取用（含 1 项"当天吉时开工"项）。
  - customize_checklist: LLM 结合对话上下文对模板增删条目; 任何失败/超时/解析
    失败 → 返回模板原样（降级, 绝不返回空）。
  - free_items: 只保留 core 项并按 stage 顺序排序（提前3天 → 提前1天 → 当天）。
  - build_checklist: 入口; free → 定制后取 core; member → 全量定制。

文案红线（宁缺毋滥）:
  涉及外部机构事项（车管所/银行/物业/不动产登记/水电气）只写通用提醒文案
  （预约/准备/确认/联系/核对 等动作），绝不写具体承诺（如"3天内过户成功"）。
  模板已遵守; LLM 定制时在系统提示中显式约束。
"""
import json
import logging
import os
import re

from src.llm.client import deepseek_anthropic_completion

logger = logging.getLogger(__name__)

# 阶段顺序（免费精简版排序依据）
STAGES = ["提前3天", "提前1天", "当天"]

# LLM 定制红线/约束（系统提示, 与 brief 逐条对应）
_SYSTEM_PROMPT = (
    "你是一位精通行事安排的生活助理。用户已选定吉日, 你负责对其办事清单做"
    "个性化增删。要求:\n"
    "1. 保持阶段结构: 每项的 stage 只能取「提前3天」「提前1天」「当天」三者之一;\n"
    "2. 不删 core 项: core 为 true 的条目是必做核心项, 必须全部保留;\n"
    "3. 涉及外部机构的事项（车管所/银行/物业/不动产登记/水电气）只写通用提醒文案,"
    "如「预约过户」「准备材料」「确认手续」, 绝不写具体承诺（例如不得写"
    "「保证3天过户成功」）;\n"
    "4. 总条目数不超过 14 项;\n"
    "5. 只输出一个 JSON 数组, 每项格式 "
    "{\"stage\": \"提前3天|提前1天|当天\", \"text\": \"事项文案\"}, 不要输出任何其他文字。"
)

_LLM_MODEL = "deepseek-v4-flash"
_LLM_MAX_TOKENS = 1500
_LLM_TEMPERATURE = 0.7
_LLM_TIMEOUT = 30.0
_MAX_ITEMS = 14

# ---------------------------------------------------------------------------
# 7 场景模板（文案宁缺毋滥: 每项具体、可执行、≤20 字; core 恰 5 项/场景）
# ---------------------------------------------------------------------------
CHECKLIST_TEMPLATES: dict = {
    # 嫁娶: 领证办席 —— 请柬/酒席/彩排 → 接亲/敬茶/答谢
    "嫁娶": [
        {"stage": "提前3天", "text": "发请柬并统计宾客名单", "core": True},
        {"stage": "提前3天", "text": "与酒店核对桌数菜单", "core": True},
        {"stage": "提前3天", "text": "婚纱礼服最终试穿", "core": False},
        {"stage": "提前1天", "text": "婚车路线踩点装饰", "core": False},
        {"stage": "提前1天", "text": "彩排走场对词", "core": True},
        {"stage": "提前1天", "text": "确认化妆师到位时间", "core": False},
        {"stage": "提前1天", "text": "新房布置红包喜字", "core": False},
        {"stage": "当天", "text": "吉时9-11点 婚车出发接亲", "core": True},
        {"stage": "当天", "text": "敬茶改口,午前完成", "core": False},
        {"stage": "当天", "text": "宴席开席,敬酒答谢", "core": True},
    ],
    # 搬家: 搬运/过户 → 入宅/安顿（示例: 米缸进财、主家具先行、物业登记）
    "搬家": [
        {"stage": "提前3天", "text": "联系搬家公司确认车型费用", "core": True},
        {"stage": "提前3天", "text": "确认新旧小区停车与电梯时段", "core": False},
        {"stage": "提前3天", "text": "打包分类,贵重物品随身", "core": False},
        {"stage": "提前1天", "text": "水电气暖预约过户更名", "core": True},
        {"stage": "提前1天", "text": "宽带迁移预约", "core": False},
        {"stage": "提前1天", "text": "保洁提前一天新宅除尘", "core": False},
        {"stage": "当天", "text": "7点前厨房米面入宅,米缸进财", "core": True},
        {"stage": "当天", "text": "吉时9-11点 主家具先行入宅", "core": True},
        {"stage": "当天", "text": "长者殿后进屋,进门开灯", "core": False},
        {"stage": "当天", "text": "物业登记,门锁换新,清点物品", "core": True},
    ],
    # 开业: 执照/备货 → 调试/清洁 → 揭牌开市
    "开业": [
        {"stage": "提前3天", "text": "营业执照确认并张贴", "core": True},
        {"stage": "提前3天", "text": "备货理货,陈列调整", "core": True},
        {"stage": "提前3天", "text": "促销物料设计印制", "core": False},
        {"stage": "提前1天", "text": "银行对公账户确认开通", "core": True},
        {"stage": "提前1天", "text": "设备调试收银系统测试", "core": True},
        {"stage": "提前1天", "text": "店面卫生清洁招牌点亮", "core": False},
        {"stage": "当天", "text": "吉时9-11点 揭牌开业", "core": True},
        {"stage": "当天", "text": "开业优惠,首客迎宾", "core": False},
        {"stage": "当天", "text": "财神位摆供,开市鸣炮", "core": False},
    ],
    # 晋升: 业绩/材料 → 预演/准备 → 面试谈薪
    "晋升": [
        {"stage": "提前3天", "text": "梳理业绩亮点,备齐述职/竞聘材料", "core": True},
        {"stage": "提前3天", "text": "了解竞聘岗位与评审标准", "core": True},
        {"stage": "提前3天", "text": "与领导沟通意向,争取支持", "core": False},
        {"stage": "提前1天", "text": "预演述职/面试,准备问题", "core": True},
        {"stage": "提前1天", "text": "备齐证明材料与证件", "core": True},
        {"stage": "提前1天", "text": "确认时间地点,整理着装", "core": False},
        {"stage": "当天", "text": "吉时9-11点 面试/述职/谈薪", "core": True},
        {"stage": "当天", "text": "复盘表现,记录要点", "core": False},
        {"stage": "当天", "text": "跟进结果,维护关系", "core": False},
    ],
    # 出行: 行程/证件 → 打包/值机 → 出门
    "出行": [
        {"stage": "提前3天", "text": "确认行程,预订机酒门票", "core": True},
        {"stage": "提前3天", "text": "检查证件签证是否有效", "core": True},
        {"stage": "提前3天", "text": "换汇并告知家人行程", "core": False},
        {"stage": "提前1天", "text": "行李打包,充电宝雨具备齐", "core": True},
        {"stage": "提前1天", "text": "值机选座,预约接送车辆", "core": False},
        {"stage": "提前1天", "text": "查目的地天气调整衣物", "core": False},
        {"stage": "当天", "text": "吉时7-9点 出门启程", "core": True},
        {"stage": "当天", "text": "提前2小时到机场车站", "core": True},
        {"stage": "当天", "text": "出发前关水电煤,锁好门窗", "core": False},
    ],
    # 提车: 交车/保险 → 上牌预约 → 到店验车
    "提车": [
        {"stage": "提前3天", "text": "联系4S店确认交车时间", "core": True},
        {"stage": "提前3天", "text": "确认保险方案与生效日期", "core": True},
        {"stage": "提前3天", "text": "准备身份证驾驶证购车合同", "core": False},
        {"stage": "提前1天", "text": "车管所预约上牌时间", "core": True},
        {"stage": "提前1天", "text": "查临牌办理所需材料", "core": False},
        {"stage": "提前1天", "text": "验车清单打印备用", "core": False},
        {"stage": "当天", "text": "吉时9-11点 到店提车", "core": True},
        {"stage": "当天", "text": "验车核对车架号里程", "core": False},
        {"stage": "当天", "text": "上牌贴膜,检查随车证件", "core": True},
    ],
    # 签约: 审阅/资质 → 材料/款项 → 用印
    "签约": [
        {"stage": "提前3天", "text": "合同文本逐条审阅", "core": True},
        {"stage": "提前3天", "text": "咨询律师或专业意见", "core": False},
        {"stage": "提前3天", "text": "核对双方资质证照", "core": True},
        {"stage": "提前1天", "text": "备齐身份证营业执照公章", "core": True},
        {"stage": "提前1天", "text": "确认款项支付方式与账户", "core": True},
        {"stage": "提前1天", "text": "打印合同一式多份", "core": False},
        {"stage": "当天", "text": "吉时9-11点 签约用印", "core": True},
        {"stage": "当天", "text": "核对盖章签字与日期", "core": False},
        {"stage": "当天", "text": "留存合同原件与附件", "core": False},
    ],
}


# ---------------------------------------------------------------------------
# LLM 定制
# ---------------------------------------------------------------------------

def _build_messages(scene: str, template: list, context_text: str | None) -> list:
    """构建 LLM 消息: 系统提示（红线约束）+ 用户提示（场景/模板/上下文）。"""
    tpl_json = json.dumps(
        [{"stage": it["stage"], "text": it["text"], "core": it.get("core", False)}
         for it in template],
        ensure_ascii=False, indent=1)
    user = f"场景：{scene}\n当前模板：\n{tpl_json}\n"
    if context_text:
        user += f"用户对话上下文：{context_text}\n"
    user += "请结合上下文给出定制后的清单（只输出 JSON 数组）。"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _normalize_stage(stage) -> str | None:
    """归一化 stage: 精确匹配优先; 容错包含匹配（如"提前3天(联系)"）。"""
    if not isinstance(stage, str):
        return None
    s = stage.strip()
    if s in STAGES:
        return s
    for std in STAGES:
        if std in s:
            return std
    return None


def _json_candidates(text: str) -> list:
    """按优先级产出候选 JSON 文本: 整体 → 截取首个 [ 到末个 ] 的内容。"""
    candidates = [text]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        candidates.insert(0, text[start:end + 1])
    return candidates


def _parse_items(raw: str) -> list | None:
    """鲁棒解析 LLM 返回的 JSON 数组。

    - 剥除 ```json ``` 代码围栏
    - 直接 json.loads → 失败则截取首个 [ 到末个 ] → 仍失败则容忍尾逗号
    - 逐项校验: 非 dict / 空 text / 非法 stage 的项丢弃
    - 空数组或解析失败 → 返回 None（调用方降级为模板原样, 绝不返回空）
    """
    if not isinstance(raw, str):
        return None  # 错误类型（dict/list/数字等）→ 调用方降级为模板原样
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()

    data = None
    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError:
            data = None
    if data is None:
        # 容忍尾逗号（",]"/",}" 是 LLM 常见小错误）
        for candidate in _json_candidates(text):
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
                break
            except json.JSONDecodeError:
                data = None

    if not isinstance(data, list) or not data:
        return None

    items = []
    for it in data:
        if not isinstance(it, dict):
            continue
        stage = _normalize_stage(it.get("stage"))
        if not isinstance(it.get("text"), str):
            continue  # 错误类型 text（数字/布尔等）→ 丢弃该项, 不崩溃
        item_text = (it.get("text") or "").strip()
        if stage is None or not item_text:
            continue
        items.append({"stage": stage, "text": item_text})
    return items or None


def _merge_items(template: list, llm_items: list) -> list:
    """LLM 结果与模板合并（core 保护 + 14 项上限）。

    - LLM 条目命中模板文案 → 沿用模板原条目（保留 core 标记）
    - LLM 新条目 → core=False
    - 模板 core 项被 LLM 删除 → 防御性合并回（"不删 core 项"双保险）
    - 总数 > 14 → 从尾部丢弃非 core 项
    """
    tpl_by_text = {it["text"]: it for it in template}
    result = []
    for it in llm_items:
        tpl = tpl_by_text.get(it["text"])
        if tpl is not None:
            result.append(dict(tpl))
        else:
            result.append({"stage": it["stage"], "text": it["text"], "core": False})

    have_texts = {r["text"] for r in result}
    for tpl in template:
        if tpl.get("core") and tpl["text"] not in have_texts:
            result.append(dict(tpl))

    while len(result) > _MAX_ITEMS:
        for idx in range(len(result) - 1, -1, -1):
            if not result[idx]["core"]:
                del result[idx]
                break
        else:
            break  # 全是 core（理论上不会发生）→ 停止
    return result


def customize_checklist(scene: str, template: list,
                        context_text: str | None = None,
                        api_key: str | None = None) -> list:
    """LLM 结合对话上下文对模板增删条目; 失败/超时 → 模板原样（绝不空）。

    LLM 提示词含红线约束: 保持 stage 结构、不删 core 项、外部机构只写通用提醒
    不给具体承诺、总数 ≤14、返回 JSON 数组（每项 {stage, text}）。
    """
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY") \
        or os.getenv("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return [dict(it) for it in template]  # 无密钥 → 模板原样

    try:
        raw = deepseek_anthropic_completion(
            api_key,
            _build_messages(scene, template, context_text),
            model=_LLM_MODEL,
            max_tokens=_LLM_MAX_TOKENS,
            temperature=_LLM_TEMPERATURE,
            timeout=_LLM_TIMEOUT,
        )
    except Exception as exc:  # 超时/上游错误 → 降级
        logger.warning("zeri_checklist LLM 定制失败(%s), 降级为模板原样", exc)
        return [dict(it) for it in template]

    parsed = _parse_items(raw)
    if parsed is None:
        return [dict(it) for it in template]  # 解析失败/空 → 模板原样
    return _merge_items(template, parsed)


# ---------------------------------------------------------------------------
# 免费/会员切分
# ---------------------------------------------------------------------------

def free_items(items: list) -> list:
    """只保留 core 项, 按 stage 顺序（提前3天 → 提前1天 → 当天）排序。"""
    stage_order = {s: i for i, s in enumerate(STAGES)}
    core = [dict(it) for it in items if it.get("core")]
    core.sort(key=lambda it: stage_order.get(it.get("stage"), len(STAGES)))
    return core


def build_checklist(scene: str, is_member: bool,
                    context_text: str | None = None) -> tuple:
    """入口: 生成办事清单。

    返回 (items, plan_type):
      - 免费: LLM 定制后取 core 5 项, plan_type="free"
      - 会员: LLM 全量定制, plan_type="member"
    """
    template = CHECKLIST_TEMPLATES.get(scene)
    if template is None:
        raise ValueError(f"未知场景: {scene!r}, 可选: {list(CHECKLIST_TEMPLATES.keys())}")
    customized = customize_checklist(scene, template, context_text)
    if is_member:
        return customized, "member"
    return free_items(customized), "free"
