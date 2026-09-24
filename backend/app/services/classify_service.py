"""AI 自动分类建库服务(带内存会话,支持"确认前可调整")。

流程:
1. POST /prepare 批量上传无标签混杂文档 → 解析采样 → 嵌入 → 聚类
   → 返回 session_id + 候选簇(含建议名,此时尚未建库);
2. 用户在前端可改名/改描述/勾选确认 → POST /confirm {session_id, clusters[]}
   → 为每个确认簇建库并批量入库;未确认簇中的文档进入"待人工处理"。

会话数据仅存内存(单用户本地场景),数据本身不落库直到用户确认 —— 人工可控。
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..core.config import settings
from ..core.ollama_client import OllamaError, ollama
from ..rag import parsers
from ..rag.chunker import chunk_sections
from . import doc_service, kb_service


@dataclass
class ClassifyItem:
    filename: str
    data: bytes
    sample_text: str
    vector: list[float] = field(default_factory=list)


@dataclass
class ClusterSession:
    session_id: str
    items: list[ClassifyItem] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)  # 簇视图(不含 items)
    created_at: str = ""


_sessions: dict[str, ClusterSession] = {}
_lock = threading.Lock()
_SESSION_TTL_SECONDS = 60 * 60  # 1 小时后过期


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cosine(a, b) -> float:
    if a is None or b is None:
        return 0.0
    aa = np.asarray(a, dtype=np.float32)
    bb = np.asarray(b, dtype=np.float32)
    if aa.size == 0 or bb.size == 0 or aa.size != bb.size:
        return 0.0
    na = float(np.linalg.norm(aa))
    nb = float(np.linalg.norm(bb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(aa, bb) / (na * nb))


def prepare_session(files: list[tuple[str, bytes]]) -> dict:
    """解析 + 采样 + 嵌入 + 聚类,返回 {session_id, clusters, skipped}。"""
    items: list[ClassifyItem] = []
    skipped: list[str] = []
    for fname, data in files:
        try:
            sections = parsers.parse_file(fname, data)
            chunks = chunk_sections(sections)
            if not chunks:
                skipped.append(fname)
                continue
            sample = " ".join(c["text"] for c in chunks)[: settings.classify_doc_sample_chars].strip()
            if not sample:
                skipped.append(fname)
                continue
            items.append(ClassifyItem(filename=fname, data=data, sample_text=sample))
        except Exception:
            skipped.append(fname)

    if items:
        vectors = ollama.embed([it.sample_text for it in items])
        for it, v in zip(items, vectors):
            it.vector = v

    session = ClusterSession(
        session_id=uuid.uuid4().hex[:12],
        items=items,
        created_at=_now(),
    )
    session.clusters = _cluster_views(items)
    with _lock:
        # 清理过期会话
        expired = [k for k, v in _sessions.items()
                   if (datetime.now(timezone.utc) - datetime.fromisoformat(v.created_at)).total_seconds()
                   > _SESSION_TTL_SECONDS]
        for k in expired:
            del _sessions[k]
        _sessions[session.session_id] = session

    return {
        "session_id": session.session_id,
        "clusters": session.clusters,
        "total_files": len(files),
        "clustered_files": len(items),
        "skipped": skipped,
    }


def _cluster_views(items: list[ClassifyItem]) -> list[dict]:
    """贪心增量聚类(质心余弦相似度),生成簇视图。"""
    threshold = settings.cluster_sim_threshold
    clusters: list[dict] = []  # 内部: {centroid, indices:[idx]}
    for idx, it in enumerate(items):
        if not it.vector:
            continue
        best_i, best_s = -1, -1.0
        for ci, cl in enumerate(clusters):
            s = _cosine(it.vector, cl["centroid"])
            if s > best_s:
                best_s, best_i = s, ci
        if best_i >= 0 and best_s >= threshold:
            cl = clusters[best_i]
            cl["indices"].append(idx)
            arr = np.asarray(cl["centroid"], dtype=np.float32) * (len(cl["indices"]) - 1)
            arr = (arr + np.asarray(it.vector, dtype=np.float32)) / len(cl["indices"])
            cl["centroid"] = arr
        else:
            clusters.append({"centroid": list(it.vector), "indices": [idx]})

    views: list[dict] = []
    for cl in sorted(clusters, key=lambda c: len(c["indices"]), reverse=True):
        idxs = cl["indices"]
        filenames = [items[i].filename for i in idxs]
        sample = items[idxs[0]].sample_text[:300]
        views.append({
            "cluster_id": uuid.uuid4().hex[:8],
            "size": len(idxs),
            "filenames": filenames,
            "sample_text": sample,
            "suggested_name": "",
            "suggested_desc": "",
            "_indices": idxs,
        })
    return views


_NAME_PROMPT = """你是资料管理员。下面是一组文档片段(来自若干同主题文档),请为这批资料:
1. 拟定一个简洁的中文知识库名称(不超过 8 字);
2. 写一句不超过 30 字的描述。

只输出 JSON,格式: {"name": "...", "description": "..."}。不要输出其它文字。"""


def _suggest_one(filenames: list[str], sample_text: str) -> dict:
    user = "文档列表:\n" + "\n".join(f"- {f}" for f in filenames[:20]) + f"\n\n内容片段:\n{sample_text[:1200]}"
    try:
        text = ollama.chat([
            {"role": "system", "content": _NAME_PROMPT},
            {"role": "user", "content": user},
        ], temperature=0.2, max_tokens=160)
        m = text.replace("```json", "").replace("```", "").strip()
        start, end = m.find("{"), m.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(m[start:end + 1])
            name = str(obj.get("name", "")).strip().strip('"')
            desc = str(obj.get("description", "")).strip().strip('"')
            if name:
                return {"name": name[:30], "description": desc[:120]}
    except Exception:
        pass
    base = filenames[0].rsplit(".", 1)[0] if filenames else "未命名"
    return {"name": base[:30], "description": "由 AI 自动分类生成"}


def suggest_names(session_id: str) -> list[dict]:
    """为会话内各簇生成建议库名(前端可逐个触发,也可整体触发)。"""
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        raise ValueError("分类会话不存在或已过期,请重新上传")
    out = []
    for cl in session.clusters:
        sug = _suggest_one(cl["filenames"], cl["sample_text"])
        cl["suggested_name"] = sug["name"]
        cl["suggested_desc"] = sug["description"]
        out.append({
            "cluster_id": cl["cluster_id"],
            "suggested_name": sug["name"],
            "suggested_desc": sug["description"],
        })
    return out


def confirm_build(session_id: str, confirmations: list[dict]) -> dict:
    """按用户确认建库。confirmations: [{cluster_id, confirmed, name, description}]。"""
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        raise ValueError("分类会话不存在或已过期,请重新上传")
    idx_by_cid = {cl["cluster_id"]: cl.get("_indices", []) for cl in session.clusters}

    created: list[dict] = []
    errors: list[dict] = []
    pending: list[str] = []  # 未被确认簇中的文档
    confirmed_ids = {c["cluster_id"] for c in confirmations if c.get("confirmed")}
    confirmed_set = set()
    for c in confirmations:
        if not c.get("confirmed"):
            continue
        cid = c.get("cluster_id")
        if cid not in idx_by_cid:
            continue
        confirmed_set.add(cid)
        indices = idx_by_cid[cid]
        name = (c.get("name") or "").strip() or "未命名知识库"
        desc = (c.get("description") or "").strip()
        try:
            kb = kb_service.create_kb(name, desc)
        except Exception as e:
            errors.append({"cluster_id": cid, "name": name, "message": str(e)})
            continue
        ok, fail = [], []
        for i in indices:
            it = session.items[i]
            try:
                doc_service.add_document(kb["id"], it.filename, it.data)
                ok.append(it.filename)
            except Exception as e:
                fail.append({"filename": it.filename, "message": str(e)})
        created.append({"kb_id": kb["id"], "name": name, "imported": ok, "failed": fail})

    # 未确认簇文档 → 待人工处理
    for cl in session.clusters:
        if cl["cluster_id"] not in confirmed_set:
            pending.extend(session.items[i].filename for i in cl.get("_indices", []))
    with _lock:
        _sessions.pop(session_id, None)
    return {"created": created, "errors": errors, "pending": pending}
