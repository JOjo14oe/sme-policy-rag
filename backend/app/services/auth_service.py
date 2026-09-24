"""【增强B】身份与知识库可见性鉴权。

中小企业场景:人事制度、薪酬制度等敏感文件不应被无权限部门检索到。
本模块提供:
- Actor:请求身份(角色 + 部门 + 名称),来源为请求头(X-Actor-Role / X-Actor-Dept /
  X-Actor-Name),缺省时取配置默认值(本机单人使用即 admin);
- 知识库可见范围:all(全员) / restricted(仅指定部门);
- 统一鉴权入口,供路由、检索、冲突检测、文档操作调用;越权访问记入审计日志。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from ..core.config import settings

ADMIN_ROLES = {"admin", "owner", "sysadmin"}


@dataclass
class Actor:
    name: str = ""
    role: str = ""
    dept: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role.lower() in ADMIN_ROLES

    def to_dict(self) -> dict:
        return {"name": self.name, "role": self.role, "dept": self.dept, "is_admin": self.is_admin}


def _hv(value: str | None) -> str:
    """HTTP 头只允许 latin-1,中文需百分号编码;这里统一解码。"""
    if not value:
        return ""
    try:
        return unquote(value).strip()
    except Exception:
        return value.strip()


def actor_from_headers(headers) -> Actor:
    """从请求头解析身份(支持百分号编码的中文);无则用默认配置。"""
    get = (headers or {}).get
    role = _hv(get("x-actor-role")) or settings.default_role or "admin"
    dept = _hv(get("x-actor-dept")) or settings.default_dept or ""
    name = _hv(get("x-actor-name")) or settings.default_actor_name or "local"
    return Actor(name=name, role=role.lower(), dept=dept)


class PermissionDenied(PermissionError):
    """无权访问指定知识库。"""


def _dept_list(value: str | None) -> list[str]:
    if not value:
        return []
    raw = str(value).replace(";", ",").replace("、", ",").replace(" ", ",")
    return [d.strip() for d in raw.split(",") if d.strip()]


def can_access_kb(kb: dict, actor: Actor) -> bool:
    """判断该身份是否可访问某知识库。admin 全通;restricted 库需部门匹配。"""
    if not settings.enforce_permissions or actor.is_admin:
        return True
    visibility = (kb.get("visibility") or "all").strip().lower()
    if visibility != "restricted":
        return True
    allowed = _dept_list(kb.get("allowed_depts"))
    if not allowed:
        return False            # restricted 但未配置部门 → 仅 admin 可见
    return bool(actor.dept) and actor.dept in allowed


def filter_kbs(kbs: list[dict], actor: Actor) -> list[dict]:
    """过滤出该身份可见的知识库列表。"""
    return [kb for kb in kbs if can_access_kb(kb, actor)]


def kb_ids_for(kbs: list[dict], actor: Actor) -> list[str]:
    return [kb["id"] for kb in filter_kbs(kbs, actor)]


def visible_doc_scope(kb: dict, actor: Actor) -> bool:
    """文档级二次校验钩子(当前与库级一致,预留按 dept_scope 细化)。"""
    return can_access_kb(kb, actor)


def require_kb(kb: dict | None, actor: Actor, *, action: str = "access", audit: bool = True) -> dict:
    """校验并返回知识库;无权则抛 PermissionDenied(并写审计)。"""
    if kb is None:
        raise KeyError("kb_not_found")
    if can_access_kb(kb, actor):
        return kb
    if audit:
        try:
            from . import audit_service

            audit_service.log(
                action="deny",
                actor=actor,
                kb_id=kb.get("id", ""),
                target=kb.get("name", ""),
                result="denied",
                detail={"reason": "kb_visibility_restricted", "attempt": action,
                        "visibility": kb.get("visibility"),
                        "allowed_depts": kb.get("allowed_depts")},
            )
        except Exception:
            pass
    raise PermissionDenied(
        f"无权访问知识库「{kb.get('name', '')}」(可见范围:{kb.get('visibility', 'all')},"
        f"允许部门:{kb.get('allowed_depts') or '未配置'};当前身份:{actor.dept or '未指定部门'})"
    )
