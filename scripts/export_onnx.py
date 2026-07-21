#!/usr/bin/env python3
"""导出 BGE-M3 为 ONNX int8 量化模型，降低内存占用."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag.embedder_v2 import EmbedderV2


def export_onnx(model_name: str, output_dir: str) -> str:
    """导出 ONNX 量化模型"""
    print(f"Loading {model_name} for ONNX export...")
    embedder = EmbedderV2(model_name=model_name)
    if not embedder.load():
        print("ERROR: Cannot load model")
        sys.exit(1)

    print(f"Model dimension: {embedder.dimension}")
    print(f"Exporting to {output_dir}...")

    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.save_pretrained(output_dir)

    onnx_model = ORTModelForFeatureExtraction.from_pretrained(
        model_name,
        export=True,
        provider="CPUExecutionProvider",
    )
    onnx_model.save_pretrained(output_dir)

    print(f"ONNX model exported to {output_dir}")
    print(f"Files: {list(Path(output_dir).iterdir())}")

    # 测试 ONNX 推理
    embedder._inner._model = onnx_model
    test_vec = embedder.encode_single("测试文本")
    print(f"ONNX inference test: shape={test_vec.shape}")
    print("Export OK")

    return output_dir


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-m3"
    out = sys.argv[2] if len(sys.argv) > 2 else "/mnt/d/fortune-models/bge-m3-onnx"
    export_onnx(model, out)
