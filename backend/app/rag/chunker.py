"""结构感知切分器:把解析得到的 Section 列表切成检索块(chunk)。

策略:
1. 尽量以"标题层级边界"为切分点(结构感知),保留标题路径作为上下文;
2. 单块超过 chunk_max_chars 时按句子/字符硬切,并带重叠;
3. 每个 chunk 输出: text, heading_path(溯源), page, index。
"""
from __future__ import annotations

import re

from ..core.config import settings
from .parsers import Section

_SENT_END = re.compile(r"(?<=[。！？!?；;.])\s*")
_CHINESE_NO_END = re.compile(r"[，。；、！？：,.!?;:]$")


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_END.split(text)
    return [p for p in parts if p.strip()]


def chunk_sections(sections: list[Section]) -> list[dict]:
    """返回 [{index, heading_path, page, text}],text 已拼入标题前缀便于检索。"""
    max_chars = settings.chunk_max_chars
    overlap = settings.chunk_overlap_chars
    chunks: list[dict] = []
    idx = 0

    for sec in sections:
        heading = (sec.heading_path or "").strip()
        text = (sec.text or "").strip()
        if not text:
            continue

        # 标题本身若很简短且无正文,直接作为独立小块(如 PDF 中的标题行)
        if len(text) <= 80 and heading and text in heading:
            continue  # 标题已含在 heading_path 前缀中,避免重复

        prefix = f"{heading}\n" if heading else ""
        # 若正文极短(单句标题式),整块输出
        if len(text) <= max_chars:
            body = (prefix + text).strip()
            if body:
                chunks.append({
                    "index": idx,
                    "heading_path": heading,
                    "page": sec.page,
                    "text": body,
                })
                idx += 1
            continue

        # 长文本:按句子聚合到 <= max_chars;超长句子再按字符切
        sentences = _split_sentences(text)
        buf = ""
        for sent in sentences:
            if len(sent) > max_chars:
                # 先把缓冲中的句子落盘
                if buf:
                    body = (prefix + buf).strip()
                    if body:
                        chunks.append({
                            "index": idx, "heading_path": heading, "page": sec.page, "text": body,
                        })
                        idx += 1
                    buf = ""
                # 超长句子按字符切,带重叠
                start = 0
                step = max_chars - overlap
                while start < len(sent):
                    low = max(start - overlap, 0) if start > 0 else 0
                    piece = sent[low:low + max_chars]
                    body = (prefix + piece).strip()
                    if body:
                        chunks.append({
                            "index": idx, "heading_path": heading, "page": sec.page, "text": body,
                        })
                        idx += 1
                    start += step
                continue
            # 常规句子:并入缓冲
            if buf and len(buf) + len(sent) + 1 > max_chars:
                body = (prefix + buf).strip()
                if body:
                    chunks.append({
                        "index": idx, "heading_path": heading, "page": sec.page, "text": body,
                    })
                    idx += 1
                # 重叠:保留上一块末尾若干字符
                buf = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf += sent
            if not _CHINESE_NO_END.search(sent):
                buf += " "
        if buf:
            body = (prefix + buf).strip()
            if body:
                chunks.append({
                    "index": idx, "heading_path": heading, "page": sec.page, "text": body,
                })
                idx += 1
    return chunks
