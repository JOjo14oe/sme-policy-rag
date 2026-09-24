"""自动分类路由:批量上传混杂文档 → 聚类 → 建议命名 → 用户确认建库。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..core.ollama_client import OllamaError
from ..services import classify_service

router = APIRouter(prefix="/api/classify", tags=["auto-classify"])


@router.post("/prepare")
def prepare(files: list[UploadFile] = File(...)):
    """上传混杂文档,返回 session_id 与候选簇(尚未建库)。"""
    if not files:
        raise HTTPException(400, "请至少上传一个文件")
    try:
        data = [(f.filename or "unnamed", f.file.read()) for f in files]
    except Exception as e:
        raise HTTPException(400, f"读取文件失败: {e}")
    try:
        return classify_service.prepare_session(data)
    except OllamaError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"自动分类失败: {e}")


@router.post("/suggest")
def suggest(body: dict):
    """为会话内各簇生成建议库名。"""
    sid = body.get("session_id") or ""
    try:
        return {"suggestions": classify_service.suggest_names(sid)}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"命名建议失败: {e}")


@router.post("/confirm")
def confirm(body: dict):
    """用户确认建库。body: {session_id, clusters:[{cluster_id, confirmed, name, description}]}"""
    sid = body.get("session_id") or ""
    clusters = body.get("clusters") or []
    try:
        return classify_service.confirm_build(sid, clusters)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except OllamaError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"建库失败: {e}")
