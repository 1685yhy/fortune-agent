"""Tests for chart image generation - verify images are generated, not content."""
import os
from pathlib import Path

from src.engines.bazi import BaziEngine
from src.engines.ziwei import ZiweiEngine
from src.engines.fengshui import FengshuiEngine
from src.images import BaziChartGenerator, ZiweiChartGenerator, FengshuiChartGenerator

CHARTS_DIR = Path("/mnt/d/fortune-data/charts")


def _cleanup(path: str):
    """Remove test file if it exists."""
    if path and os.path.exists(path):
        os.remove(path)


def test_bazi_chart_generation():
    """Test that BaziChartGenerator creates a valid PNG file."""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    gen = BaziChartGenerator()
    tmp_path = "/tmp/test_bazi_chart.png"
    _cleanup(tmp_path)

    try:
        output = gen.generate(result, output_path=tmp_path)
        assert output == os.path.abspath(tmp_path)
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(tmp_path)


def test_bazi_chart_auto_path():
    """Test that BaziChartGenerator auto-generates path when output_path is None."""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    gen = BaziChartGenerator()
    output = gen.generate(result, output_path=None)
    try:
        assert output is not None
        assert output.startswith(str(CHARTS_DIR)), f"Expected charts dir, got {output}"
        assert output.endswith(".png"), f"Expected .png, got {output}"
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(output)


def test_ziwei_chart_generation():
    """Test that ZiweiChartGenerator creates a valid PNG file."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    gen = ZiweiChartGenerator()
    tmp_path = "/tmp/test_ziwei_chart.png"
    _cleanup(tmp_path)

    try:
        output = gen.generate(result, output_path=tmp_path)
        assert output == os.path.abspath(tmp_path)
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(tmp_path)


def test_ziwei_chart_auto_path():
    """Test that ZiweiChartGenerator auto-generates path."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    gen = ZiweiChartGenerator()
    output = gen.generate(result, output_path=None)
    try:
        assert output is not None
        assert output.startswith(str(CHARTS_DIR)), f"Expected charts dir, got {output}"
        assert output.endswith(".png"), f"Expected .png, got {output}"
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(output)


def test_fengshui_chart_generation():
    """Test that FengshuiChartGenerator creates a valid PNG file."""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", year_built=2024, birth_year=1990, gender="男")

    gen = FengshuiChartGenerator()
    tmp_path = "/tmp/test_fengshui_chart.png"
    _cleanup(tmp_path)

    try:
        output = gen.generate(result, output_path=tmp_path)
        assert output == os.path.abspath(tmp_path)
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(tmp_path)


def test_fengshui_chart_auto_path():
    """Test that FengshuiChartGenerator auto-generates path."""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", year_built=2024)

    gen = FengshuiChartGenerator()
    output = gen.generate(result, output_path=None)
    try:
        assert output is not None
        assert output.startswith(str(CHARTS_DIR)), f"Expected charts dir, got {output}"
        assert output.endswith(".png"), f"Expected .png, got {output}"
        assert os.path.exists(output), "PNG file was not created"
        assert os.path.getsize(output) > 1000, "PNG file is too small"
    finally:
        _cleanup(output)


def test_fengshui_different_directions():
    """Test chart generation with different house directions."""
    engine = FengshuiEngine()
    gen = FengshuiChartGenerator()

    tmp_path = "/tmp/test_fengshui_qian.png"
    _cleanup(tmp_path)

    try:
        result = engine.analyze(direction="乾", year_built=2024)
        output = gen.generate(result, output_path=tmp_path)
        assert os.path.exists(output)
        assert os.path.getsize(output) > 1000
    finally:
        _cleanup(tmp_path)


def test_all_generators_have_generate_method():
    """All generators should have a 'generate' method with correct signature."""
    for GenClass in [BaziChartGenerator, ZiweiChartGenerator, FengshuiChartGenerator]:
        gen = GenClass()
        assert hasattr(gen, "generate"), f"{GenClass.__name__} missing generate()"
        import inspect
        sig = inspect.signature(gen.generate)
        assert "result" in sig.parameters
        assert "output_path" in sig.parameters
        assert sig.return_annotation == str or str in str(sig.return_annotation)
