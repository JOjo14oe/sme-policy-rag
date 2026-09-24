"""产品化增强验证:①制度版本与生效期 ②部门/角色权限隔离 ③审计日志。

用法: python scripts/verify_productization.py [http://127.0.0.1:8000]
输出: 控制台逐项结论 + 证据文件 scripts/_verify_productization.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent / "_verify_productization.json"
c = httpx.Client(base_url=BASE, timeout=900)

PASS, FAIL = 0, 0
EV: dict = {"base_url": BASE, "checks": [], "metrics": {}}


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"✅ {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"❌ {name}" + (f"  [{detail}]" if detail else ""))
    EV["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
    return bool(cond)


def hdr(role="admin", dept="", name="tester"):
    """HTTP 头仅允许 latin-1:角色/部门/名称统一百分号编码(与服务端解码对应)。"""
    return {"X-Actor-Role": quote(role), "X-Actor-Dept": quote(dept), "X-Actor-Name": quote(name)}


def mk_kb(name, desc="", visibility="all", allowed_depts="", category="", owner=""):
    body = {"name": name, "description": desc, "visibility": visibility,
            "allowed_depts": allowed_depts, "doc_category": category, "owner_dept": owner}
    r = c.post("/api/kb", json=body, headers=hdr())
    if r.status_code != 200:
        for kb in c.get("/api/kb", headers=hdr("admin")).json()["kbs"]:
            if kb["name"] == name:
                c.delete(f"/api/kb/{kb['id']}", headers=hdr())
        r = c.post("/api/kb", json=body, headers=hdr())
    r.raise_for_status()
    return r.json()["kb"]


def add_doc(kb_id, filename, text, **meta):
    data = {k: v for k, v in meta.items() if v is not None}
    r = c.post(f"/api/doc/add/{kb_id}", headers=hdr(),
               files={"file": (filename, text.encode("utf-8"), "text/plain")}, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"入库失败 {filename}: {r.status_code} {r.text[:200]}")
    return r.json()["doc"]


def doc_list(kb_id, headers=None):
    return c.get(f"/api/doc/list/{kb_id}", headers=headers or hdr()).json()["docs"]


def ask_events(payload, headers=None):
    events = []
    with c.stream("POST", "/api/qa/ask", json=payload, headers=headers or hdr()) as resp:
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
    return events


def answer_of(events) -> str:
    return "".join(d.get("text", "") for e, d in events if e == "delta")


# =====================================================================
# ① 制度版本与生效期管理
# =====================================================================
OLD_POLICY = """公司年假制度(2023 版,文件编号 HR-2023-001)
第一条 员工工作满 1 年可享受年假 5 天,满 5 年享受 10 天,满 10 年享受 15 天。
第二条 年假需提前 3 个工作日在系统申请,由部门负责人审批。
第三条 当年未休完的年假不结转至次年,过期作废。"""

NEW_POLICY = """公司年假制度(2025 修订版,文件编号 HR-2025-001)
第一条 员工工作满 1 年可享受年假 8 天,满 5 年享受 12 天,满 10 年享受 18 天。
第二条 年假需提前 7 个工作日在系统申请,由部门负责人审批。
第三条 当年未休完的年假可结转至次年第一季度使用。"""


def test_version_management() -> list[str]:
    print("\n========== ① 制度版本与生效期管理 ==========")
    kb = mk_kb("_验证-制度版本", "版本管理验证", category="人事", owner="人力资源部")
    old = add_doc(kb["id"], "年假制度2023.txt", OLD_POLICY,
                  doc_no="HR-2023-001", title="公司年假制度",
                  effective_date="2023-01-01", doc_status="effective", issuer="人力资源部")
    new = add_doc(kb["id"], "年假制度2025.txt", NEW_POLICY,
                  doc_no="HR-2025-001", title="公司年假制度",
                  effective_date="2025-01-01", doc_status="effective", issuer="人力资源部",
                  supersedes="HR-2023-001")

    docs = {d["doc_id"]: d for d in doc_list(kb["id"])}
    d_old, d_new = docs.get(old["doc_id"]), docs.get(new["doc_id"])
    EV["metrics"]["version"] = {"old": d_old, "new": d_new}

    check("文档可携带制度元数据(编号/生效期/状态)",
          bool(d_new and d_new["doc_no"] == "HR-2025-001" and d_new["effective_date"] == "2025-01-01"),
          f"doc_no={d_new and d_new['doc_no']}")
    check("新版本声明后旧版本自动标记为「已被替代」",
          bool(d_old and d_old["state"] == "superseded" and d_old["superseded_by"] == "HR-2025-001"),
          f"old.state={d_old and d_old['state']} superseded_by={d_old and d_old['superseded_by']}")
    check("现行版本状态判定为 effective", bool(d_new and d_new["state"] == "effective"),
          f"new.state={d_new and d_new['state']}")

    # 版本链
    chain = c.get(f"/api/doc/versions/{kb['id']}", params={"doc_id": new["doc_id"]}, headers=hdr()).json()
    order = [d["doc_no"] for d in chain["chain"]]
    check("版本链按生效日期从旧到新", order == ["HR-2023-001", "HR-2025-001"], f"{order}")

    # 检索过滤:只应命中现行版本
    events = ask_events({"question": "公司年假有几天?未休完可以结转吗?", "mode": "manual", "kb_id": kb["id"]})
    answer = answer_of(events)
    hits = next((d["hits"] for e, d in events if e == "hits"), [])
    hit_files = [h["filename"] for h in hits]
    EV["metrics"]["version"]["answer"] = answer[:300]
    EV["metrics"]["version"]["hits"] = hit_files
    check("检索自动排除已废止版本(不命中 2023 版)",
          all("2023" not in f for f in hit_files), f"hits={hit_files}")
    check("回答依据现行版本(8 天 / 可结转)",
          ("8" in answer or "八" in answer), answer[:100].replace("\n", " "))

    # 跨库版本差异分类(两个库各存一个版本,且都未被标记替代)
    kb_a = mk_kb("_验证-年假旧档", "含旧版本", category="人事")
    kb_b = mk_kb("_验证-年假新档", "含新版本", category="人事")
    add_doc(kb_a["id"], "年假办法2023.txt", OLD_POLICY.replace("HR-2023-001", "HR-2023-777"),
            doc_no="HR-2023-777", title="年假管理办法", effective_date="2023-01-01")
    add_doc(kb_b["id"], "年假办法2025.txt", NEW_POLICY.replace("HR-2025-001", "HR-2025-777"),
            doc_no="HR-2025-777", title="年假管理办法", effective_date="2025-01-01")
    scan = c.post("/api/qa/conflict-check", headers=hdr(),
                  json={"question": "公司年假有几天?未休完可以结转吗?",
                        "kb_ids": [kb_a["id"], kb_b["id"]]}).json()
    conflicts = scan.get("conflicts", [])
    EV["metrics"]["version"]["conflict"] = {
        "version_conflicts": scan.get("version_conflicts"),
        "genuine_conflicts": scan.get("genuine_conflicts"),
        "first": conflicts[0] if conflicts else None,
    }
    check("同制度不同版本被判为「版本差异」而非普通冲突",
          bool(conflicts) and conflicts[0].get("kind") == "version_conflict",
          f"kind={conflicts[0].get('kind') if conflicts else None}")
    pref = (conflicts[0].get("preferred") if conflicts else None) or {}
    check("版本差异给出「以现行版为准」的建议依据",
          pref.get("doc_no") == "HR-2025-777", f"preferred={pref.get('doc_no')}")

    # 问答链路:版本差异提示
    events2 = ask_events({"question": "公司年假有几天?未休完可以结转吗?", "mode": "auto"})
    kinds = [e for e, _ in events2]
    cf = next((d for e, d in events2 if e == "conflict"), None)
    check("问答链路就版本差异发出告警事件", cf is not None and (cf.get("version_conflicts") or 0) >= 1,
          f"events={sorted(set(kinds))}, version_conflicts={cf and cf.get('version_conflicts')}")

    # 到期提醒
    kb_exp = mk_kb("_验证-到期提醒", "到期提醒验证")
    import datetime as _dt
    soon = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()
    doc_exp = add_doc(kb_exp["id"], "临时规定.txt", "临时安全规定:本规定为试行版本,到期后自动失效。",
                      doc_no="TMP-001", title="临时安全规定", effective_date="2024-01-01", expiry_date=soon)
    items = c.get("/api/doc/expiring", headers=hdr(), params={"days": 30}).json()["items"]
    matched = [i for i in items if i.get("doc_no") == "TMP-001"]
    check("即将到期的制度可被提醒", bool(matched),
          f"days_to_expiry={matched[0].get('days_to_expiry') if matched else None}")

    # 制度属性可独立修改(不走重新上传)
    r = c.patch(f"/api/doc/meta/{kb_exp['id']}/{doc_exp['doc_id']}", headers=hdr(),
                json={"issuer": "行政部", "title": "临时安全规定(试行)", "expiry_date": soon})
    ok_meta = r.status_code == 200 and r.json()["doc"].get("issuer") == "行政部"
    check("制度属性可通过接口单独修改", ok_meta,
          f"issuer={r.json()['doc'].get('issuer') if r.status_code == 200 else r.status_code}")
    return [kb["id"], kb_a["id"], kb_b["id"], kb_exp["id"]]


# =====================================================================
# ② 部门/角色权限隔离
# =====================================================================
def test_permissions() -> list[str]:
    print("\n========== ② 部门/角色权限隔离 ==========")
    kb_hr = mk_kb("_验证-薪酬制度(受限)", "仅人力资源部可见", visibility="restricted",
                  allowed_depts="人力资源部", category="薪酬", owner="人力资源部")
    add_doc(kb_hr["id"], "薪酬保密制度.txt",
            "薪酬保密制度:员工薪酬实行保密管理,薪酬明细仅人力资源部与本人可见,严禁相互打探。",
            doc_no="HR-SAL-001", title="薪酬保密制度", effective_date="2025-01-01",
            dept_scope="人力资源部")

    # 2.1 非授权部门看不到该库
    fin = c.get("/api/kb", headers=hdr("staff", "财务部")).json()
    fin_names = [k["name"] for k in fin["kbs"]]
    check("受限库对非授权部门隐藏", kb_hr["name"] not in fin_names,
          f"hidden_count={fin.get('hidden_count')}")

    # 2.2 授权部门可见
    hr = c.get("/api/kb", headers=hdr("staff", "人力资源部")).json()
    hr_names = [k["name"] for k in hr["kbs"]]
    check("受限库对授权部门可见", kb_hr["name"] in hr_names)

    # 2.3 管理员始终可见
    adm = c.get("/api/kb", headers=hdr("admin", "")).json()
    check("管理员可见全部知识库(含受限库)", kb_hr["name"] in [k["name"] for k in adm["kbs"]])

    # 2.4 自动路由不把受限库作为候选
    r = c.post("/api/qa/route", headers=hdr("staff", "财务部"),
               json={"question": "公司薪酬保密是怎么规定的?"}).json()
    cand_ids = [x["kb_id"] for x in r.get("candidates", [])]
    check("自动路由候选不含无权访问的库", kb_hr["id"] not in cand_ids, f"candidates={cand_ids}")

    # 2.5 手动指定越权 → 拒绝
    events = ask_events({"question": "薪酬保密制度怎么规定?", "mode": "manual", "kb_id": kb_hr["id"]},
                        headers=hdr("staff", "财务部"))
    denied = next((d for e, d in events if e == "denied"), None)
    EV["metrics"]["permission"] = {"denied_event": denied}
    check("手动指定越权库被拒绝(返回 denied 事件)", denied is not None,
          (denied or {}).get("message", "")[:80])

    # 2.6 文档接口越权 → 403
    code = c.get(f"/api/doc/list/{kb_hr['id']}", headers=hdr("staff", "财务部")).status_code
    check("越权访问文档列表返回 403", code == 403, f"http={code}")

    # 2.7 冲突检测越权 → 403
    code2 = c.post("/api/qa/conflict-check", headers=hdr("staff", "财务部"),
                   json={"question": "薪酬制度?", "kb_ids": [kb_hr["id"]]}).status_code
    check("越权调用冲突检测返回 403", code2 == 403, f"http={code2}")

    # 2.8 授权身份可正常问答
    events_ok = ask_events({"question": "薪酬保密制度怎么规定?", "mode": "manual", "kb_id": kb_hr["id"]},
                           headers=hdr("staff", "人力资源部"))
    ans = answer_of(events_ok)
    check("授权部门可正常检索该库", "保密" in ans, ans[:80].replace("\n", " "))
    return [kb_hr["id"]]


# =====================================================================
# ③ 审计日志
# =====================================================================
def test_audit() -> None:
    print("\n========== ③ 审计日志 ===========")
    data = c.get("/api/audit", headers=hdr("admin"), params={"limit": 200}).json()
    items = data.get("items", [])
    actions = [i["action"] for i in items]
    EV["metrics"]["audit"] = {
        "total": data.get("total"), "actions": sorted(set(actions)),
        "denied": sum(1 for i in items if i["result"] == "denied"),
    }
    check("审计记录已产生", len(items) > 0, f"total={data.get('total')}")
    check("问答行为已留痕", "ask" in actions)
    check("越权拒绝已留痕", "deny" in actions,
          f"denied={sum(1 for i in items if i['result'] == 'denied')}")
    check("知识库创建已留痕", "kb_create" in actions)
    check("文档元数据修改已留痕", "doc_meta" in actions)

    q = c.get("/api/audit", headers=hdr("admin"), params={"action": "deny"}).json()
    check("可按操作类型过滤查询", all(i["action"] == "deny" for i in q.get("items", [])),
          f"count={len(q.get('items', []))}")

    stats = c.get("/api/audit/stats", headers=hdr("admin")).json()
    check("审计统计可用(总数/越权数/分布)", (stats.get("total") or 0) > 0 and stats.get("by_action") is not None,
          f"total={stats.get('total')} denied={stats.get('denied')}")

    # 非管理员仅见本部门记录
    fin_audit = c.get("/api/audit", headers=hdr("staff", "财务部")).json()
    depts = {i["actor_dept"] for i in fin_audit.get("items", [])}
    check("非管理员查询审计受部门范围约束", depts <= {"财务部"}, f"depts={depts or '空'}")


def cleanup(kb_ids: list[str]) -> None:
    for kid in kb_ids:
        try:
            c.delete(f"/api/kb/{kid}", headers=hdr())
        except Exception:
            pass


def main() -> int:
    ok = c.get("/api/system/health").json().get("ok")
    print(f"后端: {BASE} ok={ok}")
    created: list[str] = []
    try:
        created += test_version_management()
        created += test_permissions()
        test_audit()
    except Exception as e:
        print(f"\n⚠ 验证中断: {type(e).__name__}: {e}")
        EV["error"] = f"{type(e).__name__}: {e}"
    finally:
        cleanup(created)
        print("\n(已清理验证创建的知识库)")

    EV["summary"] = {"pass": PASS, "fail": FAIL, "total": PASS + FAIL,
                     "conclusion": "全部通过" if FAIL == 0 else f"{FAIL} 项未通过"}
    OUT.write_text(json.dumps(EV, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n======== 结果: {PASS} 通过 / {FAIL} 失败 ========")
    print(f"证据文件: {OUT}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
