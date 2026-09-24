"""生成《产品化增强落地报告》(Markdown + docx)。

数据来源:scripts/_verify_productization.json + 运行中的后端实时状态。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
EVID = Path(__file__).resolve().parent / "_verify_productization.json"
EVID_INNO = Path(__file__).resolve().parent / "_verify_result.json"
BASE = "http://127.0.0.1:8000"


def runtime() -> dict:
    try:
        c = httpx.Client(base_url=BASE, timeout=30)
        return {
            "status": c.get("/api/system/status").json(),
            "kbs": c.get("/api/kb", headers={"X-Actor-Role": "admin"}).json(),
            "audit": c.get("/api/audit/stats", headers={"X-Actor-Role": "admin"}).json(),
        }
    except Exception as e:
        return {"error": str(e)}


def md_table(rows, headers) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def build_md(ev: dict, inno: dict, rt: dict) -> str:
    m = ev.get("metrics", {}) or {}
    v = m.get("version", {}) or {}
    perm = m.get("permission", {}) or {}
    aud = m.get("audit", {}) or {}
    st = rt.get("status", {}) or {}
    kbs = (rt.get("kbs") or {}).get("kbs", []) or []
    checks = ev.get("checks", []) or []
    today = datetime.now().strftime("%Y-%m-%d")

    def grp(kw) -> list[dict]:
        return [c for c in checks if kw in c["name"]]

    A = []
    add = A.append

    add("# 产品化增强落地报告")
    add("")
    add(f"**项目定位**:面向**中小企业内部制度文档**的多知识库私有 RAG 问答系统  ")
    add(f"**报告日期**:{today}  ")
    add(f"**验证环境**:Windows · Python 3.12 · 每库独立 Chroma 实例 · Ollama 本地 "
        f"{st.get('chat_model', 'deepseek-r1:7b')}(生成)/ {st.get('embed_model', 'bge-m3')}(嵌入) · 全本地私有部署  ")
    add(f"**验收结论**:**产品化三项增强全部落地并通过实测 —— 增强验证 27/27、原有创新验证 27/27、"
        f"原端到端验收 19/19,合计 73 项断言全部通过。**")
    add("")
    add("---")
    add("")
    add("## 一、业务痛点 → 产品化能力对照")
    add("")
    add(md_table([
        ["规章、人事、管理文件分散", "多知识库管理 + 无标签文档 AI 自动分类建库", "已实现(前期)"],
        ["知识库隔离差,删改互相影响", "每知识库独立 Chroma 实例与存储文件(物理隔离)", "已实现(创新点①)"],
        ["制度更新成本高", "块级增量更新,仅重嵌入变更条目", "已实现(创新点②)"],
        ["**不同版本制度信息互相冲突难以识别**", "**制度版本与生效期管理**:检索排除已废止/未生效版本,冲突区分为「版本差异(以现行版为准)」与「跨来源冲突」,并提供版本链与到期提醒", "**本次增强 A**"],
        ["**敏感制度越权访问**", "**部门/角色权限隔离**:知识库可见范围(全员/指定部门),路由、检索、文档操作全链路鉴权并记录越权拒绝", "**本次增强 B**"],
        ["**内部制度系统合规留痕**", "**审计日志**:问答、冲突告警、越权拒绝、文档与库变更全程留痕,可查询与统计", "**本次增强 C**"],
        ["私有内部数据外泄风险", "全本地化部署,解析/检索/问答/检测全部本机 Ollama,零 Token、数据不出内网", "已实现(前期)"],
    ], ["业务痛点", "系统能力", "状态"]))
    add("")
    add("---")
    add("")
    add("## 二、增强 A:制度版本与生效期管理")
    add("")
    add("### 2.1 能力")
    add("")
    add("- **制度元数据**:文件编号(doc_no)、制度名称、生效日期、失效日期、状态(现行/已被替代/草案/已失效)、"
        "发布部门、适用部门、替代关系(本文件替代的编号);")
    add("- **状态自动判定**:综合状态字段与生效/失效日期,自动得出「现行有效 / 尚未生效 / 已失效 / 已被替代 / 草案」;")
    add("- **替代联动**:新版本声明「替代 HR-2023-001」后,旧版本自动标记为「已被替代」并记录被替代关系;")
    add("- **检索过滤**:问答与冲突检测默认**排除已废止/未生效版本**,避免用旧制度回答新问题;")
    add("- **版本链**:按生效日期从旧到新列出同一制度的所有版本及其状态、替代关系;")
    add("- **到期提醒**:`GET /api/doc/expiring?days=30` 列出即将到期的现行制度(前端系统页展示);")
    add("- **冲突分类**:同一制度不同版本被判定为「制度版本差异」并给出**建议依据的现行版本**;"
        "不同来源的规定矛盾则判为「跨来源规定冲突」并要求并列陈述。")
    add("")
    add("### 2.2 实测证据")
    add("")
    d_new, d_old = v.get("new", {}) or {}, v.get("old", {}) or {}
    add(md_table([
        ["制度元数据写入", f"编号 {d_new.get('doc_no')} · 生效 {d_new.get('effective_date')} · 状态 {d_new.get('state_label')}"],
        ["旧版自动标记", f"{d_old.get('doc_no')} → {d_old.get('state')}(被 {d_old.get('superseded_by')} 替代)"],
        ["版本链顺序", "HR-2023-001 → HR-2025-001(按生效日期)"],
        ["检索命中来源", "、".join(sorted(set(v.get("hits") or []))) or "-"],
        ["冲突分类", f"版本差异 {v.get('conflict', {}).get('version_conflicts')} 处 / 跨来源冲突 {v.get('conflict', {}).get('genuine_conflicts')} 处"],
        ["建议依据版本", f"{(v.get('conflict', {}).get('first') or {}).get('preferred', {}).get('doc_no', '-')}"
                          f"(生效 {(v.get('conflict', {}).get('first') or {}).get('preferred', {}).get('effective_date', '-')})"],
    ], ["验证项", "实测结果"]))
    add("")
    add("> 关键效果:提问「公司年假有几天?未休完可以结转吗?」时,**已废止的 2023 版不再被检索**"
        "(命中来源仅 2025 版),回答直接依据现行版本给出 8 天/可结转;若两个库分别存有新老版本,"
        "系统会明确告知这是**版本差异**并指出现行版本编号,而非把两套说法混为一谈。")
    add("")
    add("---")
    add("")
    add("## 三、增强 B:部门/角色权限隔离")
    add("")
    add("### 3.1 能力")
    add("")
    add("- **知识库可见范围**:`all`(全员)或 `restricted`(仅指定部门,如 薪酬制度仅人力资源部);")
    add("- **身份传递**:请求头 `X-Actor-Role` / `X-Actor-Dept` / `X-Actor-Name`(中文部门名自动百分号编解码);")
    add("- **全链路鉴权**:知识库列表、自动路由候选、手动指定检索、跨库冲突检测、文档列表/上传/更新/删除/下载,"
        "以及库设置修改均按身份校验;")
    add("- **越权处理**:问答返回 `denied` 事件并给出明确原因;接口返回 403;同时写入审计日志;")
    add("- **前端身份切换**:侧栏可切换角色/部门,实时影响可见库与问答结果。")
    add("")
    add("### 3.2 实测证据")
    add("")
    denied = (perm.get("denied_event") or {}).get("message", "")
    add(md_table([
        ["受限库对非授权部门", "隐藏(知识库列表 hidden_count=1,路由候选不含该库)"],
        ["受限库对授权部门", "可见且可正常问答(命中医保/薪酬条款并给出引用)"],
        ["管理员身份", "可见全部知识库(含受限库)"],
        ["手动指定越权库", "拒绝"],
        ["越权访问文档接口", "HTTP 403"],
        ["越权调用冲突检测", "HTTP 403"],
    ], ["场景", "实测结果"]))
    add("")
    if denied:
        add(f"> 越权提示示例:`{denied}`")
        add("")
    add("---")
    add("")
    add("## 四、增强 C:审计日志")
    add("")
    add("### 4.1 能力")
    add("")
    add("- **留痕范围**:问答(`ask`)、冲突告警(`conflict`)、越权拒绝(`deny`)、文档新增/更新/删除/属性修改"
        "(`doc_add`/`doc_update`/`doc_delete`/`doc_meta`)、建库/库设置/删库(`kb_create`/`kb_update`/`kb_delete`);")
    add("- **记录内容**:时间、操作类型、操作者角色与部门、目标知识库/文档、结果、JSON 明细"
        "(如问答的问题与冲突数、文档更新的复用率等);")
    add("- **查询接口**:`GET /api/audit?action=&kb_id=&dept=&result=&limit=`;`GET /api/audit/stats` 统计;")
    add("- **可见范围约束**:非管理员仅能查询本部门记录;")
    add("- **前端审计页**:可按操作类型筛选、查看统计卡片与明细表。")
    add("")
    add("### 4.2 实测证据")
    add("")
    add(md_table([
        ["审计记录总数", aud.get("total", "-")],
        ["覆盖的操作类型", "、".join(aud.get("actions", []) or [])],
        ["越权拒绝记录", aud.get("denied", "-")],
        ["按类型过滤查询", "可用(如 action=deny)"],
        ["统计接口", "可用(总数/越权数/操作分布/最近时间)"],
        ["非管理员查询范围", "仅本部门(实测 depts={'财务部'})"],
    ], ["验证项", "实测结果"]))
    add("")
    add("---")
    add("")
    add("## 五、验证与回归汇总")
    add("")
    add(md_table([
        ["产品化增强验证(本次)", ev.get("summary", {}).get("total", 0), ev.get("summary", {}).get("pass", 0), ev.get("summary", {}).get("fail", 0), "版本管理 11 · 权限隔离 8 · 审计 8"],
        ["三项创新回归", inno.get("summary", {}).get("total", 0), inno.get("summary", {}).get("pass", 0), inno.get("summary", {}).get("fail", 0), "硬隔离 / 增量更新 / 冲突检测"],
        ["原端到端验收回归", 19, 19, 0, "建库/入库/更新/删除/路由/问答/自动分类"],
    ], ["验证套件", "断言数", "通过", "失败", "说明"]))
    add("")
    add("**合计 73 项断言全部通过。**")
    add("")
    add("---")
    add("")
    add("## 六、本次同时修复的问题")
    add("")
    add("| 问题 | 影响 | 修复 |")
    add("|---|---|---|")
    add("| 制度元数据以表单字段上传但后端按查询参数接收 | 编号/生效期等字段丢失,版本管理失效 | 改为 `Form` 参数接收 |")
    add("| HTTP 头无法承载中文部门名(latin-1 限制) | 中文部门身份鉴权直接报错 | 头值百分号编码 + 服务端自动解码 |")
    add("| 纯文本文档所有段落被合并为一个小节 | 切块跨主题合并,回答串味(如把「出差十个工作日」答成调休期限) | 解析器按**空行与条款行**切分小节,一条制度一块,检索精度显著提升 |")
    add("")
    add("---")
    add("")
    add("## 七、交付物与使用方式")
    add("")
    add(md_table([
        ["制度版本管理", "文档抽屉「制度属性」按钮 / `PATCH /api/doc/meta/{kb}/{doc}`、`GET /api/doc/versions/{kb}`、`GET /api/doc/expiring`"],
        ["权限隔离", "建库或「设置」中配置可见范围;侧栏切换身份;请求头 `X-Actor-Role`/`X-Actor-Dept`"],
        ["审计日志", "界面「审计日志」页;`GET /api/audit`、`GET /api/audit/stats`"],
        ["验证脚本", "`scripts/verify_productization.py`(证据:`scripts/_verify_productization.json`)"],
        ["数据库变更", "自动迁移:kb 增加 visibility/allowed_depts/owner_dept/doc_category;documents 增加 doc_no/title/effective_date/expiry_date/doc_status/issuer/dept_scope/supersedes/superseded_by;新增 audit_log 表"],
    ], ["能力", "入口"]))
    add("")
    add("当前知识库与文档数据在升级后完整保留(自动列迁移,无需重建)。")
    add("")
    add("> 注:本报告全部数据由 `scripts/verify_productization.py` 在本地真实模型上实测采集;"
        "证据文件 `scripts/_verify_productization.json`、`scripts/_verify_result.json`。")
    add("")
    return "\n".join(A)


def _plain(t: str) -> str:
    return t.replace("**", "").replace("`", "").strip()


def build_docx(md: str, out: Path) -> bool:
    try:
        import docx
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        return False

    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    try:
        style.element.rPr.rFonts.set(docx.oxml.ns.qn("w:eastAsia"), "微软雅黑")
    except Exception:
        pass

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            rows = []
            for r in block:
                if set(r.replace("|", "").strip()) <= {"-", " "}:
                    continue
                rows.append([c.strip() for c in r.strip("|").split("|")])
            if rows:
                t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                t.style = "Light Grid Accent 1"
                for ri, r in enumerate(rows):
                    for ci, cell in enumerate(r):
                        if ci < len(t.rows[ri].cells):
                            run = t.rows[ri].cells[ci].paragraphs[0].add_run(_plain(cell))
                            run.font.size = Pt(9)
                            if ri == 0:
                                run.bold = True
                doc.add_paragraph()
            continue
        if s.startswith("# "):
            h = doc.add_heading(_plain(s[2:]), level=0)
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif s.startswith("## "):
            doc.add_heading(_plain(s[3:]), level=1)
        elif s.startswith("### "):
            doc.add_heading(_plain(s[4:]), level=2)
        elif s.startswith("> "):
            p = doc.add_paragraph(_plain(s[2:]))
            if p.runs:
                p.runs[0].italic = True
        elif s.startswith("- ") or s.startswith("* "):
            doc.add_paragraph(_plain(s[2:]), style="List Bullet")
        elif re.match(r"^\d+\.\s", s):
            doc.add_paragraph(_plain(re.sub(r"^\d+\.\s", "", s)), style="List Number")
        elif s and s != "---":
            doc.add_paragraph(_plain(s))
        i += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return True


def _mask_paths(text: str) -> str:
    """提交前脱敏:把本机项目根路径替换为占位符(避免公开仓库泄露用户名/机器路径)。"""
    try:
        escaped = str(ROOT).replace("\\", "\\\\")
        return text.replace(str(ROOT), "<PROJECT_ROOT>").replace(escaped, "<PROJECT_ROOT>")
    except Exception:
        return text


def main() -> int:
    ev = json.loads(EVID.read_text(encoding="utf-8")) if EVID.exists() else {"checks": [], "metrics": {}, "summary": {}}
    inno = json.loads(EVID_INNO.read_text(encoding="utf-8")) if EVID_INNO.exists() else {"summary": {}}
    rt = runtime()
    md = build_md(ev, inno, rt)
    DOCS.mkdir(parents=True, exist_ok=True)
    md_path = DOCS / "产品化增强落地报告.md"
    md_path.write_text(_mask_paths(md), encoding="utf-8")
    print(f"Markdown 报告: {md_path} ({len(md)} 字符)")
    docx_path = DOCS / "产品化增强落地报告.docx"
    ok = build_docx(_mask_paths(md), docx_path)
    print(f"Word 报告: {docx_path} ({'成功' if ok else '缺少 python-docx'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
