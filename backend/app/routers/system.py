"""系统路由:健康检查、配置查看、模型状态。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import db
from ..core.config import settings
from ..core.ollama_client import ollama

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True, "service": "rag-local"}


@router.get("/status")
def status():
    """诊断面板数据:Ollama 可达性、模型、配置、统计。"""
    from ..services import kb_service

    models = ollama.list_models()
    chat_ok = settings.chat_model in models or any(
        m.split(":")[0] == settings.chat_model.split(":")[0] for m in models
    )
    embed_ok = settings.embed_model in models or any(
        m.split(":")[0] == settings.embed_model.split(":")[0] for m in models
    )
    kbs = kb_service.list_kbs()
    total_docs = sum(k["doc_count"] for k in kbs)
    total_chunks = sum(k["chunk_count"] for k in kbs)
    from ..services import audit_service, doc_service
    from ..services.lexical_index import lexical_cache

    restricted = [k["name"] for k in kbs if (k.get("visibility") or "all") == "restricted"]
    expiring = doc_service.expiring(None, settings.expiring_soon_days)
    return {
        "ok": True,
        "ollama_connected": bool(models),
        "ollama_models": models,
        "chat_model": settings.chat_model,
        "embed_model": settings.embed_model,
        "chat_ok": chat_ok,
        "embed_ok": embed_ok,
        "kbs": len(kbs),
        "docs": total_docs,
        "chunks": total_chunks,
        "data_dir": str(settings.data_root),
        "updates": doc_service.update_stats(),
        "audit": audit_service.stats(),
        "retrieval": {
            "mode": "hybrid" if (settings.enable_vector_channel and settings.enable_lexical_channel) else "vector",
            "vector_channel": settings.enable_vector_channel,
            "lexical_channel": settings.enable_lexical_channel,
            "rrf_k": settings.rrf_k,
            "weights": {"vector": settings.rrf_vector_weight, "lexical": settings.rrf_lexical_weight},
            "meta_boost": {
                "doc_no": settings.meta_boost_docno,
                "dept": settings.meta_boost_dept,
                "recency": settings.meta_boost_recency,
                "heading": settings.meta_boost_heading,
            },
            "lexical_index_cache": lexical_cache.stats(),
        },
        "policy": {
            "exclude_expired_docs": settings.exclude_expired_docs,
            "expiring_soon_days": settings.expiring_soon_days,
            "expiring_count": len(expiring),
            "expiring_items": expiring[:10],
            "restricted_kbs": restricted,
        },
        "config": {
            "chunk_max_chars": settings.chunk_max_chars,
            "top_k": settings.top_k,
            "score_threshold": settings.score_threshold,
            "route_min_sim": settings.route_min_sim,
            "temperature": settings.temperature,
            "enable_conflict_check": settings.enable_conflict_check,
            "conflict_multi_kb_n": settings.conflict_multi_kb_n,
            "conflict_pairs_limit": settings.conflict_pairs_limit,
            "enforce_permissions": settings.enforce_permissions,
            "default_role": settings.default_role,
            "default_dept": settings.default_dept,
        },
    }


@router.get("/isolation")
def isolation():
    """【创新点1】向量硬隔离证据:每个知识库独立的存储目录、文件与占用。"""
    from ..services import kb_service, migration
    from ..services.vector_store import vector_store

    kbs = kb_service.list_kbs()
    stores = vector_store.all_storage_info()
    info = []
    for s in stores:
        kb = next((k for k in kbs if k["id"] == s["kb_id"]), None)
        info.append({
            **s,
            "kb_name": (kb or {}).get("name", "(已删除)"),
            "chunk_count": vector_store.count(s["kb_id"]),
        })
    return {
        "layout": "per-kb-chroma-instance",
        "vectors_dir": str(settings.vectors_dir),
        "kb_stores": info,
        "store_count": len(stores),
        "migration": migration.layout_status(),
        "distinct_paths": len({s["path"] for s in info}),
    }


@router.get("/update-stats")
def update_stats(kb_id: str | None = None):
    """【创新点2】增量更新累计收益(复用率 / 耗时对比)。"""
    from ..services import doc_service

    return doc_service.update_stats(kb_id)


@router.post("/db-close")
def db_close():
    db.close()
    return {"ok": True}
