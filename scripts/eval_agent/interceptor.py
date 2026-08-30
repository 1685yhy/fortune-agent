"""L1 工具调用拦截器（E2，测试侧 monkeypatch，生产零改动）。

红线：src/bot/tool_calls.py 零改动。本模块只在测试侧包装工具调度入口
`MessageHandler._execute_tool_call`——生产主链工具执行的唯一漏斗
（handler.py 1456/1532 两处调用点均经此方法；cap.executor 仅由
_run_with_timeout ← _execute_tool_call 触发，grep 全仓无其他分派点），
记录每次实际工具调用的 `(tool_name, params)` 序列，按轮次索引分组。

- 工具名归一：注册表中文名（如「合婚」）→ 英文 cap_id（hehun），与评估集
  expected_tools.name（cap_id 口径，src/bot/capability_registry.py 事实源）
  对齐；未知名原样透传（比对自然失败，不隐式吞掉）
- params 原样记录：结构化工单（params_obj dict）→ dict；文本标签 → str。
  比对层按契约三档处理（exact=全等 / partial=期望键都在 / any=只看工具名）
- 恢复无残留：context manager / finally 还原原始方法；幂等安装与恢复
  （重复 restore 安全；双实例并发安装由类哨兵属性防住）
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# 哨兵：类属性标记「已安装」（防两个 recorder 实例互相覆盖还原）
_SENTINEL_ATTR = "_eval_l1_interceptor_installed"

# 懒加载缓存：中文工具名 → 英文 cap_id（评估集契约口径）
_NAME_TO_ID_CACHE = None


def _name_to_id_map():
    """注册表中文名 → cap_id 反投影（懒加载；capability_registry 为唯一事实源）。"""
    global _NAME_TO_ID_CACHE
    if _NAME_TO_ID_CACHE is None:
        from src.bot.capability_registry import CAPABILITIES
        _NAME_TO_ID_CACHE = {c.name: c.cap_id for c in CAPABILITIES
                             if c.cap_type == "tool"}
    return _NAME_TO_ID_CACHE


def canonical_tool_name(name: str) -> str:
    """工具名归一为注册表 cap_id（评估集 expected_tools.name 口径）。

    中文名（「合婚」）→ cap_id（hehun）；已是 cap_id / 未知名 → 原样。
    """
    return _name_to_id_map().get(name, name)


class ToolCallRecorder:
    """按轮次分组的工具调用记录器。

    用法：
        rec = ToolCallRecorder()
        with rec:                       # install + 清空 + 恢复（无残留）
            handler = build_handler(...)
            for i, turn in enumerate(turns):
                rec.begin_turn()
                handler.process(turn["text"], user_id, session_id=sid)
        rec.turns()                     # [[(cap_id, params), ...], ...] 按轮分组
        rec.flat_calls()                # 摊平序列
    """

    def __init__(self):
        self._original = None
        self._target_cls = None
        self._installed = False
        self._turn_buckets = []   # list[list[(cap_id, params)]]
        self._current = None

    # ---------------------------------------------------------- 记录 API
    def begin_turn(self):
        """开启新一轮次分组（每轮 process() 调用前调用一次）。"""
        self._current = []
        self._turn_buckets.append(self._current)

    def turns(self):
        """按轮次索引分组的调用序列：[[(cap_id, params), ...], ...]。"""
        return [list(b) for b in self._turn_buckets]

    def flat_calls(self):
        """摊平为单序列（跨轮次按顺序），供顺序敏感比对使用。"""
        return [c for b in self._turn_buckets for c in b]

    def reset(self):
        """清空已记录调用（保持安装状态）。"""
        self._turn_buckets = []
        self._current = None

    # ---------------------------------------------------------- 安装/恢复
    def install(self, target_cls=None):
        """包装目标类（默认 src.bot.handler.MessageHandler）的 _execute_tool_call。

        - 幂等：已安装（含其他 recorder 实例已装）时 raise，防互相覆盖
        - 参数签名与生产调用点一致：(name, params, user_id, user_question="")
        """
        if self._installed:
            return
        if target_cls is None:
            from src.bot.handler import MessageHandler
            target_cls = MessageHandler
        if getattr(target_cls, _SENTINEL_ATTR, False):
            raise RuntimeError(
                "ToolCallRecorder 重复安装：另一实例已包装 "
                f"{target_cls.__name__}._execute_tool_call（先 restore 再安装）")
        self._original = target_cls._execute_tool_call
        self._target_cls = target_cls
        recorder = self

        def _wrapped(obj, name, params, user_id, user_question=""):
            recorder._record(name, params)
            return recorder._original(obj, name, params, user_id,
                                      user_question=user_question)

        target_cls._execute_tool_call = _wrapped
        setattr(target_cls, _SENTINEL_ATTR, True)
        self._installed = True

    def restore(self):
        """还原原始方法（幂等；未安装时无操作）。任何路径 finally 兜底。"""
        if self._installed and self._target_cls is not None:
            self._target_cls._execute_tool_call = self._original
            delattr(self._target_cls, _SENTINEL_ATTR)
            self._installed = False
            self._target_cls = None
            self._original = None

    # ---------------------------------------------------------- context
    def __enter__(self):
        """进入：仅清空历史。安装由调用方显式 install(target_cls)（默认
        MessageHandler），__exit__ 一律恢复——生命周期由 context 兜底。"""
        self.reset()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.restore()
        return False

    # ---------------------------------------------------------- 内部
    def _record(self, name, params):
        """记录一次实际工具调用：名字归一为 cap_id；params 原样。"""
        if self._current is None:
            self.begin_turn()  # 兜底：未显式 begin_turn 时自建首个分组
        self._current.append((canonical_tool_name(name), params))
