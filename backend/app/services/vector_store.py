"""向量库封装 —— 【创新点1】多知识库向量硬隔离。

设计:
- **每个知识库一个独立的 Chroma PersistentClient**,落在独立目录
  `data/vectors/<kb_id>/`,各自拥有独立的 `chroma.sqlite3` 与索引文件;
- 不再是"单实例 + 多 collection(靠元数据标签做逻辑隔离)",而是
  **物理分离**:删库即删目录,不会触碰其它库的一致性与数据文件;
- 实例按 kb_id 缓存复用;删库时先释放客户端再物理删除目录,
  若文件被占用则退化为"重命名隔离 + 启动期清理",保证删除语义成立。

chunk 向量元数据统一携带: doc_id, kb_id, chunk_index, filename,
heading_path, page —— 支撑按 doc_id 精准删除与增量更新。
"""
from __future__ import annotations

import gc
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..core.config import settings

COLLECTION_NAME = "documents"      # 每库独立实例,集合名固定
_LEGACY_COLL_PREFIX = "kb_"


class VectorStore:
    def __init__(self) -> None:
        self.base_dir: Path = settings.vectors_dir  # type: ignore[assignment]
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, Any] = {}
        self._lock = threading.RLock()

    # ---------------- 物理布局 ----------------
    def kb_dir(self, kb_id: str) -> Path:
        """该知识库的独立向量存储目录(物理隔离单元)。"""
        return self.base_dir / kb_id

    def kb_files(self, kb_id: str) -> list[Path]:
        d = self.kb_dir(kb_id)
        if not d.exists():
            return []
        return [p for p in d.rglob("*") if p.is_file()]

    def storage_info(self, kb_id: str) -> dict:
        """返回该库的物理存储信息(用于隔离性验证与前端展示)。"""
        d = self.kb_dir(kb_id)
        files = self.kb_files(kb_id)
        total = sum(p.stat().st_size for p in files)
        return {
            "kb_id": kb_id,
            "path": str(d),
            "exists": d.exists(),
            "file_count": len(files),
            "size_bytes": total,
            "size_kb": round(total / 1024, 1),
            "sqlite": [f.name for f in files if f.name.endswith(".sqlite3")],
        }

    def all_storage_info(self) -> list[dict]:
        out = []
        for d in sorted(self.base_dir.iterdir()) if self.base_dir.exists() else []:
            if d.is_dir() and not d.name.startswith("."):
                out.append(self.storage_info(d.name))
        return out

    # ---------------- 客户端 / 集合 ----------------
    def _client(self, kb_id: str):
        with self._lock:
            c = self._clients.get(kb_id)
            if c is None:
                d = self.kb_dir(kb_id)
                d.mkdir(parents=True, exist_ok=True)
                c = chromadb.PersistentClient(
                    path=str(d),
                    settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
                )
                self._clients[kb_id] = c
            return c

    def get_collection(self, kb_id: str, create: bool = True):
        client = self._client(kb_id)
        try:
            return client.get_collection(COLLECTION_NAME)
        except Exception:
            if not create:
                raise KeyError(f"知识库向量实例不存在: {kb_id}")
            # 显式余弦空间:score = 1 - distance
            return client.create_collection(
                COLLECTION_NAME,
                metadata={"kb_id": kb_id, "hnsw:space": "cosine"},
            )

    # ---------------- 删除(物理) ----------------
    def _release_client(self, kb_id: str) -> None:
        with self._lock:
            self._clients.pop(kb_id, None)
        try:  # 释放 chroma 共享系统缓存,关闭 sqlite 句柄
            from chromadb.api.shared_system_client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:
            pass
        gc.collect()

    def drop_kb(self, kb_id: str) -> dict:
        """【硬隔离】物理删除该知识库的整个向量存储目录。

        返回 {removed, path, mode}:mode=deleted 为彻底删除;若目录被占用
        则先重命名为 .trash_* 完成逻辑隔离,交由启动期清理。
        """
        path = self.kb_dir(kb_id)
        self._release_client(kb_id)
        if not path.exists():
            return {"removed": True, "path": str(path), "mode": "absent"}
        for attempt in range(4):
            try:
                shutil.rmtree(path)
                return {"removed": True, "path": str(path), "mode": "deleted"}
            except Exception:
                time.sleep(0.4)
                self._release_client(kb_id)
        trash = self.base_dir / f".trash_{kb_id}_{int(time.time())}"
        try:
            path.rename(trash)
            return {"removed": True, "path": str(path), "mode": "renamed", "trash": str(trash)}
        except Exception as e:
            return {"removed": False, "path": str(path), "mode": "failed", "error": str(e)}

    def cleanup_trash(self) -> list[str]:
        """启动期清理历史遗留的 .trash_* 目录。"""
        removed = []
        if not self.base_dir.exists():
            return removed
        for d in self.base_dir.iterdir():
            if d.is_dir() and d.name.startswith(".trash_"):
                try:
                    shutil.rmtree(d)
                    removed.append(d.name)
                except Exception:
                    pass
        return removed

    def count(self, kb_id: str) -> int:
        try:
            return self.get_collection(kb_id, create=False).count()
        except KeyError:
            return 0
        except Exception:
            return 0

    # ---------------- 写入 ----------------
    def add_doc_chunks(
        self,
        kb_id: str,
        doc_id: str,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> int:
        """批量写入一篇文档的所有 chunk。调用前需先删除该 doc_id 的旧数据。"""
        coll = self.get_collection(kb_id)
        ids = [f"{doc_id}:{c['index']}" for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas: list[dict[str, Any]] = [
            {
                "doc_id": doc_id,
                "kb_id": kb_id,
                "chunk_index": c["index"],
                "filename": c.get("filename", ""),
                "heading_path": c.get("heading_path", ""),
                "page": c.get("page") or -1,
                "chunk_hash": c.get("chunk_hash", ""),
            }
            for c in chunks
        ]
        if ids:
            coll.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        return len(ids)

    def get_vectors(self, kb_id: str, ids: Iterable[str]) -> dict[str, list[float]]:
        """按 chunk id 取回已存向量(供增量更新复用,避免重复嵌入)。"""
        id_list = list(ids)
        if not id_list:
            return {}
        coll = self.get_collection(kb_id, create=False)
        out: dict[str, list[float]] = {}
        try:
            res = coll.get(ids=id_list, include=["embeddings"])
        except Exception:
            return {}
        got_ids = res.get("ids") or []
        embs = res.get("embeddings")
        if embs is None:
            return {}
        for i, cid in enumerate(got_ids):
            try:
                out[cid] = list(embs[i])
            except Exception:
                continue
        return out

    def get_chunk_records(self, kb_id: str, doc_id: str) -> list[dict]:
        """取某文档的全部 chunk 记录(id/text/meta/embedding),用于增量比对。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return []
        rows: list[dict] = []
        try:
            res = coll.get(where={"doc_id": doc_id}, include=["documents", "metadatas", "embeddings"])
        except Exception:
            return []
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        embs = res.get("embeddings")
        for i, cid in enumerate(ids):
            rows.append({
                "id": cid,
                "text": docs[i] if i < len(docs) else "",
                "meta": metas[i] if i < len(metas) else {},
                "embedding": list(embs[i]) if embs is not None and i < len(embs) else None,
            })
        return rows

    def delete_doc(self, kb_id: str, doc_id: str) -> int:
        """删除某篇文档在该库中的全部向量,返回删除条数(按 doc_id 精准定位)。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return 0
        res = coll.get(where={"doc_id": doc_id}, include=[])
        ids = res.get("ids", [])
        if ids:
            coll.delete(ids=ids)
        return len(ids)

    def count_doc_chunks(self, kb_id: str, doc_id: str) -> int:
        """校验用:该文档在当前库中还剩多少向量(应为 0)。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return 0
        try:
            res = coll.get(where={"doc_id": doc_id}, include=[])
            return len(res.get("ids", []))
        except Exception:
            return 0

    # ---------------- 查询 ----------------
    def query(
        self,
        kb_id: str,
        query_embedding: list[float],
        top_k: int | None = None,
        where: dict | None = None,
    ) -> list[dict]:
        """返回 [{id, text, score, meta}] 按相关度降序。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return []
        n = top_k or settings.top_k
        total = coll.count()
        if total == 0:
            return []
        res = coll.query(
            query_embeddings=[query_embedding],
            n_results=min(n, total),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out: list[dict] = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        for i in range(len(docs)):
            d = dists[i] if i < len(dists) else 1.0
            # Chroma 余弦距离 = 1 - cos,相似度 = 1 - distance
            score = max(0.0, min(1.0, 1.0 - float(d)))
            out.append({
                "id": ids[i] if i < len(ids) else "",
                "text": docs[i],
                "score": round(score, 4),
                "meta": metas[i] if i < len(metas) else {},
            })
        return out

    def all_doc_ids(self, kb_id: str) -> list[str]:
        """列出该库中所有出现过(含孤儿)的 doc_id,用于对账。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return []
        ids: list[str] = []
        offset = 0
        while True:
            batch = coll.get(include=["metadatas"], limit=1000, offset=offset)
            metas = batch.get("metadatas", [])
            ids.extend(m.get("doc_id", "") for m in metas if m.get("doc_id"))
            if len(batch.get("ids", [])) < 1000:
                break
            offset += 1000
        return sorted(set(ids))

    def all_chunks(self, kb_id: str) -> list[dict]:
        """读取该库全部块(id / 文本 / 元数据),供词法索引(BM25)构建使用。"""
        try:
            coll = self.get_collection(kb_id, create=False)
        except KeyError:
            return []
        rows: list[dict] = []
        offset = 0
        while True:
            try:
                batch = coll.get(include=["documents", "metadatas"], limit=1000, offset=offset)
            except Exception:
                break
            ids = batch.get("ids") or []
            if not ids:
                break
            docs = batch.get("documents") or []
            metas = batch.get("metadatas") or []
            for i, cid in enumerate(ids):
                rows.append({
                    "id": cid,
                    "text": docs[i] if i < len(docs) else "",
                    "meta": metas[i] if i < len(metas) else {},
                })
            if len(ids) < 1000:
                break
            offset += 1000
        return rows


vector_store = VectorStore()
