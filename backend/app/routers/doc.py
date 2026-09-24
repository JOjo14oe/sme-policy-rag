"""文档路由:上传新增、更新、删除、列表、预览解析、原文下载、制度属性管理。

【增强A】制度元数据(文件编号/生效期/状态/替代关系)与版本链、到期提醒;
【增强B】文档操作按知识库可见性鉴权;
【增强C】增删改与越权拒绝写入审计日志。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..services import audit_service, auth_service, doc_service, kb_service
from ..services.vector_store import vector_store

router = APIRouter(prefix="/api/doc", tags=["documents"])


def _read_file(f: UploadFile) -> tuple[str, bytes]:
    return f.filename or "unnamed", f.file.read()


def _guard(kb_id: str, request: Request, action: str):
    """校验知识库存在且当前身份可访问。"""
    try:
        kb = kb_service.get_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    actor = auth_service.actor_from_headers(request.headers)
    try:
        auth_service.require_kb(kb, actor, action=action)
    except auth_service.PermissionDenied as e:
        raise HTTPException(403, str(e))
    return kb, actor


@router.get("/list/{kb_id}")
def list_docs(kb_id: str, request: Request):
    kb, _actor = _guard(kb_id, request, "doc_list")
    return {"docs": doc_service.list_documents(kb_id), "kb": {"id": kb["id"], "name": kb["name"]}}


@router.post("/add/{kb_id}")
def add_doc(kb_id: str, request: Request, file: UploadFile = File(...),
            doc_no: str = Form(""), title: str = Form(""), effective_date: str = Form(""),
            expiry_date: str = Form(""), doc_status: str = Form(""), issuer: str = Form(""),
            dept_scope: str = Form(""), supersedes: str = Form("")):
    """新增文档(可同时以表单字段填写制度属性)。"""
    kb, actor = _guard(kb_id, request, "doc_add")
    filename, data = _read_file(file)
    meta = {"doc_no": doc_no, "title": title, "effective_date": effective_date,
            "expiry_date": expiry_date, "doc_status": doc_status, "issuer": issuer,
            "dept_scope": dept_scope, "supersedes": supersedes}
    try:
        doc = doc_service.add_document(kb_id, filename, data, meta=meta)
    except doc_service.DocError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"新增文档失败: {e}")
    audit_service.log(action="doc_add", actor=actor, kb_id=kb_id, doc_id=doc["doc_id"],
                      target=doc["filename"],
                      detail={"chunks": doc.get("chunk_count"), "doc_no": doc.get("doc_no"),
                              "doc_status": doc.get("doc_status"),
                              "supersede_links": doc.get("supersede_links")})
    return {"doc": doc}


@router.put("/update/{kb_id}/{doc_id}")
def update_doc(kb_id: str, doc_id: str, request: Request, file: UploadFile = File(...)):
    _kb, actor = _guard(kb_id, request, "doc_update")
    filename, data = _read_file(file)
    try:
        doc = doc_service.update_document(kb_id, doc_id, filename, data)
    except doc_service.DocError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"更新文档失败: {e}")
    audit_service.log(action="doc_update", actor=actor, kb_id=kb_id, doc_id=doc_id,
                      target=doc["filename"],
                      detail={"version": doc.get("version"), "update_stats": doc.get("update_stats")})
    return {"doc": doc}


@router.patch("/meta/{kb_id}/{doc_id}")
def update_doc_meta(kb_id: str, doc_id: str, request: Request, body: dict = Body(...)):
    """【增强A】更新制度属性:文件编号、名称、生效/失效日期、状态、发布部门、适用范围、替代关系。"""
    _kb, actor = _guard(kb_id, request, "doc_meta")
    try:
        doc = doc_service.update_meta(kb_id, doc_id, body)
    except doc_service.DocError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"更新制度属性失败: {e}")
    audit_service.log(action="doc_meta", actor=actor, kb_id=kb_id, doc_id=doc_id,
                      target=doc.get("filename", ""),
                      detail={"changed": {k: body.get(k) for k in body},
                              "supersede_links": doc.get("supersede_links")})
    return {"doc": doc}


@router.get("/versions/{kb_id}")
def versions(kb_id: str, request: Request, doc_id: str | None = None, doc_no: str | None = None):
    """【增强A】制度版本链(按生效日期从旧到新)。"""
    _guard(kb_id, request, "doc_versions")
    try:
        chain = doc_service.version_chain(kb_id, doc_no=doc_no, doc_id=doc_id)
    except doc_service.DocError as e:
        raise HTTPException(404, str(e))
    return {"chain": chain, "count": len(chain)}


@router.get("/expiring")
def expiring(request: Request, kb_id: str | None = None, days: int | None = None):
    """【增强A】即将到期的现行制度提醒。"""
    actor = auth_service.actor_from_headers(request.headers)
    items = doc_service.expiring(kb_id, days)
    return {"items": items, "days": days, "actor": actor.to_dict()}


@router.delete("/{kb_id}/{doc_id}")
def delete_doc(kb_id: str, doc_id: str, request: Request):
    _kb, actor = _guard(kb_id, request, "doc_delete")
    try:
        res = doc_service.delete_document(kb_id, doc_id)
    except doc_service.DocError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"删除文档失败: {e}")
    audit_service.log(action="doc_delete", actor=actor, kb_id=kb_id, doc_id=doc_id, detail=res)
    return res


@router.get("/file/{kb_id}/{doc_id}")
def download_doc(kb_id: str, doc_id: str, request: Request):
    _guard(kb_id, request, "doc_download")
    try:
        p: Path = doc_service.get_archive_path(kb_id, doc_id)
    except doc_service.DocError as e:
        raise HTTPException(404, str(e))
    if not p.exists():
        raise HTTPException(404, "原文文件不存在")
    return FileResponse(str(p), filename=p.name)


@router.post("/preview")
def preview(file: UploadFile = File(...)):
    """解析预览(不落库):返回切分块与文本统计,便于用户确认切分质量。"""
    filename, data = _read_file(file)
    try:
        chunks = doc_service.parse_document(filename, data)
        return {
            "filename": filename,
            "chunk_count": len(chunks),
            "chunks": chunks[:20],
            "total_chars": sum(len(c["text"]) for c in chunks),
        }
    except doc_service.DocError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"预览失败: {e}")


@router.get("/count/{kb_id}")
def count_vec(kb_id: str):
    """向量数量(调试/对账展示用)。"""
    return {"kb_id": kb_id, "chunk_count": vector_store.count(kb_id)}
