"""Word(docx)→ Markdown 反向转换(用于从已分发成品的 docx 还原 .md 源文件)。

用法: python scripts/docx_to_md.py <输入.docx> [输出.md]
"""
from __future__ import annotations

import sys
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph


def convert(src: Path, dst: Path) -> int:
    d = docx.Document(str(src))
    lines: list[str] = []
    tables = 0
    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = p.style.name or ""
            if style.startswith("Heading"):
                try:
                    lvl = int(style.replace("Heading ", "") or 1)
                except ValueError:
                    lvl = 1
                lines.append("#" * max(1, min(6, lvl)) + " " + text)
                lines.append("")
            elif style == "List Bullet":
                lines.append("- " + text)
            elif style == "List Number":
                lines.append("1. " + text)
            else:
                lines.append(text)
                lines.append("")
        elif tag == "tbl":
            t = Table(child, d)
            rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in t.rows]
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            width = len(rows[0])
            lines.append("| " + " | ".join(rows[0]) + " |")
            lines.append("|" + "|".join(["---"] * width) + "|")
            for r in rows[1:]:
                r = (r + [""] * width)[:width]
                lines.append("| " + " | ".join(r) + " |")
            lines.append("")
            tables += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成:{dst}(段落 {len(d.paragraphs)} 个,表格 {tables} 个)")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/docx_to_md.py <输入.docx> [输出.md]")
        return 2
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"文件不存在:{src}")
        return 1
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".md")
    return convert(src, dst)


if __name__ == "__main__":
    sys.exit(main())
