"""FastAPI 应用入口:装配路由 + 托管内嵌 Web 界面。

启动: uvicorn app.main:app --host 127.0.0.1 --port 8000
(或在 backend 目录运行: python -m uvicorn app.main:app)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.db import get_conn
from .routers import audit, classify, doc, eval as eval_api, kb, qa, system

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("rag-local")

app = FastAPI(title="多知识库私有 RAG 问答系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(kb.router)
app.include_router(doc.router)
app.include_router(qa.router)
app.include_router(classify.router)
app.include_router(audit.router)
app.include_router(eval_api.router)
app.include_router(system.router)


@app.on_event("startup")
def _startup():
    get_conn()  # 初始化 SQLite 表
    logger.info("data_root=%s", settings.data_root)
    logger.info("chat_model=%s embed_model=%s", settings.chat_model, settings.embed_model)

    # 【创新点1】向量硬隔离:清理历史 .trash 残留 + 旧布局一次性迁移
    from .services import migration
    from .services.vector_store import vector_store

    try:
        trashed = vector_store.cleanup_trash()
        if trashed:
            logger.info("cleaned %d trash vector dir(s): %s", len(trashed), trashed)
        rep = migration.migrate_legacy_layout()
        if rep.get("needed"):
            logger.info("legacy vector layout migrated: %s", rep.get("migrated"))
        elif rep.get("skipped_reason"):
            logger.info("vector layout migration skipped: %s", rep["skipped_reason"])
        if rep.get("errors"):
            logger.warning("vector migration errors: %s", rep["errors"])
        cleanup = migration.cleanup_legacy_after_migration()
        if cleanup.get("action") != "none":
            logger.info("legacy chroma dir cleanup: %s", cleanup)
    except Exception as e:  # 迁移失败不阻断启动
        logger.exception("vector layout migration failed: %s", e)


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse(settings.web_dir / "index.html")


# 托管静态前端(置于 API 之后注册,避免覆盖 /api 前缀)
app.mount("/", StaticFiles(directory=str(settings.web_dir), html=True), name="web")
