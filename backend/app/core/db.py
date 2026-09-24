"""SQLite 元数据库:知识库 / 文档 / chunk 的权威映射表。

设计要点:
- 每个文档唯一 doc_id,与向量库中每个 chunk 的元数据 doc_id 一一对应,
  保证"按 doc_id 精准新增/更新/删除、旧数据零残留"。
- kb 表冗余维护嵌入质心(emb_sum / emb_cnt),用于快速自动路由,
  文档增删时同步更新,避免每次路由全量重算。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    emb_sum     BLOB,          -- float32 向量和(维度 embed_dim)
    emb_cnt     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    kb_id       TEXT NOT NULL REFERENCES kb(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    ext         TEXT NOT NULL,
    file_hash   TEXT NOT NULL,
    file_path   TEXT NOT NULL,      -- 相对 files_dir
    chunk_count INTEGER NOT NULL DEFAULT 0,
    embedding   BLOB,               -- 文档级 float32 向量(所有 chunk 均值)
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_doc_kb ON documents(kb_id);
CREATE INDEX IF NOT EXISTS idx_doc_hash ON documents(file_hash);

-- 【创新点2】块级账本:记录每个 chunk 的内容哈希,增量更新时据此复用未变更块的向量
CREATE TABLE IF NOT EXISTS doc_chunks (
    doc_id      TEXT NOT NULL,
    kb_id       TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_hash  TEXT NOT NULL,
    text_len    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunk_hash ON doc_chunks(doc_id, chunk_hash);

-- 更新历史(用于统计增量收益)
CREATE TABLE IF NOT EXISTS update_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id            TEXT NOT NULL,
    kb_id             TEXT NOT NULL,
    filename          TEXT NOT NULL,
    total_chunks      INTEGER NOT NULL,
    reused_chunks     INTEGER NOT NULL,
    embedded_chunks   INTEGER NOT NULL,
    actual_ms         INTEGER NOT NULL,
    estimated_full_ms INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

-- 【增强C】审计日志:问答与文档/库变更全程留痕
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    action      TEXT NOT NULL,        -- ask / doc_add / doc_update / doc_delete / kb_create / kb_delete / meta_update / deny / conflict
    actor_role  TEXT NOT NULL DEFAULT '',   -- 角色:admin / hr / staff ...
    actor_dept  TEXT NOT NULL DEFAULT '',   -- 部门
    kb_id       TEXT NOT NULL DEFAULT '',
    doc_id      TEXT NOT NULL DEFAULT '',
    target      TEXT NOT NULL DEFAULT '',   -- 人类可读目标(库名/文件名)
    result      TEXT NOT NULL DEFAULT '',   -- ok / denied / error
    detail      TEXT NOT NULL DEFAULT ''    -- JSON 详情
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""

# 【增强A/B】增量列迁移:老库自动补齐新字段(ALTER TABLE 幂等)
MIGRATIONS: list[tuple[str, str, str]] = [
    # (表, 列, 类型与默认值)
    ("kb", "visibility", "TEXT NOT NULL DEFAULT 'all'"),      # all | restricted
    ("kb", "allowed_depts", "TEXT NOT NULL DEFAULT ''"),      # 逗号分隔部门;restricted 时生效
    ("kb", "owner_dept", "TEXT NOT NULL DEFAULT ''"),         # 归属部门
    ("kb", "doc_category", "TEXT NOT NULL DEFAULT ''"),       # 制度类别:人事/财务/行政...
    ("documents", "doc_no", "TEXT NOT NULL DEFAULT ''"),            # 文件编号
    ("documents", "effective_date", "TEXT NOT NULL DEFAULT ''"),    # 生效日期 YYYY-MM-DD
    ("documents", "expiry_date", "TEXT NOT NULL DEFAULT ''"),       # 失效日期
    ("documents", "doc_status", "TEXT NOT NULL DEFAULT 'effective'"),  # effective | superseded | draft | expired
    ("documents", "issuer", "TEXT NOT NULL DEFAULT ''"),            # 发布部门
    ("documents", "dept_scope", "TEXT NOT NULL DEFAULT ''"),        # 适用部门(逗号分隔,空=全员)
    ("documents", "supersedes", "TEXT NOT NULL DEFAULT ''"),        # 替代的文件编号
    ("documents", "superseded_by", "TEXT NOT NULL DEFAULT ''"),     # 被哪个文件编号替代
    ("documents", "title", "TEXT NOT NULL DEFAULT ''"),             # 制度名称(可不同于文件名)
]

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path: Path = settings.meta_db_path  # type: ignore[assignment]
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _run_migrations(_conn)
        _conn.commit()
    return _conn


def _run_migrations(conn: sqlite3.Connection) -> list[str]:
    """为老数据库补齐新增列(幂等:已存在则跳过)。"""
    applied: list[str] = []
    for table, column, decl in MIGRATIONS:
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not cols:
                continue          # 表尚未创建
            if column in cols:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
        except sqlite3.Error:
            continue
    return applied


@contextmanager
def tx():
    """事务上下文,自动 commit/rollback。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def blob_to_vec(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    import struct

    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def vec_to_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)
