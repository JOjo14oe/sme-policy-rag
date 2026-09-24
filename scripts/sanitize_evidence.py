"""提交前脱敏:把证据 JSON / 报告中的本机绝对路径替换为占位符。

为什么需要:评测与回归脚本会把本机路径(Windows 盘符路径或 Unix 用户主目录路径)
写入证据 JSON,这些路径既无信息价值,也会泄露个人/机器信息,不应进入公开仓库。

处理范围(默认):
  scripts/_verify_result.json
  scripts/_verify_productization.json
  scripts/_eval_result.json
用法:
  python scripts/sanitize_evidence.py            # 就地脱敏上述证据文件
  python scripts/sanitize_evidence.py --check    # 仅检查,发现残留路径时退出码 1
  python scripts/sanitize_evidence.py --all      # 额外扫描 docs/*.md 与 README.md

注意:本文件的说明文字刻意不包含任何真实盘符路径示例,
以免被 CI 的"个人绝对路径"检查误判(该检查扫描仓库内所有文本文件)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "<PROJECT_ROOT>"

EVIDENCE = [
    ROOT / "scripts" / "_verify_result.json",
    ROOT / "scripts" / "_verify_productization.json",
    ROOT / "scripts" / "_eval_result.json",
]
TEXT_DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]

# 匹配 Windows 绝对路径(含空格用户名)与 Unix home 路径
PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\+Users\\\\+[^\"'\s,;)\]}]+"),          # 转义后的 JSON 路径
    re.compile(r"[A-Za-z]:\\+Users\\+[^\"'\s,;)\]}]+"),              # 普通 JSON 路径
    re.compile(r"/(?:home|Users)/[A-Za-z0-9._\-]+/[^\s\"',;)\]}]*"),  # Unix 路径
]


def _sanitize_text(text: str) -> tuple[str, int]:
    """先替换项目根路径,再兜底替换其它用户目录路径。"""
    root_escaped = str(ROOT).replace("\\", "\\\\")
    root_plain = str(ROOT)
    n = 0
    for variant in (root_escaped, root_plain):
        if variant in text:
            n += text.count(variant)
            text = text.replace(variant, PLACEHOLDER)
    for pat in PATTERNS:
        text, k = pat.subn(PLACEHOLDER, text)
        n += k
    return text, n


def process(path: Path, check_only: bool) -> int:
    if not path.exists():
        return 0
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return 0                     # 二进制(如 docx)跳过
    cleaned, hits = _sanitize_text(original)
    if hits == 0:
        return 0
    if check_only:
        print(f"  ⚠ {path.relative_to(ROOT)}:发现 {hits} 处本机路径(需脱敏)")
        return hits
    path.write_text(cleaned, encoding="utf-8")
    print(f"  ✅ {path.relative_to(ROOT)}:已脱敏 {hits} 处")
    return hits


def main() -> int:
    check = "--check" in sys.argv
    scan_docs = "--all" in sys.argv
    targets = list(EVIDENCE) + (TEXT_DOCS if scan_docs else [])
    print(("[检查]" if check else "[脱敏]") + f" 目标 {len(targets)} 个文件")
    total = sum(process(p, check) for p in targets)
    if check:
        print(f"\n结果:{'发现 ' + str(total) + ' 处待处理' if total else '未发现本机路径,可安全提交'}")
        return 1 if total else 0
    print(f"\n完成:共处理 {total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
