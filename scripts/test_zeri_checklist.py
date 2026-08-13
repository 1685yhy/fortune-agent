"""择吉日 Task 3 — 办事清单管线（6场景模板 + LLM定制 + 免费/会员切分）测试。

覆盖（hermetic·离线, 无 LLM/无网络, mock deepseek_anthropic_completion）:
1. 6 场景模板完整性: 每场景 8-12 项、覆盖 3 个 stage、core 恰好 5 项、
   其中恰 1 项"当天+吉时"开工项; 文案红线: 无"保证/承诺/一定/包过"等承诺词
2. customize_checklist: mock LLM 返回增删 → 断言合并
   （core 5 项全保留、新增项进入、被删的非 core 项消失、stage 合法）
3. customize_checklist: mock LLM 抛异常 → 降级返回模板原样（绝不空）
4. customize_checklist: mock LLM 返回非 JSON 垃圾 → 模板原样
5. customize_checklist: mock LLM 返回空数组 → 模板原样（绝不空）
6. 红线: 断言发给 LLM 的提示词含红线约束（外部机构只通用提醒/不给承诺 +
   不删 core + ≤14 项 + JSON 数组格式）
7. 鲁棒解析: 代码围栏 + 尾逗号 + 内嵌文本 → 正常解析
8. LLM 删掉 core 项 → 防御性合并回; LLM 返回超 14 项 → 截断且 core 全保留
9. build_checklist(free) → 5 项 core + plan_type=free; member → 全量 + plan_type=member
10. free_items 排序: 提前3天 → 提前1天 → 当天
11. build_checklist 未知场景 → ValueError

用法:
    cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_zeri_checklist.py
退出码: 0 = 全部通过; 1 = 有失败项
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.engines.zeri_checklist as zc

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------

class FakeLLM:
    """记录调用参数、可配置返回/抛异常的 LLM 桩。"""

    def __init__(self, result="", exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0
        self.last_messages = None
        self.last_kwargs = None

    def __call__(self, api_key, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        self.last_kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return self.result


class _PatchLLM:
    """上下文管理器: 替换模块内 LLM 引用并恢复。"""

    def __init__(self, fake):
        self.fake = fake
        self.orig = None

    def __enter__(self):
        self.orig = zc.deepseek_anthropic_completion
        zc.deepseek_anthropic_completion = self.fake
        return self.fake

    def __exit__(self, *exc):
        zc.deepseek_anthropic_completion = self.orig


class _patch_api_key:
    """上下文管理器: 临时设置 DEEPSEEK_API_KEY 并恢复。"""

    def __enter__(self):
        self.orig = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        return self

    def __exit__(self, *exc):
        if self.orig is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = self.orig


def template_texts(items):
    return [it["text"] for it in items]


def find_by_text(items, text):
    for it in items:
        if it["text"] == text:
            return it
    return None


# ---------------------------------------------------------------------------
# 1. 6 场景模板完整性
# ---------------------------------------------------------------------------
print("== 1. 6 场景模板完整性 ==")
SCENES = ["嫁娶", "搬家", "开业", "出行", "提车", "签约"]
check("模板覆盖 6 场景", set(zc.CHECKLIST_TEMPLATES.keys()) == set(SCENES),
      f"got {list(zc.CHECKLIST_TEMPLATES.keys())}")
PROMISE_WORDS = ["保证", "承诺", "一定", "包过", "绝对"]
for scene in SCENES:
    tpl = zc.CHECKLIST_TEMPLATES.get(scene)
    check(f"[{scene}] 模板存在", isinstance(tpl, list) and len(tpl) > 0)
    if not tpl:
        continue
    n = len(tpl)
    check(f"[{scene}] 8-12 项(实 {n})", 8 <= n <= 12, f"n={n}")
    stages = {it["stage"] for it in tpl}
    check(f"[{scene}] 覆盖 3 个 stage", stages == {"提前3天", "提前1天", "当天"},
          f"got {stages}")
    bad_stage = [it for it in tpl if it["stage"] not in zc.STAGES]
    check(f"[{scene}] 无非法 stage", not bad_stage, f"got {bad_stage}")
    empty = [it for it in tpl if not (it.get("text") or "").strip()]
    check(f"[{scene}] 无空文案", not empty)
    core = [it for it in tpl if it.get("core")]
    check(f"[{scene}] core 恰好 5 项(实 {len(core)})", len(core) == 5,
          f"core={[it['text'] for it in core]}")
    jishi_core = [it for it in core if it["stage"] == "当天" and "吉时" in it["text"]]
    check(f"[{scene}] core 含 1 项当天吉时开工项", len(jishi_core) == 1,
          f"got {[it['text'] for it in jishi_core]}")
    long_texts = [it["text"] for it in tpl if len(it["text"]) > 20]
    check(f"[{scene}] 文案均 ≤20 字", not long_texts, f"got {long_texts}")
    promised = [it["text"] for it in tpl
                if any(w in it["text"] for w in PROMISE_WORDS)]
    check(f"[{scene}] 无承诺词({','.join(PROMISE_WORDS)})", not promised,
          f"got {promised}")
    texts = [it["text"] for it in tpl]
    check(f"[{scene}] 文案无重复", len(set(texts)) == len(texts))
    # 外部机构类(车管所/银行/物业/不动产/水电气)只写通用提醒: 必含动作词
    for it in tpl:
        if any(kw in it["text"] for kw in ["车管所", "银行", "物业", "不动产", "水电气"]):
            has_action = any(kw in it["text"] for kw in ["预约", "准备", "确认", "联系", "核对", "登记"])
            check(f"[{scene}] 外部机构项为通用提醒: {it['text']}",
                  has_action, f"text={it['text']}")

# ---------------------------------------------------------------------------
# 2. customize_checklist: mock LLM 返回增删 → 合并
# ---------------------------------------------------------------------------
print("== 2. customize_checklist LLM 增删 → 合并 ==")
_tpl = zc.CHECKLIST_TEMPLATES["搬家"]
fake = FakeLLM(json.dumps([
    {"stage": "提前3天", "text": "联系搬家公司确认车型费用"},     # 保留 core
    {"stage": "提前3天", "text": "打包分类,贵重物品随身"},        # 保留非 core
    {"stage": "提前1天", "text": "水电气暖预约过户更名"},         # 保留 core
    {"stage": "提前1天", "text": "预约新小区物业交接手续"},       # 新增
    {"stage": "当天", "text": "吉时9-11点 主家具先行入宅"},       # 保留 core
    {"stage": "当天", "text": "7点前厨房米面入宅,米缸进财"},      # 保留 core
    {"stage": "当天", "text": "物业登记,门锁换新,清点物品"},      # 保留 core
    {"stage": "当天", "text": "吉时前放响鞭炮开灯暖宅"},          # 新增
], ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl, context_text="用户说家里人多,注意老人小孩")
check("调用 LLM 1 次", fake.calls == 1, f"calls={fake.calls}")
check("calls 传 5 个 core 全保留",
      all(find_by_text(got, t) for t in
          ["联系搬家公司确认车型费用", "水电气暖预约过户更名",
           "吉时9-11点 主家具先行入宅", "7点前厨房米面入宅,米缸进财",
           "物业登记,门锁换新,清点物品"]),
      f"got={template_texts(got)}")
check("被删的非 core 项消失(宽带迁移预约)",
      not find_by_text(got, "宽带迁移预约"), f"got={template_texts(got)}")
new_items = ["预约新小区物业交接手续", "吉时前放响鞭炮开灯暖宅"]
check("LLM 新增 2 项进入", all(find_by_text(got, t) for t in new_items),
      f"got={template_texts(got)}")
check("全部 stage 合法",
      all(it["stage"] in zc.STAGES for it in got),
      f"got={[it['stage'] for it in got]}")
check("新增项 core=False",
      all(not find_by_text(got, t).get("core") for t in new_items))
check("结果总项数 = 8 (5core + 新增2 + 保留非core 1)", len(got) == 8,
      f"n={len(got)} texts={template_texts(got)}")

# ---------------------------------------------------------------------------
# 3. customize_checklist: mock LLM 抛异常 → 降级模板原样
# ---------------------------------------------------------------------------
print("== 3. LLM 抛异常 → 降级模板原样 ==")
fake = FakeLLM(exc=RuntimeError("upstream timeout"))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("调用 LLM 1 次", fake.calls == 1)
check("降级 = 模板原样", template_texts(got) == template_texts(_tpl),
      f"got={template_texts(got)}")
check("core 标记原样", [it["core"] for it in got] == [it["core"] for it in _tpl])
check("不返回空", len(got) > 0)

# 无 api_key（DEEPSEEK 与 ANTHROPIC 均缺）→ 不调 LLM, 直接模板原样
fake = FakeLLM()
_orig_key = os.environ.pop("DEEPSEEK_API_KEY", None)
_orig_key2 = os.environ.pop("ANTHROPIC_API_KEY", None)
try:
    with _PatchLLM(fake):
        got = zc.customize_checklist("搬家", _tpl)
finally:
    if _orig_key is not None:
        os.environ["DEEPSEEK_API_KEY"] = _orig_key
    if _orig_key2 is not None:
        os.environ["ANTHROPIC_API_KEY"] = _orig_key2
check("无 api_key 不调 LLM", fake.calls == 0, f"calls={fake.calls}")
check("无 api_key 仍返回模板", template_texts(got) == template_texts(_tpl))

# ---------------------------------------------------------------------------
# 4. customize_checklist: 非 JSON 垃圾 → 模板原样
# ---------------------------------------------------------------------------
print("== 4. LLM 返回垃圾 → 模板原样 ==")
fake = FakeLLM("好的,以下是您的清单: 提前3天 搬家公司…(无JSON)")
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("垃圾返回 → 模板原样", template_texts(got) == template_texts(_tpl),
      f"got={template_texts(got)}")

# ---------------------------------------------------------------------------
# 5. customize_checklist: 空数组 → 模板原样（绝不空）
# ---------------------------------------------------------------------------
print("== 5. LLM 返回空数组 → 模板原样 ==")
fake = FakeLLM("[]")
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("空数组 → 模板原样", template_texts(got) == template_texts(_tpl),
      f"got={template_texts(got)}")

# ---------------------------------------------------------------------------
# 6. 红线: 提示词含红线约束
# ---------------------------------------------------------------------------
print("== 6. 红线: 提示词含约束 ==")
fake = FakeLLM(json.dumps([], ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    zc.customize_checklist("提车", zc.CHECKLIST_TEMPLATES["提车"], context_text="着急提车")
sys_prompt = fake.last_messages[0]["content"]
user_prompt = fake.last_messages[1]["content"]
check("消息结构 system+user", len(fake.last_messages) == 2
      and fake.last_messages[0]["role"] == "system")
for kw in ["通用提醒", "承诺", "不删", "core", "14", "JSON"]:
    check(f"系统提示含「{kw}」", kw in sys_prompt, f"sys={sys_prompt[:200]}")
check("用户提示含场景与模板", "提车" in user_prompt and "当前模板" in user_prompt)
check("用户提示含对话上下文", "着急提车" in user_prompt)
check("max_tokens/timeout 合理", fake.last_kwargs.get("max_tokens", 0) >= 1000
      and fake.last_kwargs.get("timeout", 0) > 0,
      f"kwargs={fake.last_kwargs}")

# ---------------------------------------------------------------------------
# 7. 鲁棒解析: 代码围栏 + 尾逗号 + 内嵌文本
# ---------------------------------------------------------------------------
print("== 7. 鲁棒解析 ==")
raw = "```json\n好的,定制结果如下:\n[\n" \
      "  {\"stage\": \"提前3天\", \"text\": \"联系搬家公司确认车型费用\",},\n" \
      "  {\"stage\": \"提前1天\", \"text\": \"水电气暖预约过户更名\"},\n" \
      "  {\"stage\": \"当天\", \"text\": \"吉时9-11点 主家具先行入宅\"},\n" \
      "]\n```"
fake = FakeLLM(raw)
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("围栏+尾逗号+内嵌文本 解析成功",
      len(got) == 5 and all(find_by_text(got, t) for t in
          ["联系搬家公司确认车型费用", "水电气暖预约过户更名", "吉时9-11点 主家具先行入宅"]),
      f"got={template_texts(got)}")
check("其余 core 防御性合并回", all(find_by_text(got, t) for t in
      ["7点前厨房米面入宅,米缸进财", "物业登记,门锁换新,清点物品"]),
      f"got={template_texts(got)}")

# 非法 stage / 缺 text 的项被丢弃
fake = FakeLLM(json.dumps([
    {"stage": "明年", "text": "非法阶段项"},
    {"stage": "提前3天", "text": ""},
    {"stage": "提前3天", "text": "联系搬家公司确认车型费用"},
], ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("非法 stage/空 text 丢弃", not find_by_text(got, "非法阶段项"),
      f"got={template_texts(got)}")

# ---------------------------------------------------------------------------
# 8. LLM 删 core → 防御合并回; 超 14 项 → 截断且 core 全保留
# ---------------------------------------------------------------------------
print("== 8. core 保护与 14 项上限 ==")
fake = FakeLLM(json.dumps([
    {"stage": "提前3天", "text": "新增A"},
    {"stage": "提前1天", "text": "新增B"},
], ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("LLM 删光原项 → core 5 项全合并回",
      len([it for it in got if it.get("core")]) == 5
      and find_by_text(got, "新增A") and find_by_text(got, "新增B"),
      f"got={template_texts(got)}")

extra = [{"stage": "提前3天", "text": f"追加事项{i:02d}"} for i in range(20)]
fake = FakeLLM(json.dumps([
    {"stage": "提前3天", "text": "联系搬家公司确认车型费用"},
    {"stage": "提前1天", "text": "水电气暖预约过户更名"},
    {"stage": "当天", "text": "吉时9-11点 主家具先行入宅"},
    {"stage": "当天", "text": "7点前厨房米面入宅,米缸进财"},
    {"stage": "当天", "text": "物业登记,门锁换新,清点物品"},
] + extra, ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("超 14 项 → 截断到 14", len(got) == 14, f"n={len(got)}")
check("截断后 core 5 项全保留",
      len([it for it in got if it.get("core")]) == 5,
      f"core={[it['text'] for it in got if it.get('core')]}")

# ---------------------------------------------------------------------------
# 9. build_checklist: free → 5 项 core; member → 全量
# ---------------------------------------------------------------------------
print("== 9. build_checklist 免费/会员切分 ==")
fake = FakeLLM(exc=RuntimeError("timeout"))  # LLM 失败 → 模板原样, 只测切分
with _PatchLLM(fake), _patch_api_key():
    items, plan_type = zc.build_checklist("开业", is_member=False)
check("free plan_type=free", plan_type == "free", f"got {plan_type}")
check("free → 5 项", len(items) == 5, f"n={len(items)}")
check("free → 全 core", all(it["core"] for it in items))
with _PatchLLM(fake), _patch_api_key():
    items, plan_type = zc.build_checklist("开业", is_member=True)
check("member plan_type=member", plan_type == "member", f"got {plan_type}")
check("member → 全量 9 项", len(items) == len(zc.CHECKLIST_TEMPLATES["开业"]),
      f"n={len(items)}")
check("member → 含非 core 项",
      any(not it["core"] for it in items))

# ---------------------------------------------------------------------------
# 10. free_items 排序: 提前3天 → 提前1天 → 当天
# ---------------------------------------------------------------------------
print("== 10. free_items 排序 ==")
shuffled = [
    {"stage": "当天", "text": "B", "core": True},
    {"stage": "提前1天", "text": "C", "core": True},
    {"stage": "提前3天", "text": "A", "core": True},
    {"stage": "提前1天", "text": "x", "core": False},
    {"stage": "当天", "text": "D", "core": True},
]
got = zc.free_items(shuffled)
check("只留 core", len(got) == 4 and all(it["core"] for it in got))
check("按 stage 排序", [it["stage"] for it in got]
      == ["提前3天", "提前1天", "当天", "当天"], f"got={got}")
check("free_items 不改原列表", len(shuffled) == 5)

# ---------------------------------------------------------------------------
# 11. build_checklist 未知场景 → ValueError
# ---------------------------------------------------------------------------
print("== 11. 未知场景 ==")
try:
    zc.build_checklist("打牌", is_member=False)
    check("未知场景抛 ValueError", False, "未抛异常")
except ValueError:
    check("未知场景抛 ValueError", True)

# ---------------------------------------------------------------------------
# 12. LLM 返回错误类型(text 非字符串/入参非字符串) → 丢弃不崩溃, 全无效则模板原样
# ---------------------------------------------------------------------------
print("== 12. 错误类型 LLM 输出 → 降级不崩溃 ==")
# 混合: 一项 text 为数字(错误类型), 一项为合法字符串 → 不崩溃, 合法项保留
fake = FakeLLM(json.dumps([
    {"stage": "当天", "text": 123},
    {"stage": "提前1天", "text": "真条目"},
], ensure_ascii=False))
crashed = False
with _PatchLLM(fake), _patch_api_key():
    try:
        got = zc.customize_checklist("搬家", _tpl)
    except Exception:
        crashed = True
        got = []
check("错误类型 text 不崩溃", not crashed, f"crashed={crashed}")
check("错误类型项被丢弃, 有效项保留", find_by_text(got, "真条目") is not None
      and not any(isinstance(it["text"], int) for it in got),
      f"got={template_texts(got)}")
# 全无效: 只有错误类型项 → 解析为空 → 模板原样（绝不空）
fake = FakeLLM(json.dumps([
    {"stage": "当天", "text": True},
    {"stage": "提前1天", "text": 0},
], ensure_ascii=False))
with _PatchLLM(fake), _patch_api_key():
    got = zc.customize_checklist("搬家", _tpl)
check("全错误类型 → 模板原样(绝不空)", template_texts(got) == template_texts(_tpl),
      f"got={template_texts(got)}")
# 原始入参非字符串（dict/list/数字/布尔）→ _parse_items 直接返回 None
check("_parse_items 非字符串入参 → None",
      zc._parse_items(123) is None and zc._parse_items(True) is None
      and zc._parse_items({"a": 1}) is None and zc._parse_items(["x"]) is None)

print()
print(f"结果: {_PASS} passed, {_FAIL} failed")
sys.exit(0 if _FAIL == 0 else 1)
