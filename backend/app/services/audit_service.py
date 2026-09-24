"""【增强C】审计日志。

内部制度问答系统的合规要求:谁在什么时候问了什么、命中了哪些知识库、
是否出现越权拒绝、谁改动了哪份文件 —— 全部留痕并可查询。

设计:写日志绝不影响主流程(任何异常都被吞掉)。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.config import settings
from ..core.db import tx

logger = logging.getLogger("rag-local.audit")

ACTIONS = {
    "ask": "问答",
    "doc_add": "文档新增",
    "doc_update": "文档更新",
    "doc_delete": "文档删除",
    "doc_meta": "制度属性修改",
    "kb_create": "建库",
    "kb_update": "库设置修改",
    "kb_delete": "删库",
    "conflict": "冲突告警",
    "deny": "越权拒绝",
    "meta": "系统操作",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(action: str, *, actor=None, kb_id: str = "", doc_id: str = "",
        target: str = "", result: str = "ok", detail: dict | None = None) -> None:
    """写入一条审计记录。actor 为 auth_service.Actor 或 None。"""
    try:
        role = getattr(actor, "role", "") or ""
        dept = getattr(actor, "dept", "") or ""
        name = getattr(actor, "name", "") or ""
        payload = dict(detail or {})
        if name:
            payload.setdefault("actor_name", name)
        detail_json = json.dumps(payload, ensure_ascii=False)[:4000]
        with tx() as conn:
            conn.execute(
                """INSERT INTO audit_log(created_at, action, actor_role, actor_dept, kb_id, doc_id,
                                         target, result, detail)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (_now(), action, role, dept, kb_id, doc_id, target[:300], result, detail_json),
            )
    except Exception as e:   # 审计失败不得影响业务
        logger.warning("audit log failed: %s", e)


def query(action: str | None = None, kb_id: str | None = None, dept: str | None = None,
          result: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    if action:
        sql += " AND action=?"
        args.append(action)
    if kb_id:
        sql += " AND kb_id=?"
        args.append(kb_id)
    if dept:
        sql += " AND actor_dept=?"
        args.append(dept)
    if result:
        sql += " AND result=?"
        args.append(result)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [max(1, min(1000, limit)), max(0, offset)]
    with tx() as conn:
        rows = conn.execute(sql, args).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except json.JSONDecodeError:
            d["detail"] = {}
        d["action_label"] = ACTIONS.get(d["action"], d["action"])
        items.append(d)
    return {"total": int(total), "items": items, "limit": limit, "offset": offset}


def stats() -> dict:
    with tx() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        by_action = conn.execute(
            "SELECT action, COUNT(*) AS c FROM audit_log GROUP BY action ORDER BY c DESC"
        ).fetchall()
        denied = conn.execute("SELECT COUNT(*) AS c FROM audit_log WHERE result='denied'").fetchone()["c"]
        last = conn.execute("SELECT created_at FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "total": int(total),
        "denied": int(denied),
        "by_action": [{"action": r["action"], "label": ACTIONS.get(r["action"], r["action"]),
                       "count": int(r["c"])} for r in by_action],
        "last_at": last["created_at"] if last else None,
        "enabled": True,
    }
