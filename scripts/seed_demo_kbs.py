"""幂等预置演示知识库:先删除同名旧库,再重建并入库。"""
import httpx
from pathlib import Path

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
demo = Path(__file__).resolve().parent / "demo_docs"
plan = [
    ("量子物理与计算", "量子力学、量子计算与量子通信演示库",
     ["量子力学基础.txt", "量子计算导论.md", "量子密钥分发与量子通信.txt"]),
    ("公司管理制度", "考勤与差旅报销制度演示库",
     ["公司考勤管理制度.txt", "差旅报销与费用管理制度.txt"]),
]

# 清掉所有现有库(probe、半成品)
for k in c.get("/api/kb").json()["kbs"]:
    c.delete(f"/api/kb/{k['id']}")
    print(f"cleaned old kb: {k['name']}")

for name, desc, files in plan:
    kb = c.post("/api/kb", json={"name": name, "description": desc}).json()["kb"]
    print(f"created [{name}] {kb['id']}")
    for f in files:
        p = demo / f
        r = c.post(f"/api/doc/add/{kb['id']}", files={"file": (f, p.read_bytes(), "text/plain")})
        print(f"  add {f}: HTTP {r.status_code} {r.text[:60]}")
print("--- KBs now ---")
for k in c.get("/api/kb").json()["kbs"]:
    print(f"  {k['name']}: {k['doc_count']} 文档, {k['chunk_count']} 块")
