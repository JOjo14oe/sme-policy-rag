"""【创新点1】向量存储布局迁移:单实例多 collection → 每知识库独立实例。

旧布局: data/chroma/ 单个 Chroma 实例,集合名 `kb_<kb_id>`(逻辑隔离)
新布局: data/vectors/<kb_id>/ 每个知识库一个独立 Chroma 实例(物理隔离)

迁移策略:逐库读取(含向量)→ 写入独立实例 → **数量校验** → 全部一致后
把旧目录重命名为 `data/chroma_legacy_backup`(保留回滚能力,不删除数据),
并写入标记文件,保证幂等(重复启动不会重复迁移)。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from ..core.config import settings
from .vector_store import vector_store

_BATCH = 500
_LEGACY_PREFIX = "kb_"


def _marker():
    return settings.vectors_dir / ".migrated_from_legacy"


def layout_status() -> dict:
    """返回当前布局状态(供诊断与报告使用)。"""
    legacy = settings.chroma_dir
    return {
        "vectors_dir": str(settings.vectors_dir),
        "legacy_dir": str(legacy),
        "legacy_exists": bool(legacy and legacy.exists()),
        "migrated": _marker().exists(),
        "marker": str(_marker()),
        "kb_stores": vector_store.all_storage_info(),
    }


def migrate_legacy_layout() -> dict:
    """执行一次性迁移;幂等、可重复调用。"""
    report: dict = {
        "needed": False,
        "migrated": [],
        "errors": [],
        "verified": True,
        "skipped_reason": None,
    }
    legacy = settings.chroma_dir
    if _marker().exists():
        report["skipped_reason"] = "already_migrated"
        return report
    if not legacy or not legacy.exists():
        _marker().write_text(json.dumps({"note": "no legacy layout", "at": _now()}), encoding="utf-8")
        report["skipped_reason"] = "no_legacy_dir"
        return report

    import chromadb
    from chromadb.config import Settings as ChromaSettings

    try:
        client = chromadb.PersistentClient(
            path=str(legacy),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        collections = list(client.list_collections())
    except Exception as e:
        report["errors"].append({"stage": "open_legacy", "error": str(e)})
        report["verified"] = False
        return report

    names: list[str] = []
    for c in collections:
        name = getattr(c, "name", None) or str(c)
        names.append(name)
    kb_names = [n for n in names if n.startswith(_LEGACY_PREFIX)]
    if not kb_names:
        _marker().write_text(json.dumps({"note": "legacy has no kb_* collections", "at": _now()}), encoding="utf-8")
        report["skipped_reason"] = "no_legacy_kb_collections"
        return report

    report["needed"] = True
    for name in kb_names:
        kb_id = name[len(_LEGACY_PREFIX):]
        try:
            src = client.get_collection(name)
            source_total = src.count()
            written = 0
            offset = 0
            while source_total and written < source_total:
                batch = src.get(
                    include=["documents", "metadatas", "embeddings"],
                    limit=_BATCH,
                    offset=offset,
                )
                ids = batch.get("ids") or []
                if not ids:
                    break
                docs = batch.get("documents") or []
                metas = batch.get("metadatas") or []
                embs = batch.get("embeddings")
                if embs is None:
                    raise RuntimeError("旧库未返回向量数据,无法迁移")
                target = vector_store.get_collection(kb_id, create=True)
                target.add(
                    ids=list(ids),
                    documents=[d if d is not None else "" for d in docs],
                    metadatas=[dict(m or {}) for m in metas],
                    embeddings=[list(e) for e in embs],
                )
                written += len(ids)
                if len(ids) < _BATCH:
                    break
                offset += _BATCH
            target_total = vector_store.count(kb_id)
            ok = (target_total == source_total)
            report["migrated"].append({
                "kb_id": kb_id,
                "source_total": source_total,
                "target_total": target_total,
                "ok": ok,
                "store": vector_store.storage_info(kb_id),
            })
            if not ok:
                report["verified"] = False
        except Exception as e:
            report["errors"].append({"kb_id": kb_id, "error": str(e)})
            report["verified"] = False

    if report["verified"] and not report["errors"]:
        backup = legacy.parent / "chroma_legacy_backup"
        try:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            legacy.rename(backup)
            report["legacy_backup"] = str(backup)
        except Exception as e:
            report["legacy_backup_error"] = str(e)
        _marker().write_text(
            json.dumps({"migrated_at": _now(), "report": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def cleanup_legacy_after_migration() -> dict:
    """迁移已完成时,清理遗留的旧单实例目录。

    首次迁移时旧目录常因仍有 sqlite 句柄而无法重命名/删除,这里在
    后续启动(句柄已释放)时补做:优先重命名为 backup,失败则直接删除。
    """
    legacy = settings.chroma_dir
    result = {"legacy": str(legacy), "action": "none"}
    if not _marker().exists() or not legacy or not legacy.exists():
        return result
    backup = legacy.parent / "chroma_legacy_backup"
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        legacy.rename(backup)
        result["action"] = "renamed_backup"
        result["backup"] = str(backup)
        return result
    except Exception as e:
        result["rename_error"] = str(e)
    try:
        shutil.rmtree(legacy)
        result["action"] = "deleted"
    except Exception as e:
        result["delete_error"] = str(e)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
