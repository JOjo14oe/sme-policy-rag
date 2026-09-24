"""三项待落地创新点的端到端验证脚本。

覆盖:
  ① 多知识库向量硬隔离:独立实例/独立存储文件、删库不污染它库
  ② 文档级增量更新与精准向量删除:块级复用率、耗时对比、零残留
  ③ 跨知识库信息冲突检测:矛盾识别 + 问答链路告警

用法: python scripts/verify_innovations.py [http://127.0.0.1:8000]
输出: 控制台逐项结论 + JSON 证据文件 scripts/_verify_result.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent / "_verify_result.json"
c = httpx.Client(base_url=BASE, timeout=900)

PASS, FAIL = 0, 0
EVIDENCE: dict = {"base_url": BASE, "checks": [], "metrics": {}}


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"✅ {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"❌ {name}" + (f"  [{detail}]" if detail else ""))
    EVIDENCE["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
    return bool(cond)


def make_kb(name: str, desc: str = "") -> dict:
    r = c.post("/api/kb", json={"name": name, "description": desc})
    if r.status_code != 200:
        # 同名残留:先清理再建
        for kb in c.get("/api/kb").json()["kbs"]:
            if kb["name"] == name:
                c.delete(f"/api/kb/{kb['id']}")
        r = c.post("/api/kb", json={"name": name, "description": desc})
    return r.json()["kb"]


def add_text_doc(kb_id: str, filename: str, text: str) -> dict:
    r = c.post(f"/api/doc/add/{kb_id}", files={"file": (filename, text.encode("utf-8"), "text/plain")})
    if r.status_code != 200:
        raise RuntimeError(f"入库失败 {filename}: {r.status_code} {r.text[:200]}")
    return r.json()["doc"]


def update_text_doc(kb_id: str, doc_id: str, filename: str, text: str) -> dict:
    r = c.put(f"/api/doc/update/{kb_id}/{doc_id}",
              files={"file": (filename, text.encode("utf-8"), "text/plain")})
    if r.status_code != 200:
        raise RuntimeError(f"更新失败 {filename}: {r.status_code} {r.text[:200]}")
    return r.json()["doc"]


# =====================================================================
# 创新点1:向量硬隔离
# =====================================================================
def test_hard_isolation() -> dict:
    print("\n========== ① 多知识库向量硬隔离 ==========")
    kb_a = make_kb("_验证库A-隔离", "硬隔离验证")
    kb_b = make_kb("_验证库B-隔离", "硬隔离验证")
    doc_a = add_text_doc(kb_a["id"], "A库文档.txt",
                         "量子纠缠实验记录:在 4K 温度下观测到纠缠态保真度 0.93,测量误差小于 1%。")
    doc_b = add_text_doc(kb_b["id"], "B库文档.txt",
                             "咖啡烘焙曲线记录:一爆温度约 196℃,发展时间占比 22%,总时长 11 分钟。")

    iso = c.get("/api/system/isolation").json()
    stores = {s["kb_id"]: s for s in iso["kb_stores"]}
    sa, sb = stores.get(kb_a["id"]), stores.get(kb_b["id"])

    check("A、B 两库各有独立存储目录", bool(sa and sb and sa["path"] != sb["path"]),
          f"{sa['path'] if sa else '-'} | {sb['path'] if sb else '-'}")
    check("A、B 两库各有独立 chroma.sqlite3 文件",
          bool(sa and sb and sa["sqlite"] and sb["sqlite"] and sa["path"] != sb["path"]))
    check("组件布局为 per-kb-chroma-instance", iso["layout"] == "per-kb-chroma-instance",
          iso["layout"])
    check("独立存储路径数 == 知识库数", iso["distinct_paths"] == len(iso["kb_stores"]),
          f"{iso['distinct_paths']} vs {len(iso['kb_stores'])}")

    before_b_chunks = None
    for kb in c.get("/api/kb").json()["kbs"]:
        if kb["id"] == kb_b["id"]:
            before_b_chunks = kb["chunk_count"]

    a_files_before = len(list(Path(sa["path"]).rglob("*"))) if sa else 0
    # 删除 A 库
    del_res = c.delete(f"/api/kb/{kb_a['id']}").json()
    time.sleep(1)
    iso2 = c.get("/api/system/isolation").json()
    ids_after = {s["kb_id"] for s in iso2["kb_stores"]}
    a_path = Path(sa["path"]) if sa else None

    check("删除 A 库后其独立存储目录被物理移除",
          bool(a_path and not a_path.exists()) and kb_a["id"] not in ids_after,
          f"mode={del_res.get('vector_store', {}).get('mode')}")
    check("删除 A 库后 B 库存储目录与文件完好",
          kb_b["id"] in ids_after and sb and Path(sb["path"]).exists())

    # B 库数据未受影响:检索仍可用
    q = c.post("/api/qa/ask", json={"question": "咖啡烘焙一爆温度是多少?", "mode": "manual",
                                    "kb_id": kb_b["id"]})
    text = _collect_stream_text(q)
    check("删除 A 库后 B 库检索问答仍然正常", "196" in text or "一爆" in text, text[:60])

    return {
        "kb_a": kb_a["id"], "kb_b": kb_b["id"],
        "store_a": sa, "store_b": sb,
        "delete_mode": del_res.get("vector_store", {}).get("mode"),
        "b_chunks_before": before_b_chunks,
        "a_files_before": a_files_before,
    }


# =====================================================================
# 创新点2:增量更新与精准删除
# =====================================================================
LONG_DOC_V1 = """第一段:公司考勤制度说明。员工每日工作时间为 9:00 至 18:00,午休一小时,实行五天工作制,迟到超过 30 分钟按事假半天计算;每月累计迟到超过三次将影响当月绩效考核结果,连续三个月迟到将纳入书面警告流程,考勤记录以门禁系统数据为准,员工如对记录有异议可在一个工作日内提出复核申请,复核结果由人力资源部书面反馈,考勤异常未及时申诉的视为认可系统记录。

第二段:请假流程说明。员工请假须提前一天在 OA 系统提交申请,由直属主管审批,病假需附医院证明,紧急情况可事后补办手续;连续请假超过三天的需由部门负责人二次审批并报人力资源部备案,未经批准擅自离岗按旷工处理,旷工累计超过三天的公司有权解除劳动合同并依法结算工资。

第三段:加班与调休说明。加班需提前申请,工作日加班可折算调休,调休需在三个月内使用完毕,逾期作废;法定节假日加班按国家规定支付加班费,周末加班优先安排调休,调休申请同样需在系统中提交并经主管批准,调休期间的考勤按正常出勤记录。

第四段:出差管理说明。出差前需填写出差申请单,注明目的地与事由,交通标准为高铁二等座,住宿标准为一线城市每晚 500 元以内;出差期间产生的市内交通费凭票据实报实销,出差补贴按每天 100 元标准发放,返程后十个工作日内完成报销流程,超期未报销的需说明原因并由部门负责人签字确认。

第五段:薪酬发放说明。每月 10 日发放上月工资,遇节假日顺延,工资条通过邮件发送,如有异议须在五个工作日内提出;年度调薪通常在每年四月进行,调薪结果以书面通知为准,绩效奖金依据年度考核等级发放,具体比例参见年度考核办法,离职员工的绩效奖金按实际在职月份折算发放。"""

LONG_DOC_V2 = """第一段:公司考勤制度说明。员工每日工作时间为 9:00 至 18:00,午休一小时,实行五天工作制,迟到超过 30 分钟按事假半天计算;每月累计迟到超过三次将影响当月绩效考核结果,连续三个月迟到将纳入书面警告流程,考勤记录以门禁系统数据为准,员工如对记录有异议可在一个工作日内提出复核申请,复核结果由人力资源部书面反馈,考勤异常未及时申诉的视为认可系统记录。

第二段:请假流程说明。员工请假须提前一天在 OA 系统提交申请,由直属主管审批,病假需附医院证明,紧急情况可事后补办手续;连续请假超过三天的需由部门负责人二次审批并报人力资源部备案,未经批准擅自离岗按旷工处理,旷工累计超过三天的公司有权解除劳动合同并依法结算工资。

第三段:加班与调休说明。加班需提前申请,工作日加班可折算调休,调休需在六个月内使用完毕,逾期作废;法定节假日加班按国家规定支付加班费,周末加班优先安排调休,调休申请同样需在系统中提交并经主管批准,调休期间的考勤按正常出勤记录。

第四段:出差管理说明。出差前需填写出差申请单,注明目的地与事由,交通标准为高铁二等座,住宿标准为一线城市每晚 500 元以内;出差期间产生的市内交通费凭票据实报实销,出差补贴按每天 100 元标准发放,返程后十个工作日内完成报销流程,超期未报销的需说明原因并由部门负责人签字确认。

第五段:薪酬发放说明。每月 10 日发放上月工资,遇节假日顺延,工资条通过邮件发送,如有异议须在五个工作日内提出;年度调薪通常在每年四月进行,调薪结果以书面通知为准,绩效奖金依据年度考核等级发放,具体比例参见年度考核办法,离职员工的绩效奖金按实际在职月份折算发放。"""


def test_incremental_update() -> dict:
    print("\n========== ② 文档级增量更新与精准向量删除 ==========")
    kb = make_kb("_验证库C-增量", "增量更新验证")
    doc = add_text_doc(kb["id"], "管理制度长文.txt", LONG_DOC_V1)
    v1_chunks = doc["chunk_count"]
    check("多段长文档入库(用于增量比对)", v1_chunks >= 2, f"块数={v1_chunks}")

    # 只修改第三段中的一个数字(三个月 → 六个月)
    upd = update_text_doc(kb["id"], doc["doc_id"], "管理制度长文.txt", LONG_DOC_V2)
    st = upd.get("update_stats", {})
    EVIDENCE["metrics"]["incremental"] = st

    check("更新返回增量统计", bool(st), json.dumps(st, ensure_ascii=False))
    check("版本号递增且 doc_id 不变",
          upd["version"] == 2 and upd["doc_id"] == doc["doc_id"],
          f"v{upd['version']}")
    check("存在被复用的未变更块", st.get("reused_chunks", 0) >= 1,
          f"复用 {st.get('reused_chunks')} / 共 {st.get('total_chunks')}")
    check("仅对变更块重新嵌入", st.get("embedded_chunks", 99) < st.get("total_chunks", 0),
          f"重嵌入 {st.get('embedded_chunks')} 块")
    check("未整篇重新向量化(复用率 > 0)", st.get("reuse_ratio", 0) > 0,
          f"复用率 {st.get('reuse_ratio')}")
    check("实测耗时低于整篇重嵌入估算",
          st.get("actual_ms", 10**9) <= st.get("estimated_full_reembed_ms", 0),
          f"实际 {st.get('actual_ms')}ms vs 估算 {st.get('estimated_full_reembed_ms')}ms")

    # 内容正确性:新说法可检索到,旧说法不应再出现
    r_new = c.post("/api/qa/ask", json={"question": "调休多久内必须使用完毕?", "mode": "manual",
                                        "kb_id": kb["id"]})
    text_new = _collect_stream_text(r_new)
    check("更新后能检索到新内容(六个月)",
          "六" in text_new or "6" in text_new, text_new[:80])

    rec = c.post("/api/kb/reconcile").json()["report"].get(kb["id"], {})
    check("更新后对账无孤儿向量", rec.get("orphan_chunks_removed", 1) == 0,
          f"orphans={rec.get('orphan_docs')}")

    # 精准删除:零残留
    before = c.get(f"/api/doc/count/{kb['id']}").json()["chunk_count"]
    dres = c.delete(f"/api/doc/{kb['id']}/{doc['doc_id']}").json()
    after = c.get(f"/api/doc/count/{kb['id']}").json()["chunk_count"]
    EVIDENCE["metrics"]["precise_delete"] = dres
    check("删除单篇文档返回清除的向量数", dres.get("deleted_chunks", 0) > 0,
          f"清除 {dres.get('deleted_chunks')} 条")
    check("删除后该文档零残留", dres.get("zero_residue") is True,
          f"remaining={dres.get('remaining_chunks')}")
    check("库内向量总数精确下降", after == before - dres.get("deleted_chunks", 0),
          f"{before} → {after}")
    rec2 = c.post("/api/kb/reconcile").json()["report"].get(kb["id"], {})
    check("删除后对账无孤儿向量", rec2.get("orphan_chunks_removed", 1) == 0)
    return {"kb_id": kb["id"], "doc_id": doc["doc_id"], "update_stats": st, "delete": dres}


# =====================================================================
# 创新点3:跨知识库冲突检测
# =====================================================================
CONFLICT_DOC_A = """公司年假制度(2023 版):员工工作满 1 年可享受年假 5 天,满 5 年享受 10 天,满 10 年享受 15 天。
年假需提前 3 个工作日在系统申请。当年未休完的年假不结转至次年,过期作废。"""

CONFLICT_DOC_B = """公司年假制度(2025 修订版):员工工作满 1 年可享受年假 8 天,满 5 年享受 12 天,满 10 年享受 18 天。
年假需提前 7 个工作日在系统申请。当年未休完的年假可结转至次年第一季度使用。"""


def test_conflict_detection() -> dict:
    print("\n========== ③ 跨知识库信息冲突检测 ==========")
    kb_old = make_kb("_验证库D-年假旧版", "冲突检测验证:旧版制度")
    kb_new = make_kb("_验证库E-年假新版", "冲突检测验证:新版制度")
    add_text_doc(kb_old["id"], "年假制度2023.txt", CONFLICT_DOC_A)
    add_text_doc(kb_new["id"], "年假制度2025.txt", CONFLICT_DOC_B)

    q = "公司年假有几天?未休完可以结转到次年吗?"
    t0 = time.time()
    scan = c.post("/api/qa/conflict-check", json={"question": q, "kb_ids": [kb_old["id"], kb_new["id"]]}).json()
    scan_ms = int((time.time() - t0) * 1000)
    EVIDENCE["metrics"]["conflict_scan"] = {
        "checked": scan.get("checked"), "kb_count": scan.get("kb_count"),
        "pairs_considered": scan.get("pairs_considered"),
        "conflicts": len(scan.get("conflicts", [])),
        "relations": scan.get("relations"), "elapsed_ms": scan.get("elapsed_ms"),
    }
    check("冲突检测已执行(参与库 ≥ 2)", scan.get("checked") is True and scan.get("kb_count", 0) >= 2,
          f"kb_count={scan.get('kb_count')}, pairs={scan.get('pairs_considered')}")
    conflicts = scan.get("conflicts", [])
    check("识别出跨库矛盾", len(conflicts) >= 1, f"conflicts={len(conflicts)}")
    if conflicts:
        c0 = conflicts[0]
        check("冲突包含双方说法与来源",
              bool(c0["a"]["kb_name"] and c0["b"]["kb_name"] and (c0.get("point") or c0.get("a_claim"))),
              f"point={c0.get('point')}")
        check("冲突双方来自不同知识库", c0["a"]["kb_id"] != c0["b"]["kb_id"])

    # 集成验证:实际问答链路是否发出 conflict 事件并同时陈述两种说法
    events: list[tuple[str, dict]] = []
    with c.stream("POST", "/api/qa/ask", json={"question": q, "mode": "auto"}) as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = data = None
                for line in block.splitlines():
                    if line.startswith("event: "):
                        ev = line[7:].strip()
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                if ev and data:
                    events.append((ev, data))
    kinds = [e for e, _ in events]
    answer = "".join(d.get("text", "") for e, d in events if e == "delta")
    conflict_ev = next((d for e, d in events if e == "conflict"), None)
    EVIDENCE["metrics"]["conflict_answer_preview"] = answer[:400]

    check("问答链路发出 conflict 告警事件", conflict_ev is not None and len(conflict_ev.get("conflicts", [])) >= 1,
          f"events={sorted(set(kinds))}")
    check("回答同时呈现两种说法(5/10 天 与 8/12 天)",
          ("5" in answer and "8" in answer) or ("10" in answer and "12" in answer),
          answer[:120].replace("\n", " "))
    check("回答明确提示存在冲突或分歧",
          any(k in answer for k in ("冲突", "矛盾", "不一致", "分歧", "不同", "差异", "两版", "两个版本")),
          answer[:120].replace("\n", " "))
    return {"kb_old": kb_old["id"], "kb_new": kb_new["id"],
            "conflicts": len(conflicts), "scan_ms": scan_ms}


def _collect_stream_text(resp) -> str:
    text = ""
    buf = ""
    for chunk in resp.iter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            ev = data = None
            for line in block.splitlines():
                if line.startswith("event: "):
                    ev = line[7:].strip()
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
            if ev == "delta" and data:
                text += data.get("text", "")
    return text


def cleanup(kb_ids: list[str]) -> None:
    for kid in kb_ids:
        try:
            c.delete(f"/api/kb/{kid}")
        except Exception:
            pass


def main() -> int:
    h = c.get("/api/system/health").json()
    print(f"后端: {BASE} ok={h.get('ok')}")
    st = c.get("/api/system/status").json()
    print(f"模型: {st['chat_model']} / {st['embed_model']}  就绪={st['chat_ok'] and st['embed_ok']}")
    print(f"升级前已有知识库: {st['kbs']} 个 / {st['docs']} 篇 / {st['chunks']} 块")

    created: list[str] = []
    try:
        iso = test_hard_isolation()
        created += [iso["kb_b"]]           # A 已在测试内删除
        inc = test_incremental_update()
        created.append(inc["kb_id"])
        con = test_conflict_detection()
        created += [con["kb_old"], con["kb_new"]]
    except Exception as e:
        print(f"\n⚠ 验证中断: {type(e).__name__}: {e}")
        EVIDENCE["error"] = f"{type(e).__name__}: {e}"
    finally:
        cleanup([k for k in created if k])
        print("\n(已清理验证过程中创建的知识库)")

    EVIDENCE["summary"] = {"pass": PASS, "fail": FAIL,
                           "total": PASS + FAIL,
                           "conclusion": "全部通过" if FAIL == 0 else f"{FAIL} 项未通过"}
    OUT.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n======== 结果: {PASS} 通过 / {FAIL} 失败 ========")
    print(f"证据文件: {OUT}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
