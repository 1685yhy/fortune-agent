"""文本切块器 - 将古籍文本切分为适合向量化的块."""
from dataclasses import dataclass, field
from typing import List
import re


@dataclass
class Chunk:
    text: str
    source: str
    author: str = ""
    category: str = "general"
    keywords: List[str] = field(default_factory=list)
    chunk_id: str = ""


def chunk_text(
    text: str,
    source: str,
    author: str = "",
    category: str = "general",
    chunk_size: int = 500,
    overlap: int = 50,
) -> List[Chunk]:
    """将文本切分为重叠的块.

    Splits text on paragraph boundaries first, then on sentence-ending
    punctuation (。！？) for long paragraphs. Chinese commas (，) are kept
    as part of the content since classical Chinese phrases are short.
    """
    # 1. 按段落分割（双换行或单换行），保留句子完整性
    paragraphs = re.split(r'\n\s*\n+|\n(?!\s*\n)', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 2. 对于长段落，按句子切分
    sentences = []
    for para in paragraphs:
        # 以句号、问号、感叹号、分号分割，但保留标点在句尾
        parts = re.split(r'(?<=[。！？；])', para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)

    # 3. 过滤过短的片段（古籍中单句通常至少5字）
    sentences = [s for s in sentences if len(s) >= 5]

    chunks = []
    current_text = ""
    chunk_idx = 0

    for sent in sentences:
        if len(current_text) + len(sent) <= chunk_size:
            current_text += sent
        else:
            if len(current_text) > 50:
                chunk = Chunk(
                    text=current_text.strip(),
                    source=source,
                    author=author,
                    category=category,
                    keywords=_extract_keywords(current_text),
                    chunk_id=f"{_safe_id(source)}_{chunk_idx:03d}",
                )
                chunks.append(chunk)
                chunk_idx += 1
            # 保留 overlap
            if overlap > 0 and len(current_text) > overlap:
                current_text = current_text[-overlap:] + sent
            else:
                current_text = sent

    # 最后一块
    if len(current_text) > 50:
        chunk = Chunk(
            text=current_text.strip(),
            source=source,
            author=author,
            category=category,
            keywords=_extract_keywords(current_text),
            chunk_id=f"{_safe_id(source)}_{chunk_idx:03d}",
        )
        chunks.append(chunk)

    return chunks


def _safe_id(source: str) -> str:
    """生成安全的ID前缀"""
    return re.sub(r'[^a-zA-Z一-鿿]', '_', source)[:20]


def _extract_keywords(text: str) -> List[str]:
    """提取关键词（简化版：提取天干地支五行等术语）"""
    keywords = set()
    important_terms = [
        "甲","乙","丙","丁","戊","己","庚","辛","壬","癸",
        "子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥",
        "金","木","水","火","土",
        "比肩","劫财","食神","伤官","正财","偏财","正官","七杀","正印","偏印",
        "日主","用神","格局","大运","流年","八字","四柱",
    ]
    for term in important_terms:
        if term in text:
            keywords.add(term)
    return list(keywords)
