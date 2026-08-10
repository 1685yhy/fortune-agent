"""Chart image generation for fortune-telling results.

Bugfix: 原实现 import 包即加载全部 matplotlib 图表模块（bazi_chart / ziwei_chart /
fengshui_chart），每次聊天排盘触发一次 ~9s 的 matplotlib + 字体加载（findfont），
导致 /api/chat 慢。改为 PEP 562 惰性加载：访问具体生成器时才导入对应模块。
聊天流程实际只用 *_chart_html（jinja2 渲染），不再被 matplotlib 拖累。
"""
import importlib

_LAZY_MODULES = {
    "BaziChartGenerator": "src.images.bazi_chart",
    "ZiweiChartGenerator": "src.images.ziwei_chart",
    "FengshuiChartGenerator": "src.images.fengshui_chart",
    "ShareCardGenerator": "src.images.share_card",
}

__all__ = list(_LAZY_MODULES.keys())


def __getattr__(name):
    if name in _LAZY_MODULES:
        module = importlib.import_module(_LAZY_MODULES[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + __all__)
