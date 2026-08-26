"""dash/slash 年份格式回归测试（D 类生产 bug，2026-08-26 修复）。

背景：commit 2da3396（2026-07-21）AI-native 重写 `_extract_bazi_info` 时
丢失 dash/slash 格式年份支持——用户发「1990-05-20 15:00 深圳 女」返回 None
无法排盘，直接伤害生产。本文件与 handler.py 的 Step-1/Step-2 正则修复
同 commit，作为回归防护。

修复点：src/bot/handler.py `_extract_bazi_info`
  - Step-1 年正则追加 `(\d{4})\s*[-/]\s*\d{1,2}` 替代项（group 5）
  - Step-2 月/日正则加 `(?<!\d)` lookbehind，防止 dash 年份被误拆
"""
import atexit
import os
import shutil
import tempfile
from unittest.mock import Mock

from src.bot.handler import MessageHandler
from src.storage.models import init_db

# 临时 DB 目录注册表（退出时清理）
_tmp_dirs = set()


def _cleanup_tmp_dirs():
    for d in _tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_tmp_dirs)


def _make_test_db_path():
    """创建带全量 schema 的真实临时 SQLite 文件。

    Handler.__init__ 会以 Path(dao.db_path) 实例化 MemberDAO/PreferenceDAO 等，
    Mock 的 db_path 属性会 TypeError，必须是已 init_db 的真实路径。
    """
    tmpdir = tempfile.mkdtemp(prefix="fortune_test_")
    db_path = os.path.join(tmpdir, "test.db")
    init_db(db_path)
    _tmp_dirs.add(tmpdir)
    return db_path


def _make_handler():
    """Helper: create a MessageHandler with all mocks."""
    mock_llm = Mock()
    mock_llm.analyze.return_value = Mock(response="分析结果")
    mock_llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
    mock_dao = Mock()
    mock_dao.db_path = _make_test_db_path()
    mock_dao.get_user_bazi.return_value = None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    return MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=Mock(),
        llm=mock_llm,
        dao=mock_dao,
        session_dao=mock_session,
    )


def test_extract_bazi_info_dash_year_format():
    """dash/slash 年份格式回归（D 类：2da3396 重写时丢失，2026-08-26 修复）。

    "1990-05-20 15:00 深圳 女" 这类完整 ISO 风格输入必须能排盘——
    Step-1 年正则支持 dash/slash 年份，Step-2 月/日正则不被年份误拆。
    """
    handler = _make_handler()
    result = handler._extract_bazi_info("1990-05-20 15:00 深圳 女")
    assert result is not None, "dash 年份格式应能提取"
    assert result == (1990, 5, 20, 15, 0, "深圳", "女")
    # slash 变体
    result2 = handler._extract_bazi_info("1990/5/20 15:00 深圳 男")
    assert result2 == (1990, 5, 20, 15, 0, "深圳", "男"), f"slash 变体提取错误: {result2}"
    # 单数位月/日（"1990-5-20"）同样支持
    result3 = handler._extract_bazi_info("1990-5-20 15:00 北京 男")
    assert result3 == (1990, 5, 20, 15, 0, "北京", "男"), f"单数位月/日提取错误: {result3}"
    # 传统「1990年」格式必须不受影响
    result4 = handler._extract_bazi_info("1990年5月20日 午时 北京 男")
    assert result4 is not None, "传统格式不应被回归破坏"
    assert result4[0:3] == (1990, 5, 20), f"传统格式提取错误: {result4}"
