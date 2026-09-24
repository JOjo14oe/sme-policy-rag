"""文档解析器:pdf / docx / txt / md / csv → 结构块列表。

输出统一为 Section 结构:
- heading_path: 标题层级路径,如 "第2章 > 2.1 概述"(用于结构感知切分与溯源)
- page: 页码或 None
- text: 该块正文
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".csv"}

# 制度/规章类文档的条款起始行(用于按条切分,提升检索精度)
_CLAUSE_START = re.compile(
    r"^(第\s*[一二三四五六七八九十百千0-9]+\s*[条章节款项]"
    r"|[0-9]+[.、)]"
    r"|[一二三四五六七八九十]+[、.])"
)


@dataclass
class Section:
    heading_path: str
    text: str
    page: int | None = None
    source: str = ""  # 原始行/块序号,辅助溯源


class ParseError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------- 文本切行小工具 ----------------
def _clean(line: str) -> str:
    return line.strip()


# ---------------- TXT / MD ----------------
def parse_text(content: str, source: str = "") -> list[Section]:
    """通用按行解析,txt 无标题结构;md 识别 # 标题。"""
    sections: list[Section] = []
    cur_heads: list[tuple[int, str]] = []
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_lines
        text = "\n".join(_clean(l) for l in cur_lines if _clean(l)).strip()
        if text:
            hp = " > ".join(t for _, t in cur_heads)
            sections.append(Section(heading_path=hp, text=text, source=source))
        cur_lines = []

    for raw in content.splitlines():
        line = raw.rstrip()
        s = line.strip()
        # 空行 = 段落边界:制度类文档常以空行分条,按段成节可显著提升检索精度
        if not s:
            if cur_lines:
                flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m and source.lower().endswith(".md"):
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            cur_heads = [h for h in cur_heads if h[0] < level]
            cur_heads.append((level, title))
            # 标题本身作为一个小节记录,便于单独检索
            sections.append(Section(heading_path=" > ".join(t for _, t in cur_heads), text=title, source=source))
            continue
        # 条款起始行(第X条/第X章/1. / 一、)也作为小节边界
        if _CLAUSE_START.match(s) and cur_lines:
            flush()
        cur_lines.append(line)
    flush()
    if not sections:
        sections.append(Section(heading_path="", text=content.strip(), source=source))
    return sections


# ---------------- DOCX ----------------
def _render_table(rows: list[list[str]], title: str = "") -> str:
    """把表格渲染成"每行 键:值"的自然语句,兼顾检索与生成:
    表头:假期类型 | 天数 | 申请要求
    行: 婚假 | 10 天 | 提供结婚证明
    → 渲染为「假期类型:婚假;天数:10 天;申请要求:提供结婚证明」
    """
    if not rows:
        return ""
    header = [(h or "").strip() for h in rows[0]]
    lines = []
    if title:
        lines.append(f"[表格]{title}")
    lines.append("表头:" + " | ".join(header))
    for row in rows[1:]:
        cells = [(c or "").strip() for c in row]
        if not any(cells):
            continue
        pairs = []
        for i, val in enumerate(cells):
            if not val:
                continue
            key = header[i] if i < len(header) and header[i] else f"列{i + 1}"
            pairs.append(f"{key}:{val}")
        if pairs:
            lines.append("行:" + ";".join(pairs))
    return "\n".join(lines)


def _iter_docx_blocks(document):
    """按正文原始顺序产出段落与表格(保持"表格插在它出现的位置",而非统一堆到文末)。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def parse_docx(data: bytes, source: str = "") -> list[Section]:
    try:
        import docx  # python-docx
    except ImportError as e:  # pragma: no cover
        raise ParseError("缺少 python-docx 依赖") from e

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ParseError(f"docx 解析失败: {e}") from e

    sections: list[Section] = []
    cur_heads: list[tuple[int, str]] = []
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_lines
        text = "\n".join(_clean(l) for l in cur_lines if _clean(l)).strip()
        if text:
            hp = " > ".join(t for _, t in cur_heads)
            sections.append(Section(heading_path=hp, text=text, source=source))
        cur_lines = []

    for block in _iter_docx_blocks(document):
        # --- 表格:独立成节(保留表头与行列结构,制度里的"天数表/标准表"可被精确检索) ---
        if hasattr(block, "rows") and hasattr(block, "columns"):
            raw_rows = [[(c.text or "").strip().replace("\n", " ") for c in row.cells]
                        for row in block.rows]
            raw_rows = [r for r in raw_rows if any(r)]
            if raw_rows:
                flush()
                hp = " > ".join(t for _, t in cur_heads)
                sections.append(Section(heading_path=hp, text=_render_table(raw_rows), source=source))
            continue

        # --- 段落 ---
        para = block
        style_name = (para.style.name if para.style else "") or ""
        txt = (para.text or "").strip()
        if not txt:
            continue
        if "heading" in style_name.lower() or "标题" in style_name:
            hm = re.search(r"(\d+)", style_name)
            flush()
            level = int(hm.group(1)) if hm else 1
            cur_heads = [h for h in cur_heads if h[0] < level]
            cur_heads.append((level, txt))
            sections.append(Section(heading_path=" > ".join(t for _, t in cur_heads), text=txt, source=source))
            continue
        cur_lines.append(txt)
    flush()

    if not sections:
        sections.append(Section(heading_path="", text=document.paragraphs and "\n".join(p.text for p in document.paragraphs[:5]) or "", source=source))
    return sections


# ---------------- PDF ----------------
def parse_pdf(data: bytes, source: str = "") -> list[Section]:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ParseError("缺少 PyMuPDF 依赖") from e

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ParseError(f"pdf 解析失败: {e}") from e

    # 第一遍:统计字体大小分布,推断正文基准字号
    font_counter: dict[float, int] = {}
    for page in doc:
        try:
            d = page.get_text("dict")
        except Exception:
            continue
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("size", 0)
                    if s > 0 and span.get("text", "").strip():
                        font_counter[s] = font_counter.get(s, 0) + 1
    body_size = max(font_counter, key=font_counter.get) if font_counter else 11.0

    def heading_level(size: float) -> int | None:
        """按字号推断标题级别:比正文大 45% 视为一级标题,大 12% 视为二级。"""
        if body_size <= 0 or size <= body_size * 1.1:
            return None
        if size >= body_size * 1.45:
            return 1
        return 2

    sections: list[Section] = []
    cur_heads: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush(page_no: int | None = None):
        nonlocal buf
        text = "\n".join(_clean(l) for l in buf if _clean(l)).strip()
        if text:
            hp = " > ".join(t for _, t in cur_heads)
            sections.append(Section(heading_path=hp, text=text, page=page_no, source=source))
        buf = []

    for pno, page in enumerate(doc, start=1):
        try:
            d = page.get_text("dict")
        except Exception:
            d = None
        if not d or not d.get("blocks"):
            t = page.get_text("text")  # 图片型页面降级为整页文本
            if t.strip():
                flush(pno)
                sections.append(Section(heading_path="", text=t.strip(), page=pno, source=source))
            continue
        for block in d["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                txt = "".join(s.get("text", "") for s in spans).strip()
                if not txt:
                    continue
                size = max((s.get("size", 0) for s in spans), default=0)
                lvl = heading_level(size)
                if lvl is not None and len(txt) <= 60:
                    flush(pno)
                    cur_heads = [h for h in cur_heads if h[0] < lvl]
                    cur_heads.append((lvl, txt))
                    sections.append(Section(
                        heading_path=" > ".join(t for _, t in cur_heads), text=txt, page=pno, source=source
                    ))
                    continue
                buf.append(txt)
        flush(pno)
    flush()

    # ---- 表格提取(PyMuPDF find_tables):制度文档常见"假期天数表/报销标准表",独立成节便于精确检索 ----
    try:
        for pno, page in enumerate(doc, start=1):
            finder = getattr(page, "find_tables", None)
            if finder is None:
                break
            try:
                tabs = finder()
            except Exception:
                continue
            for t in getattr(tabs, "tables", []) or []:
                try:
                    rows = t.extract()
                except Exception:
                    continue
                lines = []
                for row in rows:
                    cells = [(c or "").strip().replace("\n", " ") for c in row]
                    if any(cells):
                        lines.append(cells)
                if len(lines) >= 2:
                    sections.append(Section(
                        heading_path="",
                        text=_render_table(lines, title=f"(第 {pno} 页)"),
                        page=pno, source=source,
                    ))
    except Exception:
        pass

    if not sections:
        full = "\n".join(page.get_text("text") for page in doc)
        if full.strip():
            sections.append(Section(heading_path="", text=full.strip(), source=source))
    doc.close()
    return sections


# ---------------- CSV ----------------
def parse_csv(data: bytes, source: str = "") -> list[Section]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gbk", errors="replace")
    rows: list[list[str]] = []
    try:
        reader = csv.reader(io.StringIO(text))
        for r in reader:
            rows.append(r)
    except Exception as e:
        raise ParseError(f"csv 解析失败: {e}") from e
    if not rows:
        return []
    header = rows[0]
    sections: list[Section] = []
    lines: list[str] = [f"列: {' | '.join(header)}"]
    for i, row in enumerate(rows[1:], start=2):
        lines.append(" | ".join(cell.strip() for cell in row))
        if len("\n".join(lines)) >= 1200:  # 防止一整个大表变成巨块
            sections.append(Section(heading_path="", text="\n".join(lines), source=f"{source}:行2-{i}"))
            lines = []
    if lines:
        sections.append(Section(heading_path="", text="\n".join(lines), source=source))
    return sections


# ---------------- 统一入口 ----------------
def parse_file(filename: str, data: bytes) -> list[Section]:
    """按扩展名分发解析,返回结构块列表。"""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ParseError(f"不支持的文件格式: {ext}(支持: {', '.join(sorted(SUPPORTED_EXTS))})")
    try:
        if ext == ".txt":
            text = data.decode("utf-8", errors="replace")
            return parse_text(text, filename)
        if ext == ".md":
            text = data.decode("utf-8", errors="replace")
            return parse_text(text, filename)
        if ext == ".docx":
            return parse_docx(data, filename)
        if ext == ".pdf":
            return parse_pdf(data, filename)
        if ext == ".csv":
            return parse_csv(data, filename)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"解析 {filename} 出错: {e}") from e
    return []
