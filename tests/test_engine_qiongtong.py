# tests/test_engine_qiongtong.py
# 穷通宝鉴 120 格查表（src/engine/cases/qiongtong_table.json）完整性测试。
# 表由 src/engine/extract/extract_qiongtong.py 从
# /mnt/d/fortune-data/books/bazi/穷通宝鉴.txt 提取产出。
import json

TABLE_PATH = "src/engine/cases/qiongtong_table.json"


def test_table_complete_120_cells():
    table = json.load(open(TABLE_PATH, encoding="utf-8"))
    assert len(table) == 10
    for gan in "甲乙丙丁戊己庚辛壬癸":
        assert gan in table
        assert len(table[gan]) == 12, f"{gan} 月格数不足"
        for month, text in table[gan].items():
            assert text.strip(), f"{gan}{month} 格为空"


def test_gold_cells():
    table = json.load(open(TABLE_PATH, encoding="utf-8"))
    # 人工核对过的 3 格（2026-08-15 从《穷通宝鉴》原文抄录首句，与原 txt 逐字比对）：
    #   甲寅 ← 原文第 22 行 "正月甲木，初春尚有余寒，得丙癸逢，富贵双全。..."
    #   庚申 ← 原文第 848-849 行 "七月庚金：/七月庚金，刚锐极矣，专用丁火煆炼，次取木引丁..."
    #   壬子 ← 原文第 1219-1220 行 "十一月壬水：/十一月壬水，阳刃帮身，较前更旺，先取戊土..."
    assert "正月甲木，初春尚有余寒，得丙癸逢，富贵双全" in table["甲"]["寅"]
    assert "七月庚金，刚锐极矣，专用丁火煆炼，次取木引丁" in table["庚"]["申"]
    assert "十一月壬水，阳刃帮身，较前更旺，先取戊土" in table["壬"]["子"]
