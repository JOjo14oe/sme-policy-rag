"""文档服务:解析 → 切分 → 向量化 → 入库;增量更新;精准删除;对账清理。

核心不变量
----------
1. SQLite `documents` 是文档权威源;向量库中每个 chunk 均携带 doc_id;
   任何更新/删除都按 doc_id 精准定位 —— 旧数据零残留。
2. 【创新点2】**块级增量更新**:以 chunk 内容哈希为准,未变更的块直接复用
   已存向量,只对新增/修改的块调用嵌入模型 —— 不再整篇重新向量化,
   显著降低更新耗时与算力开销;删除单篇文档时按 doc_id 精准清除其全部向量。
3. 全部变更写入 `update_log`,用于量化增量收益(复用率/耗时对比)。
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..core import db
from ..core.config import settings
from ..core.db import tx
from ..core.ollama_client import OllamaError, ollama
from ..rag import parsers
from ..rag.chunker import chunk_sections
from . import kb_service, policy_service
from .vector_store import vector_store

# 【增强A】制度元数据字段(可写入的列)
META_FIELDS = ("doc_no", "title", "effective_date", "expiry_date",
               "doc_status", "issuer", "dept_scope", "supersedes")


class DocError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    try:
        return ollama.embed(texts)
    except OllamaError as e:
        raise DocError(str(e)) from e


def _invalidate_lexical(kb_id: str) -> None:
    """文档/向量变更后失效该库的词法索引(BM25),保证混合检索的时效性。"""
    try:
        from .lexical_index import lexical_cache

        lexical_cache.invalidate(kb_id)
    except Exception:
        pass


_WS = re.compile(r"\s+")


def chunk_hash(text: str) -> str:
    """块内容指纹:归一化空白后取 sha256,用于增量比对。"""
    norm = _WS.sub(" ", (text or "").strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def parse_document(filename: str, data: bytes) -> list[dict]:
    """仅解析并切分(不落库)。返回块均带 chunk_hash。"""
    sections = parsers.parse_file(filename, data)
    chunks = chunk_sections(sections)
    for c in chunks:
        c.setdefault("filename", Path(filename).name)
        c["chunk_hash"] = chunk_hash(c["text"])
    return chunks


# ------------------------------------------------------------------ 块级账本
def _save_ledger(conn, kb_id: str, doc_id: str, chunks: list[dict]) -> None:
    conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
    conn.executemany(
        "INSERT INTO doc_chunks(doc_id, kb_id, chunk_index, chunk_hash, text_len) VALUES (?,?,?,?,?)",
        [(doc_id, kb_id, c["index"], c["chunk_hash"], len(c["text"])) for c in chunks],
    )


def _load_old_ledger(doc_id: str) -> dict[str, list[int]]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT chunk_index, chunk_hash FROM doc_chunks WHERE doc_id=? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:
        out.setdefault(r["chunk_hash"], []).append(int(r["chunk_index"]))
    return out


def _log_update(kb_id: str, doc_id: str, filename: str, total: int, reused: int,
                embedded: int, actual_ms: int, est_full_ms: int) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO update_log(doc_id, kb_id, filename, total_chunks, reused_chunks,
                                      embedded_chunks, actual_ms, estimated_full_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (doc_id, kb_id, filename, total, reused, embedded, actual_ms, est_full_ms, _now()),
        )


def update_stats(kb_id: str | None = None) -> dict:
    """增量更新累计收益(供总结报告与系统页展示)。"""
    sql = ("SELECT COUNT(*) AS n, COALESCE(SUM(total_chunks),0) AS total, "
           "COALESCE(SUM(reused_chunks),0) AS reused, COALESCE(SUM(embedded_chunks),0) AS embedded, "
           "COALESCE(SUM(actual_ms),0) AS actual_ms, COALESCE(SUM(estimated_full_ms),0) AS est_ms "
           "FROM update_log")
    args: tuple = ()
    if kb_id:
        sql += " WHERE kb_id=?"
        args = (kb_id,)
    with tx() as conn:
        row = conn.execute(sql, args).fetchone()
    total = int(row["total"] or 0)
    reused = int(row["reused"] or 0)
    return {
        "updates": int(row["n"] or 0),
        "chunks_total": total,
        "chunks_reused": reused,
        "chunks_embedded": int(row["embedded"] or 0),
        "reuse_ratio": round(reused / total, 4) if total else 0.0,
        "actual_ms": int(row["actual_ms"] or 0),
        "estimated_full_reembed_ms": int(row["est_ms"] or 0),
        "saved_ms": max(0, int(row["est_ms"] or 0) - int(row["actual_ms"] or 0)),
    }


# ------------------------------------------------------------------ 新增
def add_document(kb_id: str, filename: str, data: bytes, overwrite: bool = False,
                 meta: dict | None = None) -> dict:
    """向指定知识库新增文档。同库内内容相同时默认拒绝重复(overwrite=True 替换)。

    meta 可携带制度元数据:doc_no / title / effective_date / expiry_date /
    doc_status / issuer / dept_scope / supersedes。
    """
    kb_service.get_kb(kb_id)  # 校验存在
    fname = Path(filename).name
    ext = Path(fname).suffix.lower()
    file_hash = parsers.sha256_bytes(data)

    with tx() as conn:
        dup = conn.execute(
            "SELECT doc_id, filename FROM documents WHERE kb_id=? AND file_hash=?",
            (kb_id, file_hash),
        ).fetchone()
    if dup and not overwrite:
        raise DocError(f"该内容已存在(文件: {dup['filename']}),如需覆盖请使用『更新』功能")

    chunks = parse_document(fname, data)
    if not chunks:
        raise DocError("文档未能提取到有效文本(可能为扫描件/图片 PDF,暂不支持 OCR)")

    embeddings = _embed_batch([c["text"] for c in chunks])
    if not embeddings or len(embeddings) != len(chunks):
        raise DocError("向量化失败,请检查 Ollama 嵌入模型")

    if overwrite and dup:
        delete_document(kb_id, dup["doc_id"], archive=False)
    doc_id = uuid.uuid4().hex
    _write_new(kb_id, doc_id, fname, ext, file_hash, data, chunks, embeddings, meta)
    applied = _apply_supersede_links(kb_id, doc_id)
    doc = get_document(kb_id, doc_id)
    if applied:
        doc["supersede_links"] = applied
    return doc


def _write_new(kb_id: str, doc_id: str, fname: str, ext: str, file_hash: str,
               data: bytes, chunks: list[dict], embeddings: list[list[float]],
               meta: dict | None = None) -> None:
    meta = _clean_meta(meta)
    # 1) 向量(每库独立实例)
    vector_store.add_doc_chunks(kb_id, doc_id, chunks, embeddings)
    _invalidate_lexical(kb_id)
    # 2) 原文归档
    kb_dir = settings.files_dir / kb_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{kb_id}/{doc_id}{ext}"
    (settings.files_dir / rel_path).write_bytes(data)
    # 3) 元数据 + 制度属性 + 块级账本
    doc_emb = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0).tolist()
    now = _now()
    with tx() as conn:
        conn.execute(
            """INSERT INTO documents(doc_id, kb_id, filename, ext, file_hash, file_path,
                                     chunk_count, embedding, version, created_at, updated_at,
                                     doc_no, title, effective_date, expiry_date, doc_status,
                                     issuer, dept_scope, supersedes)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, kb_id, fname, ext, file_hash, rel_path, len(chunks),
             db.vec_to_blob(doc_emb), now, now,
             meta.get("doc_no", ""), meta.get("title", "") or Path(fname).stem,
             meta.get("effective_date", ""), meta.get("expiry_date", ""),
             meta.get("doc_status", "effective"), meta.get("issuer", ""),
             meta.get("dept_scope", ""), meta.get("supersedes", "")),
        )
        _save_ledger(conn, kb_id, doc_id, chunks)
    kb_service.update_profile(kb_id, doc_emb, delta=+1)


def _clean_meta(meta: dict | None) -> dict:
    """规整制度元数据:仅保留已知字段、统一日期格式、限制状态取值。"""
    out: dict = {}
    meta = meta or {}
    for k in META_FIELDS:
        v = meta.get(k)
        if v is None:
            continue
        out[k] = str(v).strip()
    for k in ("effective_date", "expiry_date"):
        if out.get(k):
            d = policy_service.parse_date(out[k])
            out[k] = d.isoformat() if d else ""
    if out.get("doc_status"):
        st = out["doc_status"].strip().lower()
        out["doc_status"] = st if st in (policy_service.ST_EFFECTIVE, policy_service.ST_SUPERSEDED,
                                         policy_service.ST_DRAFT, policy_service.ST_EXPIRED) else policy_service.ST_EFFECTIVE
    return out


def _apply_supersede_links(kb_id: str, doc_id: str) -> list[dict]:
    """若本文档声明替代了某编号,则把同库内该编号的文档标记为「已被替代」。"""
    with tx() as conn:
        row = conn.execute("SELECT doc_no, supersedes FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row or not row["supersedes"]:
            return []
        old_no = row["supersedes"]
        targets = conn.execute(
            "SELECT doc_id, filename, doc_no FROM documents WHERE kb_id=? AND doc_no=? AND doc_id<>?",
            (kb_id, old_no, doc_id),
        ).fetchall()
        applied = []
        for t in targets:
            conn.execute(
                "UPDATE documents SET doc_status=?, superseded_by=?, updated_at=? WHERE doc_id=?",
                (policy_service.ST_SUPERSEDED, row["doc_no"] or "", _now(), t["doc_id"]),
            )
            applied.append({"doc_id": t["doc_id"], "filename": t["filename"], "superseded_by": row["doc_no"]})
        return applied


# ------------------------------------------------------------------ 查询
def get_document(kb_id: str, doc_id: str) -> dict:
    with tx() as conn:
        row = conn.execute("SELECT * FROM documents WHERE doc_id=? AND kb_id=?", (doc_id, kb_id)).fetchone()
    if not row:
        raise DocError(f"文档不存在: {doc_id}")
    d = dict(row)
    d["embedding"] = None  # 不向前端吐大向量
    return policy_service.enrich(d)


def list_documents(kb_id: str) -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT doc_id, filename, ext, file_hash, chunk_count, version, created_at, updated_at, "
            "doc_no, title, effective_date, expiry_date, doc_status, issuer, dept_scope, "
            "supersedes, superseded_by "
            "FROM documents WHERE kb_id=? ORDER BY created_at DESC",
            (kb_id,),
        ).fetchall()
    return policy_service.enrich_many([dict(r) for r in rows])


def doc_meta_map(kb_id: str, doc_ids: list[str] | None = None) -> dict[str, dict]:
    """取文档元数据映射(供检索按制度状态过滤)。"""
    sql = ("SELECT doc_id, filename, title, doc_no, effective_date, expiry_date, doc_status, "
           "issuer, dept_scope, supersedes, superseded_by, updated_at FROM documents WHERE kb_id=?")
    args: list = [kb_id]
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        sql += f" AND doc_id IN ({placeholders})"
        args += list(doc_ids)
    with tx() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {r["doc_id"]: dict(r) for r in rows}


def update_meta(kb_id: str, doc_id: str, meta: dict) -> dict:
    """【增强A】更新制度属性(文件编号/生效期/状态/发布部门/适用范围/替代关系)。"""
    get_document(kb_id, doc_id)      # 存在性校验
    clean = _clean_meta(meta)
    if not clean:
        return get_document(kb_id, doc_id)
    sets = ", ".join(f"{k}=?" for k in clean)
    with tx() as conn:
        conn.execute(
            f"UPDATE documents SET {sets}, updated_at=? WHERE doc_id=? AND kb_id=?",
            (*clean.values(), _now(), doc_id, kb_id),
        )
    applied = _apply_supersede_links(kb_id, doc_id)
    doc = get_document(kb_id, doc_id)
    if applied:
        doc["supersede_links"] = applied
    return doc


def version_chain(kb_id: str, doc_no: str | None = None, doc_id: str | None = None) -> list[dict]:
    """【增强A】返回同一制度的版本链(按生效日期从旧到新)。"""
    docs = list_documents(kb_id)
    if doc_id:
        base = next((d for d in docs if d["doc_id"] == doc_id), None)
        if not base:
            raise DocError(f"文档不存在: {doc_id}")
        family = [d for d in docs if policy_service.same_policy(d, base)]
    elif doc_no:
        family = [d for d in docs if (d.get("doc_no") or "") == doc_no
                  or (d.get("supersedes") or "") == doc_no
                  or (d.get("superseded_by") or "") == doc_no]
    else:
        family = []
    return policy_service.build_version_chain(family)


def expiring(kb_id: str | None = None, days: int | None = None) -> list[dict]:
    """【增强A】即将到期的现行制度清单。"""
    kbs = [kb_service.get_kb(kb_id)] if kb_id else kb_service.list_kbs()
    pairs: list[tuple[dict, str]] = []
    for kb in kbs:
        for d in list_documents(kb["id"]):
            pairs.append((d, kb["name"]))
    return policy_service.expiring_docs(pairs, days)


# ------------------------------------------------------------------ 增量更新
def update_document(kb_id: str, doc_id: str, filename: str, data: bytes) -> dict:
    """【创新点2】块级增量更新。

    流程:解析新内容并切块 → 计算块哈希 → 与旧账本比对 →
      未变更块:直接复用向量库中的既有向量(零嵌入开销)
      新增/修改块:仅对这些块调用嵌入模型
      → 按 doc_id 清除旧向量 → 写入新向量集 → 更新账本/版本/质心 → 记录收益
    """
    with tx() as conn:
        old_row = conn.execute(
            "SELECT * FROM documents WHERE doc_id=? AND kb_id=?", (doc_id, kb_id)
        ).fetchone()
    if not old_row:
        raise DocError(f"文档不存在: {doc_id}")
    old = dict(old_row)                        # 含 embedding blob,用于质心回退
    fname = Path(filename).name
    ext = Path(fname).suffix.lower()
    file_hash = parsers.sha256_bytes(data)

    t0 = time.perf_counter()
    new_chunks = parse_document(fname, data)
    if not new_chunks:
        raise DocError("文档未能提取到有效文本")

    # 旧向量记录:hash -> [embedding,...](同哈希可能有多个块,按序取用)
    old_records = vector_store.get_chunk_records(kb_id, doc_id)
    old_pool: dict[str, list[list[float]]] = {}
    for rec in old_records:
        h = (rec.get("meta") or {}).get("chunk_hash") or chunk_hash(rec.get("text", ""))
        if rec.get("embedding"):
            old_pool.setdefault(h, []).append(rec["embedding"])

    # 逐块分配:复用 or 待嵌入
    embeddings: list[list[float] | None] = [None] * len(new_chunks)
    pending_idx: list[int] = []
    reused = 0
    for i, c in enumerate(new_chunks):
        pool = old_pool.get(c["chunk_hash"])
        if pool:
            embeddings[i] = pool.pop(0)      # 复用既有向量
            reused += 1
        else:
            pending_idx.append(i)

    # 仅对变更块做嵌入
    embed_ms = 0
    if pending_idx:
        t_embed = time.perf_counter()
        fresh = _embed_batch([new_chunks[i]["text"] for i in pending_idx])
        embed_ms = int((time.perf_counter() - t_embed) * 1000)
        if len(fresh) != len(pending_idx):
            raise DocError("向量化失败,请检查 Ollama 嵌入模型")
        for slot, vec in zip(pending_idx, fresh):
            embeddings[slot] = vec

    final_embeddings = [e if e is not None else [] for e in embeddings]
    if any(not e for e in final_embeddings):
        raise DocError("增量向量化结果不完整")

    # 清理旧向量 → 写入新向量(严格按 doc_id 定位,避免残留)
    removed_old = vector_store.delete_doc(kb_id, doc_id)
    vector_store.add_doc_chunks(kb_id, doc_id, new_chunks, final_embeddings)
    _invalidate_lexical(kb_id)

    # 归档原文(覆盖)
    kb_dir = settings.files_dir / kb_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{kb_id}/{doc_id}{ext}"
    old_archive = settings.files_dir / old["file_path"]
    (settings.files_dir / rel_path).write_bytes(data)
    if old_archive != (settings.files_dir / rel_path) and old_archive.exists():
        try:
            old_archive.unlink()
        except Exception:
            pass

    new_doc_emb = np.mean(np.asarray(final_embeddings, dtype=np.float32), axis=0).tolist()
    now = _now()
    with tx() as conn:
        conn.execute(
            """UPDATE documents SET filename=?, ext=?, file_hash=?, file_path=?, chunk_count=?,
                                    embedding=?, version=version+1, updated_at=?
               WHERE doc_id=? AND kb_id=?""",
            (fname, ext, file_hash, rel_path, len(new_chunks),
             db.vec_to_blob(new_doc_emb), now, doc_id, kb_id),
        )
        _save_ledger(conn, kb_id, doc_id, new_chunks)

    # 质心:先退旧再进新
    old_emb = db.blob_to_vec(old.get("embedding")) if old.get("embedding") else None
    if old_emb:
        kb_service.update_profile(kb_id, old_emb, delta=-1)
    kb_service.update_profile(kb_id, new_doc_emb, delta=+1)

    actual_ms = int((time.perf_counter() - t0) * 1000)
    embedded = len(pending_idx)
    # 全量重嵌入的估算耗时(按本次实际嵌入速率折算)
    per_chunk_ms = (embed_ms / embedded) if embedded else 0
    est_full_ms = int(per_chunk_ms * len(new_chunks)) + (actual_ms - embed_ms)
    _log_update(kb_id, doc_id, fname, len(new_chunks), reused, embedded, actual_ms, est_full_ms)

    doc = get_document(kb_id, doc_id)
    doc["update_stats"] = {
        "total_chunks": len(new_chunks),
        "reused_chunks": reused,
        "embedded_chunks": embedded,
        "reuse_ratio": round(reused / len(new_chunks), 4) if new_chunks else 0.0,
        "removed_old_vectors": removed_old,
        "embed_ms": embed_ms,
        "actual_ms": actual_ms,
        "estimated_full_reembed_ms": est_full_ms,
        "time_saved_ms": max(0, est_full_ms - actual_ms),
    }
    return doc


# ------------------------------------------------------------------ 精准删除
def delete_document(kb_id: str, doc_id: str, archive: bool = True) -> dict:
    """按 doc_id 精准删除:清向量 → 清账本 → 删元数据 → 删原文,并校验零残留。"""
    with tx() as conn:
        row = conn.execute("SELECT * FROM documents WHERE doc_id=? AND kb_id=?", (doc_id, kb_id)).fetchone()
    if not row:
        raise DocError(f"文档不存在: {doc_id}")
    emb = db.blob_to_vec(row["embedding"]) if row["embedding"] else None

    deleted = vector_store.delete_doc(kb_id, doc_id)
    _invalidate_lexical(kb_id)
    remaining = vector_store.count_doc_chunks(kb_id, doc_id)   # 二次校验:确认零残留
    with tx() as conn:
        conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE doc_id=? AND kb_id=?", (doc_id, kb_id))
    if archive:
        p = settings.files_dir / row["file_path"]
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    kb_service.update_profile(kb_id, emb, delta=-1)
    return {
        "doc_id": doc_id,
        "deleted_chunks": deleted,
        "remaining_chunks": remaining,
        "zero_residue": remaining == 0,
        "ok": True,
    }


# ------------------------------------------------------------------ 对账
def reconcile(kb_id: str | None = None) -> dict:
    """对账:清理向量侧孤儿 doc_id(元数据已无)并核对账本一致性。"""
    kbs = [kb_service.get_kb(kb_id)] if kb_id else kb_service.list_kbs()
    report = {}
    for kb in kbs:
        kid = kb["id"]
        with tx() as conn:
            rows = conn.execute("SELECT doc_id, chunk_count FROM documents WHERE kb_id=?", (kid,)).fetchall()
            ledger_rows = conn.execute(
                "SELECT doc_id, COUNT(*) AS c FROM doc_chunks WHERE kb_id=? GROUP BY doc_id", (kid,)
            ).fetchall()
        valid = {r["doc_id"] for r in rows}
        vector_ids = vector_store.all_doc_ids(kid)
        orphans = [vid for vid in vector_ids if vid not in valid]
        removed = 0
        for oid in orphans:
            removed += vector_store.delete_doc(kid, oid)
        # 清理账本中已不存在的文档
        stale_ledger = [r["doc_id"] for r in ledger_rows if r["doc_id"] not in valid]
        if stale_ledger:
            with tx() as conn:
                for sid in stale_ledger:
                    conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (sid,))
        report[kid] = {
            "orphan_docs": orphans,
            "orphan_chunks_removed": removed,
            "stale_ledger_docs_cleaned": stale_ledger,
            "docs_in_db": len(valid),
            "docs_in_vector": len(vector_ids),
        }
    return report


def get_archive_path(kb_id: str, doc_id: str) -> Path:
    row = get_document(kb_id, doc_id)
    return settings.files_dir / row["file_path"]
