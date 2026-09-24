"""知识库服务:建库/删库/列表/统计。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..core import db
from ..core.db import tx
from .vector_store import vector_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vec_avg(vec: list[float]) -> list[float]:
    """列表归一为均值向量。"""
    if not vec:
        return []
    n = len(vec)
    if n == 1:
        return vec[0]
    import numpy as np

    arr = np.asarray(vec, dtype=np.float32)
    return np.mean(arr, axis=0).tolist()


class KbNotFound(KeyError):
    pass


def create_kb(name: str, description: str = "", visibility: str = "all",
              allowed_depts: str = "", owner_dept: str = "", doc_category: str = "") -> dict:
    """建库。【增强B】visibility=all(全员)/restricted(仅 allowed_depts 内部门可见)。"""
    name = name.strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    vis = (visibility or "all").strip().lower()
    if vis not in ("all", "restricted"):
        vis = "all"
    kb_id = uuid.uuid4().hex[:16]
    now = _now()
    try:
        with tx() as conn:
            conn.execute(
                "INSERT INTO kb(id, name, description, created_at, updated_at, emb_cnt, "
                "visibility, allowed_depts, owner_dept, doc_category) VALUES (?,?,?,?,?,0,?,?,?,?)",
                (kb_id, name, description, now, now, vis,
                 _norm_depts(allowed_depts), (owner_dept or "").strip(), (doc_category or "").strip()),
            )
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            raise ValueError(f"知识库名称已存在: {name}") from e
        raise
    vector_store.get_collection(kb_id, create=True)
    return get_kb(kb_id)


def _norm_depts(value: str | None) -> str:
    """部门列表归一化为逗号分隔字符串。"""
    if not value:
        return ""
    raw = str(value).replace(";", ",").replace("、", ",").replace(" ", ",")
    return ",".join([d.strip() for d in raw.split(",") if d.strip()])


def get_kb(kb_id: str) -> dict:
    with tx() as conn:
        row = conn.execute("SELECT * FROM kb WHERE id=?", (kb_id,)).fetchone()
    if not row:
        raise KbNotFound(kb_id)
    emb = db.blob_to_vec(row["emb_sum"])
    d = dict(row)
    d["doc_count"] = _doc_count_kb(kb_id)
    d["chunk_count"] = vector_store.count(kb_id)
    d["has_profile"] = bool(emb) and row["emb_cnt"] > 0
    d["storage"] = vector_store.storage_info(kb_id)   # 【创新点1】物理存储位置
    return d


def _doc_count_kb(kb_id: str) -> int:
    with tx() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM documents WHERE kb_id=?", (kb_id,)).fetchone()
    return int(row["c"]) if row else 0


def list_kbs() -> list[dict]:
    with tx() as conn:
        rows = conn.execute("SELECT * FROM kb ORDER BY created_at ASC").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        emb = db.blob_to_vec(d.pop("emb_sum", None))
        d["doc_count"] = _doc_count_kb(d["id"])
        d["chunk_count"] = vector_store.count(d["id"])
        d["has_profile"] = bool(emb) and row["emb_cnt"] > 0
        d["storage"] = vector_store.storage_info(d["id"])   # 【创新点1】
        out.append(d)
    return out


def rename_kb(kb_id: str, name: str | None = None, description: str | None = None,
              visibility: str | None = None, allowed_depts: str | None = None,
              owner_dept: str | None = None, doc_category: str | None = None) -> dict:
    """更新库信息与【增强B】可见范围设置。"""
    with tx() as conn:
        cur = conn.execute("SELECT * FROM kb WHERE id=?", (kb_id,)).fetchone()
        if not cur:
            raise KbNotFound(kb_id)
        new_name = cur["name"] if name is None else name.strip()
        new_desc = cur["description"] if description is None else description
        if not new_name:
            raise ValueError("知识库名称不能为空")
        vis = cur["visibility"] if visibility is None else (visibility or "all").strip().lower()
        if vis not in ("all", "restricted"):
            vis = "all"
        depts = cur["allowed_depts"] if allowed_depts is None else _norm_depts(allowed_depts)
        owner = cur["owner_dept"] if owner_dept is None else (owner_dept or "").strip()
        cat = cur["doc_category"] if doc_category is None else (doc_category or "").strip()
        conn.execute(
            "UPDATE kb SET name=?, description=?, visibility=?, allowed_depts=?, owner_dept=?, "
            "doc_category=?, updated_at=? WHERE id=?",
            (new_name, new_desc, vis, depts, owner, cat, _now(), kb_id),
        )
    return get_kb(kb_id)


def delete_kb(kb_id: str) -> dict:
    """删除库:物理删除该库独立向量目录 → 删元数据(含级联)→ 删归档文件。

    【创新点1】drop_kb 直接删掉 `data/vectors/<kb_id>/`,物理隔离,
    其它知识库的向量文件不受任何影响。
    """
    from ..core.config import settings

    with tx() as conn:
        cur = conn.execute("SELECT id FROM kb WHERE id=?", (kb_id,)).fetchone()
        if not cur:
            raise KbNotFound(kb_id)
        conn.execute("DELETE FROM kb WHERE id=?", (kb_id,))
    store_result = vector_store.drop_kb(kb_id)
    try:
        from .lexical_index import lexical_cache

        lexical_cache.invalidate(kb_id)      # 同步失效词法索引
    except Exception:
        pass
    # 清理归档目录
    import shutil

    kb_dir = settings.files_dir / kb_id
    if kb_dir.exists():
        shutil.rmtree(kb_dir, ignore_errors=True)
    return {"ok": True, "vector_store": store_result}


def update_profile(kb_id: str, vec: list[float] | None, delta: int = 1) -> None:
    """文档增删后更新该库的质心向量。vec 为文档级向量;delta +1 增 / -1 删。"""
    with tx() as conn:
        row = conn.execute("SELECT emb_sum, emb_cnt FROM kb WHERE id=?", (kb_id,)).fetchone()
        if not row:
            return
        old_sum = db.blob_to_vec(row["emb_sum"])
        cnt = row["emb_cnt"] + delta
        if vec is None or cnt <= 0:
            conn.execute("UPDATE kb SET emb_sum=?, emb_cnt=?, updated_at=? WHERE id=?",
                         (None, max(0, cnt), _now(), kb_id))
            return
        import numpy as np

        if not old_sum:
            new_sum = np.asarray(vec, dtype=np.float32)
        else:
            new_sum = np.asarray(old_sum, dtype=np.float32) + np.asarray(vec, dtype=np.float32)
        conn.execute("UPDATE kb SET emb_sum=?, emb_cnt=?, updated_at=? WHERE id=?",
                     (db.vec_to_blob(new_sum.tolist()), cnt, _now(), kb_id))


def profile_vector(kb_id: str) -> list[float] | None:
    """返回该库质心向量(供自动路由)。"""
    with tx() as conn:
        row = conn.execute("SELECT emb_sum, emb_cnt FROM kb WHERE id=?", (kb_id,)).fetchone()
    if not row or not row["emb_cnt"]:
        return None
    vec = db.blob_to_vec(row["emb_sum"])
    if not vec:
        return None
    import numpy as np

    arr = np.asarray(vec, dtype=np.float32)
    return (arr / row["emb_cnt"]).tolist()
