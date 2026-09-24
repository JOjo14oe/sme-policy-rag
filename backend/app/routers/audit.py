"""【增强C】审计日志路由:查询与统计。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..services import audit_service, auth_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def query(request: Request, action: str | None = None, kb_id: str | None = None,
          dept: str | None = None, result: str | None = None,
          limit: int = 100, offset: int = 0):
    """查询审计记录(按身份可见范围约束:非管理员仅见本部门记录)。"""
    actor = auth_service.actor_from_headers(request.headers)
    scope_dept = None if actor.is_admin else (actor.dept or "__none__")
    try:
        data = audit_service.query(action=action, kb_id=kb_id,
                                   dept=dept or scope_dept, result=result,
                                   limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(500, f"查询审计日志失败: {e}")
    data["actor"] = actor.to_dict()
    return data


@router.get("/stats")
def stats(request: Request):
    actor = auth_service.actor_from_headers(request.headers)
    try:
        data = audit_service.stats()
    except Exception as e:
        raise HTTPException(500, f"统计审计日志失败: {e}")
    data["actor"] = actor.to_dict()
    return data
