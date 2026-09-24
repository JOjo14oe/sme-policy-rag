"""知识库路由:建库、列表、改名、删除、对账。

【增强B】列表按身份过滤可见库;建库/改设置可配置可见范围(all/restricted + 允许部门);
【增强C】建库、改设置、删库写入审计日志。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..services import audit_service, auth_service, doc_service, kb_service

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


@router.get("")
def list_kbs(request: Request, all: bool = False):
    """列出知识库。默认只返回当前身份可见的库;all=true 时返回全部(用于管理页)。"""
    actor = auth_service.actor_from_headers(request.headers)
    kbs = kb_service.list_kbs()
    if all and actor.is_admin:
        return {"kbs": kbs, "actor": actor.to_dict(), "filtered": False}
    visible = auth_service.filter_kbs(kbs, actor)
    return {"kbs": visible, "actor": actor.to_dict(), "filtered": len(visible) != len(kbs),
            "hidden_count": len(kbs) - len(visible)}


@router.post("")
def create_kb(body: dict, request: Request):
    name = (body.get("name") or "").strip()
    desc = (body.get("description") or "").strip()
    actor = auth_service.actor_from_headers(request.headers)
    if not actor.is_admin and settings_restrict_creation():
        raise HTTPException(403, "仅管理员可创建知识库")
    try:
        kb = kb_service.create_kb(
            name, desc,
            visibility=body.get("visibility") or "all",
            allowed_depts=body.get("allowed_depts") or "",
            owner_dept=body.get("owner_dept") or "",
            doc_category=body.get("doc_category") or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"创建知识库失败: {e}")
    audit_service.log(action="kb_create", actor=actor, kb_id=kb["id"], target=kb["name"],
                      detail={"visibility": kb.get("visibility"),
                              "allowed_depts": kb.get("allowed_depts"),
                              "owner_dept": kb.get("owner_dept"),
                              "doc_category": kb.get("doc_category")})
    return {"kb": kb}


def settings_restrict_creation() -> bool:
    return False


@router.patch("/{kb_id}")
def update_kb(kb_id: str, body: dict, request: Request):
    actor = auth_service.actor_from_headers(request.headers)
    try:
        kb_before = kb_service.get_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    try:
        auth_service.require_kb(kb_before, actor, action="kb_update")
    except auth_service.PermissionDenied as e:
        raise HTTPException(403, str(e))
    if not actor.is_admin and ("visibility" in body or "allowed_depts" in body):
        raise HTTPException(403, "仅管理员可修改知识库可见范围")
    try:
        kb = kb_service.rename_kb(kb_id, body.get("name"), body.get("description"),
                                  visibility=body.get("visibility"),
                                  allowed_depts=body.get("allowed_depts"),
                                  owner_dept=body.get("owner_dept"),
                                  doc_category=body.get("doc_category"))
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"更新知识库失败: {e}")
    audit_service.log(action="kb_update", actor=actor, kb_id=kb_id, target=kb["name"],
                      detail={"before": {"visibility": kb_before.get("visibility"),
                                         "allowed_depts": kb_before.get("allowed_depts")},
                              "after": {"visibility": kb.get("visibility"),
                                        "allowed_depts": kb.get("allowed_depts")}})
    return {"kb": kb}


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, request: Request):
    """删除知识库:物理删除其独立向量存储目录(data/vectors/<kb_id>/)。"""
    actor = auth_service.actor_from_headers(request.headers)
    try:
        kb = kb_service.get_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    try:
        auth_service.require_kb(kb, actor, action="kb_delete")
    except auth_service.PermissionDenied as e:
        raise HTTPException(403, str(e))
    try:
        result = kb_service.delete_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    except Exception as e:
        raise HTTPException(500, f"删除知识库失败: {e}")
    audit_service.log(action="kb_delete", actor=actor, kb_id=kb_id, target=kb["name"],
                      detail=result)
    return result


@router.post("/reconcile")
def reconcile_all(request: Request):
    try:
        return {"report": doc_service.reconcile(None)}
    except Exception as e:
        raise HTTPException(500, f"对账失败: {e}")


@router.post("/{kb_id}/reconcile")
def reconcile(kb_id: str, request: Request):
    try:
        return {"report": doc_service.reconcile(kb_id)}
    except Exception as e:
        raise HTTPException(500, f"对账失败: {e}")
