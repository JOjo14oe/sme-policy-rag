"""端到端冒烟测试(验收):通过 HTTP 调用真实后端与 Ollama。

前置:
1. 后端已启动(uvicorn app.main:app --port 8000);
2. scripts/make_demo_docs.py 已运行生成 demo_docs/;
3. Ollama 有 deepseek-v2 与 bge-m3。

覆盖四项核心目标:
  1) 手动建库 + 自动分类建库
  2) doc_id 生命周期:新增/更新/删除 → 对账零残留
  3) 自动路由 + 手动指定库(双兜底)
  4) 本地化(仅访问 127.0.0.1)

用法: python scripts/smoke_test.py [base_url]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
DEMO = Path(__file__).resolve().parent / "demo_docs"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "✅" if cond else "❌"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"{mark} {name}" + (f"  -- {detail}" if detail and not cond else ""))


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=600)
    # 0) 健康
    try:
        h = c.get("/api/system/health").json()
        check("后端可达", h.get("ok") is True)
    except Exception as e:
        print(f"❌ 后端不可达: {e}")
        return 1
    st = c.get("/api/system/status").json()
    check("Ollama 已连接", st.get("ollama_connected"))
    check("生成模型就绪", st.get("chat_ok"))
    check("嵌入模型就绪", st.get("embed_ok"))

    # ---- 1) 手动建库 ----
    kb1 = c.post("/api/kb", json={"name": "量子力学", "description": "量子物理与量子计算资料"}).json()["kb"]
    kb2 = c.post("/api/kb", json={"name": "公司制度", "description": "考勤、差旅等公司管理制度"}).json()["kb"]
    check("手动创建两个知识库", bool(kb1["id"] and kb2["id"]))

    # ---- 2) 文档生命周期 ----
    f_q1 = DEMO / "量子力学基础.txt"
    f_q2 = DEMO / "量子计算导论.md"
    files = {
        "file": (f_q1.name, f_q1.read_bytes(), "text/plain"),
    }
    d1 = c.post(f"/api/doc/add/{kb1['id']}", files=files).json()["doc"]
    files2 = {"file": (f_q2.name, f_q2.read_bytes(), "text/markdown")}
    d2 = c.post(f"/api/doc/add/{kb1['id']}", files=files2).json()["doc"]
    check("文档新增入库", d1["chunk_count"] > 0 and d2["chunk_count"] > 0, f"chunks={d1['chunk_count']},{d2['chunk_count']}")

    # 更新 d1 → 版本 +1,doc_id 不变
    up = c.put(f"/api/doc/update/{kb1['id']}/{d1['doc_id']}", files=files).json()["doc"]
    check("文档更新(版本递增、id 不变)", up["version"] == d1["version"] + 1 and up["doc_id"] == d1["doc_id"])

    # 对账:应无孤儿
    rep = c.post("/api/kb/reconcile").json()["report"]
    orphan_total = sum(v["orphan_chunks_removed"] for v in rep.values())
    check("对账:无孤儿向量", orphan_total == 0, f"removed={orphan_total}")

    # 删除 d2 → 对账依旧零残留
    del_r = c.delete(f"/api/doc/{kb1['id']}/{d2['doc_id']}").json()
    check("文档删除(清除向量)", del_r["deleted_chunks"] == d2["chunk_count"])
    rep2 = c.post("/api/kb/reconcile").json()["report"]
    check("删除后再对账:零残留", sum(v["orphan_chunks_removed"] for v in rep2.values()) == 0)

    # 给 kb2(公司制度)也入库文档,供手动兜底问答使用
    f_r1 = DEMO / "公司考勤管理制度.txt"
    dr = c.post(f"/api/doc/add/{kb2['id']}", files={"file": (f_r1.name, f_r1.read_bytes(), "text/plain")}).json()
    check("制度库入库", dr["doc"]["chunk_count"] > 0)

    # ---- 3) 检索问答 ----
    q = "什么是不确定性原理?"
    r = c.post("/api/qa/route", json={"question": q}).json()
    # 说明:演示库「量子物理与计算」与测试库「量子力学」含有相同主题的文档(量子力学基础),
    # 路由命中任一量子类知识库均属正确,故断言"命中含该主题的库"。
    routed_name = r.get("auto_kb_name") or ""
    check("自动路由命中量子主题知识库", "量子" in routed_name, f"got={routed_name}")

    # 流式问答(自动)
    answer_text, citations, evts = _ask_stream(c, {"question": "海森堡不确定性原理说的是什么?", "mode": "auto"})
    check("自动问答返回引用", bool(citations), f"cites={list(citations.keys()) if citations else 'none'}")
    check("回答包含要点", "不确定性" in answer_text or "位置" in answer_text or "动量" in answer_text, answer_text[:80])

    # 手动指定库(兜底)
    answer2, cites2, evts2 = _ask_stream(c, {"question": "公司年假几天?", "mode": "manual", "kb_id": kb2["id"]})
    check("手动指定库检索出答案", "5" in answer2 or "10" in answer2 or "年假" in answer2, answer2[:80])

    # 无关问题 → 拒答不硬编
    answer3, cites3, evts3 = _ask_stream(c, {"question": "今天天气怎么样?", "mode": "auto"})
    noctx = any(e.get("type") == "no_context" for e in evts3)
    check("无关问题拒答(不幻觉)", noctx or "未找到" in answer3, answer3[:100])

    # ---- 4) 自动分类建库 ----
    mix_files = [p for p in sorted(DEMO.iterdir()) if p.name.startswith(("AI芯片", "芯片制造", "半导体设备", "新能源汽车", "充电桩", "动力电池", "咖啡"))]
    files_mix = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in mix_files]
    prep = c.post("/api/classify/prepare", files=files_mix).json()
    check("自动分类产生候选簇", len(prep.get("clusters", [])) >= 2, f"clusters={len(prep.get('clusters', []))}")
    sug = c.post("/api/classify/suggest", json={"session_id": prep["session_id"]}).json()
    check("AI 建议库名", any(s.get("suggested_name") for s in sug["suggestions"]))

    conf = [{"cluster_id": cl["cluster_id"], "confirmed": True,
             "name": cl.get("suggested_name") or f"自动库{cl['cluster_id'][:4]}", "description": "自动分类"} for cl in prep["clusters"]]
    built = c.post("/api/classify/confirm", json={"session_id": prep["session_id"], "clusters": conf}).json()
    created_ok = built.get("created", [])
    total_imported = sum(len(k["imported"]) for k in created_ok)
    check("确认后建库入库", len(created_ok) >= 1 and total_imported >= len(mix_files) * 0.5,
          f"created={len(created_ok)} imported={total_imported}/{len(mix_files)}")

    # 清理:删除测试创建的库(保留? 直接删除保持环境干净)
    for kb in [kb1, kb2]:
        c.delete(f"/api/kb/{kb['id']}")
    for kb in created_ok:
        c.delete(f"/api/kb/{kb['kb_id']}")
    print(f"\n======== 结果: {PASS} 通过 / {FAIL} 失败 ========")
    return 0 if FAIL == 0 else 1


def _ask_stream(c: httpx.Client, payload: dict) -> tuple[str, dict, list[dict]]:
    text = ""
    cites: dict = {}
    evts: list[dict] = []
    with c.stream("POST", "/api/qa/ask", json=payload) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = None
                data = None
                for line in block.splitlines():
                    if line.startswith("event: "):
                        ev = line[7:].strip()
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                if ev and data:
                    evts.append(data)
                    if ev == "delta":
                        text += data.get("text", "")
                    elif ev == "done":
                        cites = data.get("citations", {})
    return text, cites, evts


if __name__ == "__main__":
    sys.exit(main())
