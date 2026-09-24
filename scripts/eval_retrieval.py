"""检索与答案质量评测(本地化评测集,可复现)。

对标结论:主流开源方案(RAGFlow / Dify / AnythingLLM / Langchain-Chatchat 等)
普遍**没有内置评测集**;本脚本为本项目补齐"可度量"能力:

评测维度
--------
1. 检索指标(消融对比四种模式 vector / lexical / hybrid / hybrid_rerank):
   hit@1、hit@3、MRR(基于"期望来源文档"判定)
2. 答案指标(生产模式 hybrid_rerank):
   · 引用命中率:答案引用中包含期望来源文档
   · 事实命中率:答案包含期望事实关键词
   · 拒答正确率:域外问题应明确拒答(no_context)
   · 平均响应耗时
3. 版本治理校验:已废止版本(2023 版)不得出现在检索结果中

用法: python scripts/eval_retrieval.py [http://127.0.0.1:8000]
输出: 控制台表格 + 证据文件 scripts/_eval_result.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent / "_eval_result.json"
KB_NAME = "_评测-制度问答"
c = httpx.Client(base_url=BASE, timeout=600)

# ------------------------------------------------------------------ 评测语料
DOCS: list[dict] = [
    {
        "filename": "年假制度2025.txt",
        "meta": {"doc_no": "HR-2025-001", "title": "员工年假制度", "effective_date": "2025-01-01",
                 "issuer": "人力资源部", "dept_scope": "全员"},
        "text": """员工年假制度(2025 版,文件编号 HR-2025-001)

第一条 员工工作满 1 年可享受年假 8 天,满 5 年享受 12 天,满 10 年享受 18 天。

第二条 年假需提前 7 个工作日在系统提交申请,由部门负责人审批;连续休假超过 5 天的需报人力资源部备案。

第三条 请病假需附医院证明,病假期间工资按国家规定执行;年度病假累计超过 15 天的需提交复岗证明。

第四条 当年未休完的年假可结转至次年第一季度使用,逾期作废。""",
    },
    {
        "filename": "年假制度2023.txt",
        "meta": {"doc_no": "HR-2023-001", "title": "员工年假制度(旧版)", "effective_date": "2023-01-01",
                 "supersedes": "", "issuer": "人力资源部", "dept_scope": "全员"},
        "text": """员工年假制度(2023 版,文件编号 HR-2023-001)

第一条 员工工作满 1 年可享受年假 5 天,满 5 年享受 10 天,满 10 年享受 15 天。

第二条 年假需提前 3 个工作日在系统提交申请。

第三条 当年未休完的年假不结转至次年,过期作废。""",
    },
    {
        "filename": "调休与加班制度.txt",
        "meta": {"doc_no": "OT-2025-001", "title": "加班与调休制度", "effective_date": "2025-01-01",
                 "issuer": "人力资源部"},
        "text": """加班与调休制度(文件编号 OT-2025-001)

第一条 加班需提前在系统提交加班申请,由直属主管审批,未审批的加班不予认定。

第二条 工作日加班可折算调休,调休需在六个月内使用完毕,逾期作废;法定节假日加班按国家规定支付加班费。

第三条 调休申请需在系统提交并经主管批准,调休期间考勤按正常出勤记录。""",
    },
    {
        "filename": "差旅与报销标准.txt",
        "meta": {"doc_no": "TR-2025-001", "title": "差旅与报销标准", "effective_date": "2025-01-01",
                 "issuer": "财务部"},
        "text": """差旅与报销标准(文件编号 TR-2025-001)

第一条 出差交通标准:高铁二等座、飞机经济舱;市内交通费凭票据实报销。

第二条 住宿标准:一线城市每晚不超过 500 元,其他城市每晚不超过 350 元。

第三条 出差补贴按每天 100 元标准发放;返程后十个工作日内完成报销,超期需说明原因并由部门负责人签字确认。""",
    },
    {
        "filename": "薪酬保密制度.txt",
        "meta": {"doc_no": "SAL-2025-001", "title": "薪酬保密制度", "effective_date": "2025-01-01",
                 "issuer": "人力资源部", "dept_scope": "人力资源部,财务部"},
        "text": """薪酬保密制度(文件编号 SAL-2025-001)

第一条 员工薪酬实行保密管理,薪酬明细仅人力资源部与本人员工可见,严禁相互打探或对外传播。

第二条 薪酬资料由人力资源部统一归档保管,查阅需部门负责人书面审批并登记用途。

第三条 违反薪酬保密规定造成不良影响的,按公司奖惩制度处理。""",
    },
    {
        "filename": "考勤管理制度.txt",
        "meta": {"doc_no": "AT-2025-001", "title": "考勤管理制度", "effective_date": "2025-01-01",
                 "issuer": "行政部"},
        "text": """考勤管理制度(文件编号 AT-2025-001)

第一条 工作时间 9:00 至 18:00,午休一小时,实行五天工作制。

第二条 迟到超过 30 分钟按事假半天计算;每月累计迟到超过三次将影响当月绩效考核。

第三条 考勤记录以门禁系统数据为准,有异议可在一个工作日内提出复核。""",
    },
    {
        "filename": "绩效考核办法.txt",
        "meta": {"doc_no": "PF-2025-001", "title": "绩效考核办法", "effective_date": "2025-01-01",
                 "issuer": "人力资源部"},
        "text": """绩效考核办法(文件编号 PF-2025-001)

第一条 公司实行季度考核与年度考核相结合的绩效管理机制。

第二条 绩效奖金依据年度考核等级发放,等级分为 A、B、C、D 四档,具体比例参见年度考核办法附件。

第三条 年度调薪通常在每年四月进行,调薪结果以书面通知为准。""",
    },
    {
        "filename": "信息安全与数据保密制度.txt",
        "meta": {"doc_no": "IS-2025-001", "title": "信息安全与数据保密制度", "effective_date": "2025-01-01",
                 "issuer": "技术部"},
        "text": """信息安全与数据保密制度(文件编号 IS-2025-001)

第一条 公司内部资料分为公开、内部、机密三级,不同级别对应不同访问权限。

第二条 机密级资料禁止通过外部邮箱、个人网盘或即时通讯工具对外发送,禁止将内部数据上传至公有云服务。

第三条 员工离岗时应交回全部资料与访问凭证,离职后仍负有保密义务。""",
    },
    {
        "filename": "年假制度(分公司版).txt",
        "meta": {"doc_no": "HR-2025-002", "title": "分公司年假制度", "effective_date": "2025-03-01",
                 "issuer": "分公司人力资源部"},
        "text": """分公司年假制度(文件编号 HR-2025-002)

第一条 分公司员工工作满 1 年可享受年假 10 天,满 5 年享受 14 天,满 10 年享受 20 天。

第二条 分公司年假需提前 5 个工作日在系统申请,由分公司负责人审批。

第三条 分公司当年未休完的年假不结转,由分公司统一安排调休。""",
    },
    {
        "filename": "培训差旅标准.txt",
        "meta": {"doc_no": "TR-2025-002", "title": "培训差旅标准", "effective_date": "2025-02-01",
                 "issuer": "培训中心"},
        "text": """培训差旅标准(文件编号 TR-2025-002)

第一条 参加外部培训的交通标准:高铁一等座、飞机经济舱。

第二条 培训期间住宿标准:一线城市每晚不超过 600 元,其他城市不超过 450 元,由培训中心统一预订。

第三条 培训期间的餐费补贴按每天 150 元标准发放,培训结束十个工作日内完成报销。""",
    },
]

# 表格类制度文档(测试表格结构化解析:表格单元格内容可被精确检索)
TABLE_DOC = {
    "filename": "假期与福利一览.docx",
    "meta": {"doc_no": "HR-2025-003", "title": "假期与福利一览表", "effective_date": "2025-01-01",
             "issuer": "人力资源部"},
    "table": [["假期类型", "天数", "申请要求"],
              ["年假", "8 天", "提前 7 个工作日"],
              ["婚假", "10 天", "提供结婚证明"],
              ["产假", "158 天", "提供生育证明"]],
    "intro": "各类假期天数与申请要求如下表所示,本表自 2025 年 1 月 1 日起执行。",
}

# ------------------------------------------------------------------ 标注问题集
QUESTIONS: list[dict] = [
    {"q": "HR-2025-001 里规定的年假天数是多少?", "source": "年假制度2025.txt",
     "facts": ["8"], "tag": "精确编号检索", "expect_refuse": False},
    {"q": "员工休假额度按工龄怎么计算?", "source": "年假制度2025.txt",
     "facts": ["8", "12"], "tag": "语义改写检索", "expect_refuse": False},
    {"q": "请病假需要提供什么材料?", "source": "年假制度2025.txt",
     "facts": ["医院证明"], "tag": "条款细节检索", "expect_refuse": False},
    {"q": "年假需要提前几个工作日申请?", "source": "年假制度2025.txt",
     "facts": ["7"], "tag": "精确数值检索", "expect_refuse": False},
    {"q": "公司年假到底是 5 天还是 8 天?", "source": "年假制度2025.txt",
     "facts": ["8"], "tag": "版本干扰判别", "expect_refuse": False},
    {"q": "调休必须在多久内使用完毕?", "source": "调休与加班制度.txt",
     "facts": ["六个月", "6"], "tag": "期限检索", "expect_refuse": False},
    {"q": "出差住宿一晚最多能报多少钱?", "source": "差旅与报销标准.txt",
     "facts": ["500"], "tag": "报销标准检索", "expect_refuse": False},
    {"q": "薪酬明细哪些人可以查看?", "source": "薪酬保密制度.txt",
     "facts": ["人力资源部"], "tag": "保密条款检索", "expect_refuse": False},
    {"q": "迟到超过多长时间算事假半天?", "source": "考勤管理制度.txt",
     "facts": ["30"], "tag": "考勤规则检索", "expect_refuse": False},
    {"q": "绩效奖金按什么标准发放?", "source": "绩效考核办法.txt",
     "facts": ["考核等级", "等级"], "tag": "绩效制度检索", "expect_refuse": False},
    {"q": "内部机密资料可以发到外部邮箱吗?", "source": "信息安全与数据保密制度.txt",
     "facts": ["禁止", "不得", "不可以"], "tag": "合规条款检索", "expect_refuse": False},
    {"q": "HR-2025-002 规定的年假是多少天?", "source": "年假制度(分公司版).txt",
     "facts": ["10"], "tag": "编号精确区分(易混淆)", "expect_refuse": False},
    {"q": "参加外部培训时住宿标准是多少钱一晚?", "source": "培训差旅标准.txt",
     "facts": ["600"], "tag": "易混淆标准区分", "expect_refuse": False},
    {"q": "婚假有多少天?", "source": "假期与福利一览.docx",
     "facts": ["10"], "tag": "表格内容检索", "expect_refuse": False},
    {"q": "产假可以休多少天?", "source": "假期与福利一览.docx",
     "facts": ["158"], "tag": "表格内容检索", "expect_refuse": False},
    {"q": "今天北京的天气怎么样?", "source": None, "facts": [],
     "tag": "域外问题拒答", "expect_refuse": True},
    {"q": "帮我写一首关于春天的诗", "source": None, "facts": [],
     "tag": "域外问题拒答", "expect_refuse": True},
]

_CN_DIGITS = "零一二三四五六七八九"

MODES = ["vector", "lexical", "hybrid", "hybrid_rerank"]


def _num_to_cn(n: int) -> str:
    """整数转中文写法(覆盖评测中的数值:个位/十位/百位),用于事实匹配兼容"8 天/八天"。"""
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 1000:
        s = _CN_DIGITS[n // 100] + "百"
        rem = n % 100
        if rem == 0:
            return s
        if rem < 10:
            return s + "零" + _CN_DIGITS[rem]
        return s + _num_to_cn(rem)
    return str(n)


def _fuzzy_contains(answer: str, fact: str, max_gap: int = 10) -> bool:
    """宽松包含:允许事实关键词在答案中"按顺序但不相邻"地出现(窗口 ≤ max_gap 字)。
    例:事实「医院证明」可匹配答案「医院开具的病假证明」——语义正确但非字面连续。
    """
    if not fact:
        return False
    a = answer.replace(" ", "")
    f = fact.replace(" ", "")
    if f in a:
        return True
    n = len(f)
    for i in range(len(a)):
        if a[i] != f[0]:
            continue
        j, k = 1, i + 1
        while j < n and k < min(len(a), i + max_gap + n):
            if a[k] == f[j]:
                j += 1
            k += 1
        if j == n:
            return True
    return False


def _fact_present(answer: str, facts: list[str]) -> bool:
    """事实命中判定:兼容阿拉伯数字/中文数字写法,并允许非连续关键词匹配。"""
    if not facts:
        return False
    for f in facts:
        if f in answer or _fuzzy_contains(answer, f):
            return True
        if f.isdigit():
            n = int(f)
            if _num_to_cn(n) in answer:
                return True
            if f"{n}天" in answer.replace(" ", ""):
                return True
    return False


# ------------------------------------------------------------------ 语料准备
def _docx_with_table() -> bytes:
    """生成含表格的 docx 语料(用于验证表格结构化解析)。"""
    import docx as _docx
    import io as _io

    d = _docx.Document()
    d.add_heading(TABLE_DOC["meta"]["title"], level=1)
    d.add_paragraph(TABLE_DOC["intro"])
    rows = TABLE_DOC["table"]
    t = d.add_table(rows=len(rows), cols=len(rows[0]))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    d.add_paragraph("本表由人力资源部负责解释与更新。")
    buf = _io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def ensure_corpus() -> str:
    """建立评测知识库(同名先清理),返回 kb_id。"""
    kbs = c.get("/api/kb").json()["kbs"]
    for kb in kbs:
        if kb["name"] == KB_NAME:
            c.delete(f"/api/kb/{kb['id']}")
    kb = c.post("/api/kb", json={"name": KB_NAME, "description": "评测专用知识库(脚本自动创建)",
                                 "doc_category": "评测"}).json()["kb"]
    for d in DOCS:
        r = c.post(f"/api/doc/add/{kb['id']}",
                   files={"file": (d["filename"], d["text"].encode("utf-8"), "text/plain")},
                   data={k: v for k, v in d["meta"].items() if v})
        if r.status_code != 200:
            raise RuntimeError(f"语料入库失败 {d['filename']}: {r.status_code} {r.text[:200]}")
    # 表格类文档
    r = c.post(f"/api/doc/add/{kb['id']}",
               files={"file": (TABLE_DOC["filename"], _docx_with_table(),
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
               data={k: v for k, v in TABLE_DOC["meta"].items() if v})
    if r.status_code != 200:
        raise RuntimeError(f"表格语料入库失败: {r.status_code} {r.text[:200]}")
    # 建立替代关系:2025 版替代 2023 版 → 旧版自动废止
    docs = c.get(f"/api/doc/list/{kb['id']}").json()["docs"]
    new_id = next(d["doc_id"] for d in docs if d["filename"] == "年假制度2025.txt")
    c.patch(f"/api/doc/meta/{kb['id']}/{new_id}", json={"supersedes": "HR-2023-001", "issuer": "人力资源部"})
    return kb["id"]


# ------------------------------------------------------------------ 检索评测
def eval_retrieval(kb_id: str) -> dict:
    out: dict = {}
    for mode in MODES:
        hit1 = hit3 = 0
        rr_sum = 0.0
        details = []
        for item in QUESTIONS:
            if item["expect_refuse"]:
                continue
            r = c.post("/api/eval/retrieval", json={
                "question": item["q"], "kb_id": kb_id, "mode": mode, "top_k": 5,
            }).json()
            names = [x["filename"] for x in r.get("results", [])]
            rank = names.index(item["source"]) + 1 if item["source"] in names else 0
            hit1 += 1 if rank == 1 else 0
            hit3 += 1 if 0 < rank <= 3 else 0
            rr_sum += (1.0 / rank) if rank else 0.0
            details.append({"q": item["q"], "tag": item["tag"], "expect": item["source"],
                            "rank": rank, "top3": names[:3], "hit": bool(rank)})
        n = sum(1 for i in QUESTIONS if not i["expect_refuse"])
        out[mode] = {
            "n": n,
            "hit@1": round(hit1 / n, 4),
            "hit@3": round(hit3 / n, 4),
            "mrr": round(rr_sum / n, 4),
            "details": details,
        }
    return out


def check_superseded_excluded(kb_id: str) -> dict:
    """版本治理校验:已废止的 2023 版不得出现在检索结果中。"""
    r = c.post("/api/eval/retrieval", json={
        "question": "公司年假有几天?未休完可以结转吗?", "kb_id": kb_id,
        "mode": "hybrid_rerank", "top_k": 6,
    }).json()
    names = [x["filename"] for x in r.get("results", [])]
    return {"returned": names, "superseded_present": "年假制度2023.txt" in names,
            "passed": "年假制度2023.txt" not in names}


# ------------------------------------------------------------------ 答案评测
def eval_answers(kb_id: str) -> dict:
    cite_hit = fact_hit = refuse_ok = 0
    n_fact = sum(1 for i in QUESTIONS if not i["expect_refuse"])
    n_refuse = sum(1 for i in QUESTIONS if i["expect_refuse"])
    latencies = []
    details = []
    for item in QUESTIONS:
        r = c.post("/api/eval/answer", json={"question": item["q"], "kb_id": kb_id}).json()
        answer = r.get("answer") or ""
        cites = r.get("citations") or {}
        cited_files = {v.get("filename") for v in cites.values()}
        latencies.append(r.get("elapsed_ms", 0))
        if item["expect_refuse"]:
             ok = bool(r.get("refused"))
             refuse_ok += 1 if ok else 0
             details.append({"q": item["q"], "tag": item["tag"], "refused": ok,
                             "reason": (r.get("refusal_reason") or "")[:60]})
             continue
        c_ok = item["source"] in cited_files
        f_ok = _fact_present(answer, item["facts"])
        cite_hit += 1 if c_ok else 0
        fact_hit += 1 if f_ok else 0
        details.append({"q": item["q"], "tag": item["tag"], "citation_hit": c_ok,
                        "fact_hit": f_ok, "cited": sorted(x for x in cited_files if x),
                        "answer": answer[:120]})
    return {
        "citation_hit_rate": round(cite_hit / n_fact, 4) if n_fact else 0,
        "fact_hit_rate": round(fact_hit / n_fact, 4) if n_fact else 0,
        "refusal_accuracy": round(refuse_ok / n_refuse, 4) if n_refuse else 0,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "details": details,
    }


def main() -> int:
    print(f"评测后端:{BASE}")
    st = c.get("/api/system/status").json()
    print(f"检索模式:{st.get('retrieval', {}).get('mode')} | 模型:{st['chat_model']} / {st['embed_model']}")
    kb_id = ensure_corpus()
    print(f"评测知识库已就绪:{KB_NAME}({kb_id}),语料 {len(DOCS)} 篇,问题 {len(QUESTIONS)} 条\n")

    print("========== 1) 检索消融对比(hit@1 / hit@3 / MRR) ==========")
    ret = eval_retrieval(kb_id)
    print(f"{'模式':<16}{'hit@1':>8}{'hit@3':>8}{'MRR':>8}")
    for mode, m in ret.items():
        print(f"{mode:<16}{m['hit@1']:>8.3f}{m['hit@3']:>8.3f}{m['mrr']:>8.3f}")

    print("\n========== 2) 最佳模式逐题明细(hybrid_rerank) ==========")
    for d in ret["hybrid_rerank"]["details"]:
        flag = "✅" if d["hit"] else "❌"
        print(f"  {flag} [{d['tag']}] {d['q'][:28]:<30} rank={d['rank']} top3={d['top3'][:2]}")

    print("\n========== 3) 版本治理校验 ==========")
    ver = check_superseded_excluded(kb_id)
    print(f"  {'✅ 已废止版本未出现在检索结果' if ver['passed'] else '❌ 已废止版本仍被检索'}")
    print(f"  返回来源:{ver['returned']}")

    print("\n========== 4) 答案质量(hybrid_rerank) ==========")
    ans = eval_answers(kb_id)
    print(f"  引用命中率:{ans['citation_hit_rate']:.3f}   事实命中率:{ans['fact_hit_rate']:.3f}   "
          f"拒答正确率:{ans['refusal_accuracy']:.3f}")
    print(f"  平均耗时:{ans['avg_latency_ms']} ms(最长 {ans['max_latency_ms']} ms)")
    for d in ans["details"]:
        if "citation_hit" in d:
            flag = "✅" if (d["citation_hit"] and d["fact_hit"]) else ("⚠" if d["citation_hit"] else "❌")
            print(f"  {flag} [{d['tag']}] 引用={d['citation_hit']} 事实={d['fact_hit']} | {d['q'][:26]}")
        else:
            print(f"  {'✅' if d['refused'] else '❌'} [{d['tag']}] 已拒答={d['refused']} | {d['q'][:26]}")

    result = {"kb_id": kb_id, "kb_name": KB_NAME, "retrieval": ret,
              "version_governance": ver, "answers": ans,
              "meta": {"docs": len(DOCS), "questions": len(QUESTIONS),
                       "chat_model": st["chat_model"], "embed_model": st["embed_model"]}}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据文件:{OUT}")
    print("(评测知识库保留,便于复现;如需清理可在界面删除)")

    best = max(MODES, key=lambda m: (ret[m]["hit@1"], ret[m]["mrr"]))
    print(f"\n结论:最佳检索模式 = {best}(hit@1={ret[best]['hit@1']:.3f}, MRR={ret[best]['mrr']:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
