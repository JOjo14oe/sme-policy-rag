"""问答路由:自动路由、手动检索、流式问答、路由预演、冲突检测。

【增强B】所有入口按请求身份(X-Actor-Role / X-Actor-Dept)做知识库可见性鉴权;
【增强C】问答、冲突告警、越权拒绝均写入审计日志。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..services import audit_service, auth_service, kb_service, qa_service
from ..core.ollama_client import OllamaError, ollama

router = APIRouter(prefix="/api/qa", tags=["question-answering"])


@router.post("/route")
def route(body: dict, request: Request):
    """仅路由:返回最匹配知识库及打分(前端可展示候选)。"""
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if not ollama.ping():
        raise HTTPException(503, "Ollama 未运行或无响应,请确认后重试。")
    actor = auth_service.actor_from_headers(request.headers)
    try:
        r = qa_service.route_question(question, actor=actor)
    except OllamaError as e:
        raise HTTPException(503, str(e))
    return {
        "auto_kb_id": r.kb_id,
        "auto_kb_name": r.kb_name,
        "candidates": r.scores[:5],
        "actor": actor.to_dict(),
    }


def _sse(event: str, data: dict):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
def ask(body: dict, request: Request):
    """流式问答(SSE)。

    请求: {question, kb_id?: 手动指定(兜底), mode?: 'auto'|'manual'}
    身份: 请求头 X-Actor-Role / X-Actor-Dept / X-Actor-Name
    """
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    mode = body.get("mode") or "auto"
    manual_kb = body.get("kb_id")
    actor = auth_service.actor_from_headers(request.headers)

    def gen():
        # 0) 前置探活:Ollama 不可达时不挂起,立即给友好错误
        if not ollama.ping():
            yield _sse("error", {"message": "Ollama 未运行或无响应。请运行本目录 start.bat 自动拉起后重试。"})
            return
        # 1) 决定目标库:手动(兜底)或自动路由
        kb_id, kb_name = None, None
        candidates: list[dict] = []
        if mode == "manual" and manual_kb:
            try:
                kb = kb_service.get_kb(manual_kb)
            except kb_service.KbNotFound:
                yield _sse("error", {"message": "指定的知识库不存在"})
                return
            # 【增强B】可见性鉴权
            try:
                auth_service.require_kb(kb, actor, action="ask_manual")
            except auth_service.PermissionDenied as e:
                yield _sse("denied", {"message": str(e), "kb_id": manual_kb, "kb_name": kb.get("name")})
                return
            kb_id, kb_name = kb["id"], kb["name"]
            try:
                r = qa_service.route_question(question, actor=actor)
                candidates = r.scores[:5]
            except OllamaError:
                candidates = []
        else:
            try:
                r = qa_service.route_question(question, actor=actor)
            except OllamaError as e:
                yield _sse("error", {"message": str(e)})
                return
            kb_id, kb_name = r.kb_id, r.kb_name
            candidates = r.scores[:5]
            yield _sse("route_info", {
                "auto_kb_id": kb_id, "auto_kb_name": kb_name,
                "candidates": r.scores[:5], "mode": "auto", "actor": actor.to_dict(),
            })
        # 2) 检索 + (冲突检测) + 生成
        conflict_seen = 0
        try:
            for ev in qa_service.answer_stream(question, kb_id, kb_name,
                                               candidate_kbs=candidates, actor=actor):
                if ev["type"] == "conflict":
                    conflict_seen = len(ev.get("conflicts") or [])
                    audit_service.log(
                        action="conflict", actor=actor, kb_id=kb_id or "", target=kb_name or "",
                        detail={"question": question[:300], "conflicts": conflict_seen,
                                "version_conflicts": ev.get("version_conflicts"),
                                "genuine_conflicts": ev.get("genuine_conflicts"),
                                "kinds": [c.get("kind") for c in (ev.get("conflicts") or [])]},
                    )
                yield _sse(ev["type"], ev)
        except OllamaError as e:
            yield _sse("error", {"message": str(e)})
        finally:
            audit_service.log(
                action="ask", actor=actor, kb_id=kb_id or "", target=kb_name or "",
                result="ok" if kb_id else "no_route",
                detail={"question": question[:300], "mode": mode,
                        "auto_routed": mode != "manual", "conflicts": conflict_seen},
            )

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/conflict-check")
def conflict_check(body: dict, request: Request):
    """【创新点3 + 增强A】独立冲突检测:返回分组、判定与冲突清单(含版本差异分类)。"""
    from ..services import conflict_service

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if not ollama.ping():
        raise HTTPException(503, "Ollama 未运行或无响应。")
    actor = auth_service.actor_from_headers(request.headers)
    kb_ids = body.get("kb_ids") or None
    if kb_ids:
        for kid in list(kb_ids):
            try:
                kb = kb_service.get_kb(kid)
            except kb_service.KbNotFound:
                continue
            try:
                auth_service.require_kb(kb, actor, action="conflict_check")
            except auth_service.PermissionDenied as e:
                raise HTTPException(403, str(e))
    try:
        return conflict_service.detect(question, kb_ids=kb_ids, actor=actor)
    except OllamaError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"冲突检测失败: {e}")


@router.get("/models")
def models():
    """列出当前 Ollama 可用模型(诊断)。"""
    from ..core.config import settings as cfg

    return {
        "models": ollama.list_models(),
        "chat_model": cfg.chat_model,
        "embed_model": cfg.embed_model,
    }
