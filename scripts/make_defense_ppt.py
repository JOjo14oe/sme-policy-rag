"""生成《面向中小企业内部制度文档的多知识库私有 RAG 智能问答系统》答辩 PPT(改进版)。

改进依据:原 PPT(17 页)与系统实际实现比对,修正事实错误、替换无据数据、补齐缺失能力,
所有数据均取自可复现的验证脚本产物(_verify_result.json / _verify_productization.json)与实测记录。

用法: python scripts/make_defense_ppt.py
输出: docs/中小企业制度文档RAG系统答辩PPT(改进版).pptx
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "中小企业制度文档RAG系统答辩PPT(改进版).pptx"

# ---------------- 主题 ----------------
NAVY = RGBColor(0x0E, 0x2A, 0x47)
BLUE = RGBColor(0x1F, 0x6F, 0xB2)
TEAL = RGBColor(0x0E, 0x8A, 0x7A)
AMBER = RGBColor(0xB5, 0x6A, 0x00)
GREY = RGBColor(0x55, 0x5F, 0x6D)
LIGHT = RGBColor(0xF2, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "微软雅黑"

W, H = Inches(13.333), Inches(7.5)


def _set_font(run, size=16, bold=False, color=NAVY, font=FONT):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", font)


def textbox(slide, left, top, width, height):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    return tb, tf


def para(tf, text, size=16, bold=False, color=NAVY, space_after=6, level=0,
         first=False, align=PP_ALIGN.LEFT, italic=False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.level = level
    p.alignment = align
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = text
    _set_font(run, size, bold, color)
    run.font.italic = italic
    return p


def rect(slide, left, top, width, height, fill=LIGHT, line=None):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1)
    sh.shadow.inherit = False
    sh.text_frame.word_wrap = True
    return sh


# ---------------- 版式 ----------------
def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def header(slide, idx: str, title: str, sub: str = ""):
    bar = rect(slide, Inches(0), Inches(0), W, Inches(1.05), NAVY)
    bar.adjustments[0] = 0
    _tb, tf = textbox(slide, Inches(0.55), Inches(0.12), Inches(11.4), Inches(0.8))
    para(tf, f"{idx}  {title}", size=26, bold=True, color=WHITE, first=True, space_after=2)
    if sub:
        para(tf, sub, size=12.5, color=RGBColor(0xC9, 0xD8, 0xE8), space_after=0)
    _tb2, tf2 = textbox(slide, Inches(11.9), Inches(0.28), Inches(1.0), Inches(0.5))
    para(tf2, "AIC", size=15, bold=True, color=RGBColor(0x8F, 0xB8, 0xDC),
         first=True, align=PP_ALIGN.RIGHT)


def footer(slide, text="面向中小企业内部制度文档的多知识库私有 RAG 智能问答系统"):
    _tb, tf = textbox(slide, Inches(0.55), Inches(6.98), Inches(12.2), Inches(0.35))
    para(tf, text, size=9.5, color=RGBColor(0x9A, 0xA5, 0xB1), first=True)


def bullets(slide, items, top=Inches(1.35), left=Inches(0.7), width=Inches(12.0),
            size=15.5, gap=9, height=Inches(5.3)):
    """items: [(文本, 层级)] 或 [文本]"""
    _tb, tf = textbox(slide, left, top, width, height)
    first = True
    for it in items:
        text, lvl = (it if isinstance(it, tuple) else (it, 0))
        bullet = "• " if lvl == 0 else "– "
        para(tf, bullet + text, size=size if lvl == 0 else size - 1.5,
             bold=(lvl == 0 and text.endswith(":")),
             color=NAVY if lvl == 0 else GREY,
             space_after=gap, level=lvl, first=first)
        first = False
    return tf


def cards(slide, items, top=Inches(1.5), height=Inches(4.6), per_row=3, start=0):
    """卡片:items=[(标题, [要点...], 颜色)]"""
    n = len(items)
    rows = (n + per_row - 1) // per_row
    left0, gapx = Inches(0.7), Inches(0.35)
    total_w = W - left0 * 2
    cw = int((total_w - gapx * (per_row - 1)) / per_row)
    ch = int((height - Inches(0.3) * (rows - 1)) / rows) if rows else int(height)
    for i, (title, points, color) in enumerate(items):
        r, c = divmod(i, per_row)
        l = left0 + (cw + gapx) * c
        t = top + (ch + Inches(0.3)) * r
        box = rect(slide, l, t, cw, ch, LIGHT)
        tf = box.text_frame
        tf.margin_left = Inches(0.22)
        tf.margin_right = Inches(0.18)
        tf.margin_top = Inches(0.16)
        para(tf, title, size=15, bold=True, color=color, first=True, space_after=6)
        for p in points:
            para(tf, "• " + p, size=11.5, color=GREY, space_after=4)


def table(slide, headers, rows, top=Inches(1.5), left=Inches(0.7),
          width=None, height=None, fsize=11.5, hsize=12):
    cols = len(headers)
    width = width or (W - left * 2)
    height = height or Inches(0.42 * (len(rows) + 1))
    gt = slide.shapes.add_table(len(rows) + 1, cols, left, top, width, height).table
    for j, htxt in enumerate(headers):
        cell = gt.cell(0, j)
        cell.text = ""
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY
        p = cell.text_frame.paragraphs[0]
        run = p.add_run()
        run.text = htxt
        _set_font(run, hsize, True, WHITE)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = gt.cell(i, j)
            cell.text = ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if i % 2 else LIGHT
            p = cell.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = str(val)
            _set_font(run, fsize, False, NAVY if j == 0 else GREY)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    return gt


def highlight(slide, text, top=Inches(5.9), left=Inches(0.7), width=Inches(12.0),
              color=TEAL, size=13.5):
    box = rect(slide, left, top, width, Inches(0.78), RGBColor(0xE8, 0xF4, 0xF1))
    tf = box.text_frame
    tf.margin_left = Inches(0.2)
    para(tf, text, size=size, bold=True, color=color, first=True, space_after=0)
    return box


# ---------------- 构建 ----------------
def build() -> Presentation:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H

    # ===== 1 封面 =====
    s = blank(prs)
    rect(s, Inches(0), Inches(0), W, H, NAVY)
    rect(s, Inches(0), Inches(2.52), W, Inches(0.06), TEAL)
    _tb, tf = textbox(s, Inches(1.0), Inches(1.05), Inches(11.3), Inches(1.5))
    para(tf, "面向中小企业内部制度文档的", size=22, color=RGBColor(0x9F, 0xC6, 0xE6),
         first=True, space_after=4)
    para(tf, "多知识库私有 RAG 智能问答系统", size=40, bold=True, color=WHITE, space_after=0)
    _tb2, tf2 = textbox(s, Inches(1.0), Inches(2.85), Inches(11.3), Inches(1.6))
    para(tf2, "全本地私有化 · 知识库物理隔离 · 制度版本治理 · 跨库冲突智能识别",
         size=16.5, color=RGBColor(0xC9, 0xD8, 0xE8), first=True, space_after=10)
    para(tf2, "AIC 全国大学生人工智能创新大赛 · 项目答辩", size=15, bold=True,
         color=RGBColor(0x7F, 0xD1, 0xC1), space_after=0)
    _tb3, tf3 = textbox(s, Inches(1.0), Inches(5.35), Inches(11.3), Inches(1.2))
    para(tf3, "答辩团队:智启未来 AI 团队", size=14, color=WHITE, first=True, space_after=4)
    para(tf3, f"答辩日期:{datetime.now().strftime('%Y 年 %m 月')}", size=13,
         color=RGBColor(0x9F, 0xB4, 0xCA), space_after=0)

    # ===== 2 目录 =====
    s = blank(prs)
    header(s, "目录", "汇报结构", "痛点 → 目标 → 六项核心能力 → 实现与稳定性 → 实验验证 → 展望")
    items = [
        ("01 项目背景与挑战", "中小企业的制度文档困境与传统 RAG 的四大痛点", BLUE),
        ("02 建设目标与方案", "四项建设目标、总体架构与技术选型", TEAL),
        ("03 核心能力(六项)", "三项技术创新 + 三项产品化能力(版本治理/权限/审计)", NAVY),
        ("04 系统实现与稳定性", "模块实现、检索质量优化、长期稳定运行工程", BLUE),
        ("05 实验验证与成果", "73 项断言全通过、关键实测数据、应用场景演示", TEAL),
        ("06 总结与展望", "成果回顾、演进方向、答辩问答预案", NAVY),
    ]
    cards(s, [(t, [d], c) for t, d, c in items], top=Inches(1.5), height=Inches(5.0),
          per_row=2)
    footer(s)

    # ===== 3 背景 =====
    s = blank(prs)
    header(s, "01", "项目背景:制度文档是中小企业最容易失控的知识资产",
           "规章、人事、管理流程类文件数量不多,却直接决定执行口径与合规风险")
    cards(s, [
        ("信息孤岛化", ["制度散落在个人电脑、邮件、群聊与共享盘",
                        "没有统一入口,查找依赖“问同事”",
                        "新员工上手成本高、口径靠口口相传"], AMBER),
        ("版本混乱化", ["同一制度多版并存(试行版/修订版/废止版)",
                        "员工无法判断“哪一版算数”",
                        "执行偏差直接转化为合规与劳动争议风险"], AMBER),
        ("查询低效化", ["关键词搜索返回大量无关文件,需人工筛选",
                        "条款级信息(天数/期限/比例)容易漏查",
                        "管理层取数慢,难以及时支撑决策"], AMBER),
    ], top=Inches(1.55), height=Inches(4.3), per_row=3)
    highlight(s, "核心矛盾:制度既要“随时可查、口径唯一”,又要“数据不出内网、修改不留隐患”",
              top=Inches(6.05))
    footer(s)

    # ===== 4 传统方案四大痛点 =====
    s = blank(prs)
    header(s, "01", "传统 RAG 方案在制度场景下的四大痛点",
           "通用文档问答方案直接套用,会在隔离、更新、冲突与合规四个维度失效")
    cards(s, [
        ("① 知识库隔离不足", ["所有文档进同一向量库,仅靠标签区分",
                              "不同部门/不同版本相互“污染”",
                              "无法界定信息来源归属,回答混淆"], AMBER),
        ("② 更新成本高昂", ["一处修改需整库重新向量化",
                            "制度迭代快,索引长期滞后",
                            "算力与时间成本随规模线性膨胀"], AMBER),
        ("③ 新旧信息冲突", ["新旧制度矛盾时,模型倾向“融合”成通顺答案",
                            "表面自洽、实质错误,误导执行",
                            "缺少版本观念:不知道哪份是现行版"], AMBER),
        ("④ 公有云隐私风险", ["内部制度与核心数据上传第三方",
                              "数据出域不可控,违反企业安全规范",
                              "与《数据安全法》等合规要求存在冲突"], AMBER),
    ], top=Inches(1.55), height=Inches(4.3), per_row=2)
    highlight(s, "本项目的出发点:不是做一个“更聪明的问答”,而是做一个“制度口径可靠、数据不出内网”的治理工具",
              top=Inches(6.05), color=BLUE)
    footer(s)

    # ===== 5 建设目标 =====
    s = blank(prs)
    header(s, "02", "建设目标:四项可验收的目标",
           "每项目标都对应系统内的具体机制与可复现的验证断言")
    table(s, ["目标", "机制实现", "验证方式"],
          [["1 物理隔离的多知识库", "每个知识库独立 ChromaDB 实例与独立存储目录、独立 sqlite 文件",
            "独立路径数=库数;删库后它库检索仍正常"],
           ["2 制度全域生命周期管控", "块级哈希账本增量更新、按 doc_id 精准删除、制度元数据与替代关系治理",
            "复用率与耗时对比;删除后 zero_residue 二次校验"],
           ["3 冲突与版本差异智能识别", "多库检索 + 话题初筛 + 模型与数值双通道判定;版本差异专类处置",
            "版本差异分类正确;问答链路发出 conflict 事件"],
           ["4 全本地私有化", "Ollama 本地推理,解析/检索/问答/检测全流程内网闭环,无外部 API",
            "零外部调用;模型与数据均在本机"]],
          top=Inches(1.5), height=Inches(4.3), fsize=11.5)
    highlight(s, "额外交付:部门/角色权限隔离与审计留痕 —— 让制度系统“可控可见、可追溯”", top=Inches(6.0))
    footer(s)

    # ===== 6 核心价值 =====
    s = blank(prs)
    header(s, "02", "核心价值:四个维度的可量化收益", "所有量化口径均来自脚本化实测,可现场复现")
    cards(s, [
        ("安全(Security)", ["全本地推理,数据不出内网",
                           "库级物理隔离:删改互不影响",
                           "越权访问被拦截并留痕"], TEAL),
        ("效率(Efficiency)", ["增量更新复用未变块向量",
                             "实测:2 块中文档改 1 段 → 复用率 50%",
                             "更新耗时 637ms vs 全量重嵌入 1250ms"], BLUE),
        ("精准(Accuracy)", ["条款级切分,条文不跨主题合并",
                           "低相关拒答而非硬编",
                           "冲突/版本差异主动告警 + 引用溯源"], NAVY),
        ("可控(Controllability)", ["全本地技术栈,无外部依赖",
                                  "参数可配置、数据目录可整包迁移",
                                  "崩溃自愈与模型唤醒的运维脚本"], RGBColor(0x6A, 0x4C, 0x9B)),
    ], top=Inches(1.5), height=Inches(4.5), per_row=2)
    highlight(s, "设计原则:能用确定性方法保证的,不交给模型“猜” —— 版本判定、数值分歧、权限与隔离均由代码保证",
              top=Inches(6.15), color=BLUE)
    footer(s)

    # ===== 7 总体架构 =====
    s = blank(prs)
    header(s, "02", "总体架构:轻量化三层 + 本地模型层", "模块解耦,普通办公电脑即可运行(定位:单机/内网私有部署)")
    rows = [
        ("应用层", "FastAPI 原生单页 Web 界面(免构建):知识库管理 / 文档管理 / 智能问答 / 自动分类建库 / 审计日志 / 系统状态", BLUE),
        ("服务层", "知识库与文档治理(元数据 · 版本 · 增量索引)· 多库检索与路由 · 冲突与版本差异检测 · 权限校验 · 审计留痕", TEAL),
        ("数据层", "每知识库独立 ChromaDB 实例(data/vectors/<kb_id>/) + SQLite(库/文档/制度元数据/块哈希账本/审计日志) + 原文归档", NAVY),
        ("模型层", "Ollama 本地部署:deepseek-r1:7b(生成,4.7GB)+ bge-m3(嵌入,1024 维,1.2GB)", RGBColor(0x6A, 0x4C, 0x9B)),
    ]
    top = Inches(1.45)
    for name, desc, color in rows:
        box = rect(s, Inches(0.7), top, Inches(11.93), Inches(1.02), LIGHT)
        tf = box.text_frame
        tf.margin_left = Inches(0.24)
        para(tf, name, size=14, bold=True, color=color, first=True, space_after=2)
        para(tf, desc, size=11.5, color=GREY, space_after=0)
        top += Inches(1.12)
    highlight(s, "架构亮点:库级物理隔离 · 制度版本可治理 · 冲突可识别 · 权限可管控 · 全流程可审计 · 部署零外部依赖",
              top=Inches(6.15))
    footer(s)

    # ===== 8 核心能力一 =====
    s = blank(prs)
    header(s, "03", "核心能力一:多知识库向量硬隔离", "从“标签逻辑隔离”升级为“实例级物理隔离”")
    bullets(s, [
        "实现:每个知识库创建独立的 ChromaDB 持久化实例,拥有专属存储目录与独立的 chroma.sqlite3;",
        "删库:先释放实例句柄,再物理删除该库目录 —— 删除语义确定,不受其它库影响;",
        "迁移:旧版单实例布局在启动时自动逐库迁移,数量校验一致后保留备份目录,可回滚;",
        "可观测:提供隔离证据接口,输出每库存储路径、独立 sqlite 文件、文件数与磁盘占用;",
    ], top=Inches(1.4), size=14)
    table(s, ["验证项", "实测结果"],
          [["独立存储", "每库 1 个独立目录 + 1 个独立 chroma.sqlite3,独立路径数 = 知识库数"],
           ["删库隔离", "删除 A 库后其目录被物理移除(mode=deleted),B 库文件完好且问答检索正常"],
           ["旧布局迁移", "逐库迁移并校验数量一致;旧目录更名备份,迁移标记保证幂等"]],
          top=Inches(3.5), height=Inches(1.9), fsize=11.5)
    highlight(s, "解决的问题:通用方案“单库混杂”导致的信息污染与来源不可界定,从存储层根治", top=Inches(5.7))
    footer(s)

    # ===== 9 核心能力二 =====
    s = blank(prs)
    header(s, "03", "核心能力二:块级增量更新与精准向量删除", "从“整篇重向量化”升级为“按变更块更新”")
    bullets(s, [
        "块哈希账本:文档切分为语义块后逐块计算内容指纹(shp256 前 32 位),建立“块–哈希–向量”映射;",
        "增量更新:未变更块直接复用向量库中的既有向量(零嵌入开销),仅对新增/修改块重新嵌入;",
        "精准删除:按 doc_id 定位清除该文档全部向量,并二次查询确认剩余为 0;",
        "收益可量化:每次更新写入账本,记录复用块数、重嵌入块数、实际耗时与全量重嵌入估算耗时。",
    ], top=Inches(1.4), size=14)
    table(s, ["指标", "实测值", "口径"],
          [["复用未变更块", "1 / 2 块(复用率 50%)", "5 段长文档,仅修改其中 1 段"],
           ["更新耗时", "637 ms", "同上场景实际耗时"],
           ["全量重嵌入估算", "1250 ms", "按本次嵌入速率折算"],
           ["删除后残留", "0(zero_residue = true)", "二次校验 + 对账清理孤儿向量"]],
          top=Inches(3.45), height=Inches(2.0), fsize=11.5)
    highlight(s, "价值:制度修订(改一个数字/期限)不必重建整库索引,更新成本随“改动量”而非“库规模”增长",
              top=Inches(5.75))
    footer(s)

    # ===== 10 核心能力三 =====
    s = blank(prs)
    header(s, "03", "核心能力三:跨知识库信息冲突检测", "不让模型把互相矛盾的规定“融合”成一个通顺的错误答案")
    bullets(s, [
        "多库检索:对同一问题在多个知识库分别召回片段(问题向量只嵌入一次,避免重复算力);",
        "话题初筛:以字符二元组相似度快速筛出“在谈同一件事”的跨库片段对,并把数值分歧对优先排序;",
        "双通道判定:本地模型语义判定 + 数值分歧确定性检测(抽取数字与带单位中文数字,取值互斥即判冲突);",
        "告警与约束:命中冲突时向界面推送冲突面板,并在生成提示中约束模型分别陈述来源、不得擅自合并。",
    ], top=Inches(1.4), size=13.5)
    table(s, ["验证项", "实测结果"],
          [["冲突检出", "新旧年假制度场景检出 1 处冲突,冲突点:「年假天数和结转规则不一致」"],
           ["判定通道", "numeric+llm 双通道确认(数值分歧 5/3 天 vs 8/6 月 + 模型语义判定)"],
           ["问答链路", "发出 conflict 告警事件,回答分别陈述两库说法并标注来源,明确提示存在冲突"]],
          top=Inches(3.9), height=Inches(1.7), fsize=11.5)
    highlight(s, "关键设计:确定性通道兜底小模型的判定波动,避免“该报的冲突没报出来”", top=Inches(5.85))
    footer(s)

    # ===== 11 核心能力四:版本治理 =====
    s = blank(prs)
    header(s, "03", "核心能力四(产品化):制度版本与生效期治理",
           "直接回应“新旧版本冲突难以识别”——把冲突升级为可判定的版本问题")
    cards(s, [
        ("制度元数据", ["文件编号 / 制度名称 / 发布部门 / 适用范围",
                       "生效日期 / 失效日期 / 状态(现行·已被替代·草案·已失效)",
                       "替代关系:本文件替代的编号 → 旧版自动标记废止"], TEAL),
        ("检索侧治理", ["自动排除已废止与尚未生效版本",
                       "同一制度多版本时以现行版为回答依据",
                       "实测:命中来源仅 2025 版,回答给出 8 天(现行版口径)"], BLUE),
        ("版本差异专类", ["同制度不同版本 → 判为「制度版本差异」",
                         "给出建议依据的现行版本(编号/生效日期/状态)",
                         "不同来源矛盾 → 判为「跨来源规定冲突」,并列陈述"], NAVY),
        ("版本链与提醒", ["版本链:按生效日期列出全部版本与替代关系",
                         "到期提醒:可配置 30 天内到期的现行制度清单",
                         "支撑制度复审与滚动修订"], RGBColor(0x6A, 0x4C, 0x9B)),
    ], top=Inches(1.5), height=Inches(4.4), per_row=2)
    highlight(s, "实测:导入 2023 版后引入 2025 版并声明替代 → 旧版自动“已被替代”且不再参与回答;跨库版本差异给出 preferred=HR-2025-777",
              top=Inches(6.1), color=BLUE, size=12.5)
    footer(s)

    # ===== 12 核心能力五:权限 =====
    s = blank(prs)
    header(s, "03", "核心能力五(产品化):部门 / 角色权限隔离", "敏感制度(薪酬、绩效)只在授权部门可见")
    bullets(s, [
        "知识库可见范围:全员(all)或受限(restricted,仅指定部门);管理员可见全部;",
        "身份传递:请求头携带角色 / 部门 / 姓名(中文部门名自动百分号编解码),界面左下角可切换身份;",
        "全链路鉴权:知识库列表、自动路由候选、手动指定检索、跨库冲突检测、文档列表/上传/更新/删除/下载、库设置修改;",
        "越权处理:问答返回明确拒绝原因,接口返回 403,并写入审计日志(deny 记录)。",
    ], top=Inches(1.4), size=13.5)
    table(s, ["验证场景", "实测结果"],
          [["受限库对非授权部门", "知识库列表隐藏该库;自动路由候选不含该库"],
           ["受限库对授权部门", "可见且可正常检索问答(命中薪酬保密条款并给出引用)"],
           ["手动指定越权库", "返回 denied 事件:提示可见范围、允许部门与当前身份"],
           ["越权访问文档 / 冲突检测接口", "HTTP 403;越权行为进入审计记录"]],
          top=Inches(3.75), height=Inches(2.05), fsize=11.5)
    highlight(s, "价值:一台内网设备即可实现“部门级制度隔离”,无需额外权限系统", top=Inches(5.95))
    footer(s)

    # ===== 13 核心能力六:审计 =====
    s = blank(prs)
    header(s, "03", "核心能力六(产品化):审计日志与合规留痕", "制度系统必须回答“谁在什么时候问了什么、改了什么”")
    bullets(s, [
        "留痕范围(9 类):问答、冲突告警、越权拒绝、文档新增/更新/删除/属性修改、建库/库设置修改/删库;",
        "记录内容:时间、操作类型、操作者角色与部门、目标库/文档、结果、JSON 明细(问题内容、冲突类型与数量、更新复用率等);",
        "查询能力:按操作类型、知识库、部门、结果过滤;统计接口输出总数、越权次数、操作分布与最近时间;",
        "范围约束:非管理员仅能查询本部门记录,界面提供审计页与筛选。",
    ], top=Inches(1.4), size=13.5)
    table(s, ["验证项", "实测结果"],
          [["留痕覆盖", "问答 / 冲突 / 越权 / 文档增删改 / 制度属性修改 / 建库 等全部产生记录"],
           ["越权留痕", "越权拒绝记录可检索(action=deny),便于事后追责"],
           ["统计与范围", "统计接口可用;非管理员查询结果被约束在本部门(实测仅返回本部门记录)"]],
          top=Inches(4.0), height=Inches(1.65), fsize=11.5)
    highlight(s, "合规价值:问答与变更全程可追溯,满足内部制度系统的审计与合规要求", top=Inches(5.85))
    footer(s)

    # ===== 14 稳定性工程 =====
    s = blank(prs)
    header(s, "04", "稳定性工程:让系统“长期跑得住”", "本地大模型系统的真正难点:显存、并发与进程可靠性")
    cards(s, [
        ("进程可靠性", ["守护脚本:后端崩溃 5 秒内自动重启",
                       "Ollama 失联自动唤醒(僵死进程重启)",
                       "已运行实例被接管,避免端口抢占"],
         TEAL),
        ("显存与并发", ["推理全局串行:同时仅 1 个嵌入批次 + 1 个对话流",
                       "限制同时加载模型数与模型驻留时长",
                       "8.9GB 模型 → 4.7GB 模型,消除 OOM 主因"], BLUE),
        ("请求韧性", ["重 IO 端点线程池化,单请求不阻塞全站",
                     "调用超时与前置探活,失败快速返回友好提示",
                     "前端 SSE 总超时 + 空闲中断 + 停止按钮"], NAVY),
        ("检索可靠性", ["相似度阈值拒答,低相关不硬编",
                       "对账机制清理孤儿向量、校验块账本",
                       "条款级切分避免跨主题串味"], RGBColor(0x6A, 0x4C, 0x9B)),
    ], top=Inches(1.5), height=Inches(4.35), per_row=2)
    highlight(s, "实测:强杀后端 → 5 秒恢复健康;杀掉 Ollama → 6 秒被守护唤醒;问答在 8GB 显存笔记本上稳定运行(单次约 13–19 秒)",
              top=Inches(6.05), size=12.5)
    footer(s)

    # ===== 15 检索质量优化 =====
    s = blank(prs)
    header(s, "04", "检索质量优化:让制度回答“对得上条款”", "从“能答”到“答得准、可核验”")
    bullets(s, [
        "条款级切分:按空行与条款起始行(第X条、一、、1.)切分小节,一条制度独立成块,不再跨主题合并;",
        "问题实证:优化前曾出现“出差十个工作日”被误答为调休期限的情况 —— 因两块内容被合并进同一向量块;"
        "切分后该问题精确命中调休条款,回归测试稳定通过;",
        "拒答策略:低于相似度阈值的片段不进入生成,回答明确说明“未找到相关内容”,抑制幻觉;",
        "引用溯源:回答标注 [n] 引用编号,并给出源文件名、条款路径与页码,可逐条核对;",
        "对账校验:定期或按需清理孤儿向量,校验元数据与向量侧文档数一致,保证“删除即干净”。",
    ], top=Inches(1.45), size=13.5, gap=10)
    highlight(s, "工程启示:在垂直制度场景中,切分粒度的收益往往大于换更大的模型", top=Inches(6.05), color=BLUE)
    footer(s)

    # ===== 16 技术栈(修正) =====
    s = blank(prs)
    header(s, "04", "系统实现:技术选型与部署方案", "全部组件可离线部署,无外部 API 依赖")
    table(s, ["层次", "选型", "说明"],
          [["开发语言", "Python 3.12", "类型注解 + 数据类,后端统一 FastAPI 生态"],
           ["Web 服务", "FastAPI + Uvicorn", "服务端托管原生单页界面(HTML/CSS/JS,免构建)"],
           ["向量库", "ChromaDB 1.5", "每个知识库一个独立持久化实例(余弦距离)"],
           ["元数据", "SQLite", "知识库/文档/制度元数据/块哈希账本/更新账本/审计日志"],
           ["生成模型", "Ollama · deepseek-r1:7b", "4.7GB,8GB 显存可流畅推理(约 13–19 秒/答)"],
           ["嵌入模型", "Ollama · bge-m3", "1024 维,中文语义效果稳定,支持长文本"],
           ["部署方式", "Windows 单机 / 内网", "install.bat 一键装依赖,start.bat 守护启动"]],
          top=Inches(1.45), height=Inches(4.4), fsize=11)
    highlight(s, "技术边界说明:服务端托管原生单页界面(未引入低代码框架);嵌入模型为 bge-m3;当前版本聚焦文本类制度文档,不包含图像/音视频多模态解析",
              top=Inches(6.05), color=AMBER, size=12)
    footer(s)

    # ===== 17 功能与界面 =====
    s = blank(prs)
    header(s, "04", "系统功能与交互界面", "六大功能页覆盖“建库 → 治理 → 问答 → 审计”全流程")
    cards(s, [
        ("知识库管理", ["建库时配置可见范围/归属部门/制度类别",
                       "卡片展示独立存储路径与占用(隔离证据)",
                       "支持改名、设置、删除、对账清理"], BLUE),
        ("文档管理", ["pdf/docx/txt/md/csv 多格式上传与解析预览",
                     "增量更新并弹窗展示复用率与耗时对比",
                     "制度属性编辑、版本链查看、原文下载"], TEAL),
        ("智能问答", ["自动路由 / 手动指定库兜底",
                     "冲突与版本差异告警面板",
                     "引用溯源、停止生成、超时中断"], NAVY),
        ("自动分类建库", ["无标签混杂文档批量上传",
                         "嵌入聚类 → 建议库名与描述",
                         "人工确认后批量建库入库"], RGBColor(0x6A, 0x4C, 0x9B)),
        ("审计日志", ["按操作类型筛选与统计卡片",
                     "记录问答内容、冲突类型、越权原因",
                     "非管理员仅见本部门记录"], BLUE),
        ("系统状态", ["模型就绪状态与隔离实例证据表",
                     "增量更新收益与制度到期提醒",
                     "运行参数一览(可配置文件调整)"], TEAL),
    ], top=Inches(1.45), height=Inches(5.0), per_row=3)
    footer(s)

    # ===== 18 验证体系 =====
    s = blank(prs)
    header(s, "05", "实验验证:三套脚本化验证,73 项断言全部通过", "所有验证可一条命令复现,证据以 JSON 存档")
    table(s, ["验证套件", "断言数", "覆盖内容", "结果"],
          [["产品化增强验证", "27", "制度版本治理 11 · 权限隔离 8 · 审计留痕 8", "27 / 27 通过"],
           ["三项创新验证", "27", "向量硬隔离 7 · 增量更新与精准删除 12 · 冲突检测 8", "27 / 27 通过"],
           ["端到端功能验收", "19", "建库 · 入库 · 更新 · 删除 · 路由 · 问答 · 自动分类", "19 / 19 通过"]],
          top=Inches(1.5), height=Inches(2.1), fsize=11.5)
    bullets(s, [
        "验证脚本:scripts/verify_productization.py、verify_innovations.py、smoke_test.py;",
        "证据文件:scripts/_verify_productization.json、_verify_result.json(含每项断言的实测明细);",
        "验证均在本地真实模型(deepseek-r1:7b + bge-m3)上执行,非模拟桩数据。",
    ], top=Inches(4.0), size=13)
    highlight(s, "合计 73 项断言全部通过;另实测故障恢复两次(后端强杀自愈、Ollama 宕机唤醒)",
              top=Inches(5.7), color=TEAL)
    footer(s)

    # ===== 19 关键实测数据 =====
    s = blank(prs)
    header(s, "05", "关键实测数据一览", "回答“到底好在哪里”,全部为可复现的实测口径")
    table(s, ["指标", "实测值", "测量口径"],
          [["增量更新复用率", "50%(2 块复用 1 块)", "长文档仅修改 1 段后更新"],
           ["更新耗时对比", "637 ms vs 1250 ms", "实际耗时 vs 全量重嵌入估算"],
           ["删除残留向量", "0(zero_residue=true)", "按 doc_id 删除后二次校验 + 对账"],
           ["冲突检出与分类", "版本差异 2 处 / 跨来源 0 处", "跨库新旧版本场景 conflict-check"],
           ["权限拦截", "HTTP 403 + denied 事件", "财务部身份访问受限薪酬库"],
           ["审计留痕", "9 类操作,累计 195 条记录", "验证执行时点的审计统计"],
           ["单次问答响应", "约 13–19 秒", "deepseek-r1:7b,8GB 显存笔记本"],
           ["向量隔离实例", "每库 1 实例 1 sqlite", "4 个知识库 → 4 个独立存储路径"]],
          top=Inches(1.45), height=Inches(4.6), fsize=11)
    footer(s)

    # ===== 20 场景演示一 =====
    s = blank(prs)
    header(s, "05", "应用场景演示(一):新旧制度冲突 → 版本治理闭环",
           "场景:知识库A 存 2023 版(年假 5 天),知识库B 发布 2025 版(年假 8 天)")
    bullets(s, [
        "① 用户提问“公司年假有几天?未休完可以结转吗?”;",
        "② 系统在多个知识库并行检索,初筛出“谈同一件事”的片段对;",
        "③ 双通道判定:数值通道发现 5/3 天 与 8/6 月取值互斥,模型通道确认语义矛盾;",
        "④ 系统判定该冲突属于「制度版本差异」(同编号家族/同名制度),给出建议依据:HR-2025-777(现行有效);",
        "⑤ 回答分别列出两版口径与来源文件,并提示“以现行版本为准、存在新旧差异”,不擅自合并;",
        "⑥ 若两版位于同一知识库且新版声明替代旧版:旧版自动标记「已被替代」,检索不再返回旧版,回答直接依据现行版。",
    ], top=Inches(1.45), size=13, gap=9)
    highlight(s, "对企业的意义:制度换版不再靠“通知到人”,系统在回答层面自动执行“口径唯一”",
              top=Inches(6.05), color=BLUE)
    footer(s)

    # ===== 21 场景演示二 =====
    s = blank(prs)
    header(s, "05", "应用场景演示(二):制度更新提效 + 敏感制度可控",
           "场景:薪酬制度局部修改;薪酬库设为仅人力资源部可见")
    table(s, ["环节", "系统行为", "实测结果"],
          [["制度局部修订", "块哈希比对,仅对变更块重新嵌入,其余块复用向量",
            "复用率 50%,耗时 637ms(全量约 1250ms)"],
           ["更新后校验", "按 doc_id 清理旧向量并二次确认,随后对账",
            "残留 0;孤儿向量 0"],
           ["越权访问", "财务部身份查询薪酬库",
            "返回 403 / denied 事件,并写入审计"],
           ["授权访问", "人力资源部身份查询同一库",
            "正常命中“薪酬保密制度”条款并给出引用"],
           ["事后追溯", "管理员查询审计页",
            "可见问答、冲突、越权与文档变更的完整记录"]],
          top=Inches(1.5), height=Inches(3.6), fsize=11.5)
    highlight(s, "对企业的意义:制度迭代成本可控 + 敏感制度按部门可见 + 全过程可审计",
              top=Inches(5.4), color=TEAL)
    footer(s)

    # ===== 22 总结与展望 =====
    s = blank(prs)
    header(s, "06", "总结与展望", "已交付一个可运行、可验证、可长期维护的制度问答系统")
    cards(s, [
        ("已达成", ["四项建设目标全部落地并验证",
                   "三项技术创新 + 三项产品化能力(版本治理/权限/审计)",
                   "73 项断言全通过,故障恢复实测有效",
                   "在 8GB 显存普通笔记本上稳定运行"], TEAL),
        ("下一步演进", ["制度治理深化:滚动复审提醒、条款级引用定位",
                       "组织架构对接:账号体系映射、条款级细粒度权限",
                       "性能与规模:检索重排优化、更大模型/量化、并发能力",
                       "交付标准化:一键部署包、备份恢复演练、运行监控"], BLUE),
    ], top=Inches(1.55), height=Inches(4.5), per_row=2)
    highlight(s, "一句话总结:用“确定性工程”兜住大模型的不确定性,让制度问答在企业内网真正可用、可控、可追溯",
              top=Inches(6.2), color=NAVY)
    footer(s)

    # ===== 23 Q&A 预案 =====
    s = blank(prs)
    header(s, "06", "答辩问答预案", "针对高频提问的应答要点(数据均可现场复现)")
    rows = [
        ["与 Dify / LangChain 等开源方案的区别?", "面向制度治理定制:库级物理隔离、版本与生效期治理、冲突/版本差异判别、权限与审计;开源框架偏通用编排,缺少制度语义与合规能力。"],
        ["8GB 显存如何保证稳定?", "选用 4.7GB 的 deepseek-r1:7b;推理全局串行(1 嵌入 + 1 对话);限制同时加载模型数与驻留时长;守护脚本自愈 —— 实测问答 13–19 秒稳定。"],
        ["冲突检测会漏判或误判吗?", "双通道设计:模型判定 + 数值分歧确定性检测兜底漏判;误判风险用“并列呈现 + 人工确认”策略收敛,不做自动合并。"],
        ["数据安全如何证明?", "全流程本机 Ollama 推理,无任何外部 API;断网可用;库级隔离 + 部门权限 + 越权拦截 + 审计留痕。"],
        ["实测数据是否可信?", "三套验证脚本可一条命令复现,每项断言的实测明细以 JSON 存档(含复用率、耗时、判定通道、越权响应码等)。"],
        ["如何扩展到更多部门与制度?", "无标签文档自动分类建库 + 元数据模板批量维护;单机向量规模支撑数十万分块,更大规模可按同一 doc_id 约定迁移向量库。"],
    ]
    table(s, ["可能的问题", "应答要点"], rows, top=Inches(1.4), height=Inches(4.7), fsize=10.5, hsize=11.5)
    footer(s)

    # ===== 24 致谢 =====
    s = blank(prs)
    rect(s, Inches(0), Inches(0), W, H, NAVY)
    _tb, tf = textbox(s, Inches(1.2), Inches(2.5), Inches(11.0), Inches(2.2))
    para(tf, "感谢聆听", size=44, bold=True, color=WHITE, first=True,
         align=PP_ALIGN.CENTER, space_after=14)
    para(tf, "敬请各位评委老师批评指正", size=18, color=RGBColor(0x9F, 0xC6, 0xE6),
         align=PP_ALIGN.CENTER, space_after=0)
    _tb2, tf2 = textbox(s, Inches(1.2), Inches(5.0), Inches(11.0), Inches(0.8))
    para(tf2, "系统与全部验证脚本可现场演示 · 数据均来自本地实测", size=13,
         color=RGBColor(0x7F, 0xD1, 0xC1), first=True, align=PP_ALIGN.CENTER)
    return prs


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    prs = build()
    prs.save(str(OUT))
    n = len(prs.slides.__iter__.__self__._sldIdLst) if False else len(prs.slides._sldIdLst)
    print(f"已生成改进版答辩 PPT:{OUT}")
    print(f"幻灯片总数:{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
