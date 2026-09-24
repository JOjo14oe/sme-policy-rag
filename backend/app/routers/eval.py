"""【改进】评测接口:检索消融与答案级评测。

为"可度量"提供后端支撑(对标开源方案普遍缺失的评测能力):
- POST /api/eval/retrieval  给定问题与知识库,返回指定检索模式的排序结果(含来源定位),
  供评测脚本计算 hit@k / MRR,并可对比 vector / lexical / hybrid / hybrid_rerank 四种模式;
- POST /api/eval/answer     运行一次完整问答(非流式),返回答案、引用、命中片段与耗时,
  供计算引用命中率与拒答正确率;
- GET  /api/eval/modes      列出可用检索模式与当前配置。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..core.config import settings
from ..core.ollama_client import OllamaError, ollama
from ..services import auth_service, conflict_service, kb_service, qa_service

router = APIRouter(prefix="/api/eval", tags=["evaluation"])

MODES = ("vector", "lexical", "hybrid", "hybrid_rerank")


@router.get("/modes")
def modes():
    return {
        "modes": list(MODES),
        "default": "hybrid_rerank" if settings.enable_lexical_channel else "vector",
        "description": {
            "vector": "仅向量通道(稠密语义检索)",
            "lexical": "仅词法通道(BM25:中文二元组 + ASCII 词)",
            "hybrid": "双通道 RRF 融合(不做元数据加权)",
            "hybrid_rerank": "双通道融合 + 确定性元数据重排(生产默认)",
        },
    }


@router.post("/retrieval")
def retrieval(body: dict, request: Request):
    """返回指定模式下的检索排序结果(用于 hit@k / MRR 计算)。"""
    question = (body.get("question") or "").strip()
    kb_id = body.get("kb_id")
    mode = (body.get("mode") or "hybrid_rerank").lower()
    top_k = int(body.get("top_k") or settings.top_k)
    if not question:
        raise HTTPException(400, "问题不能为空")
    if not kb_id:
        raise HTTPException(400, "kb_id 不能为空")
    if mode not in MODES:
        raise HTTPException(400, f"未知检索模式:{mode}(可选 {'/'.join(MODES)})")
    if not ollama.ping():
        raise HTTPException(503, "Ollama 未运行或无响应。")

    actor = auth_service.actor_from_headers(request.headers)
    try:
        kb = kb_service.get_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    try:
        auth_service.require_kb(kb, actor, action="eval_retrieval")
    except auth_service.PermissionDenied as e:
        raise HTTPException(403, str(e))

    try:
        hits, _qvec = qa_service.search_kb(kb_id, question, top_k=top_k,
                                           actor=actor, kb_meta=kb, mode=mode)
    except OllamaError as e:
        raise HTTPException(503, str(e))

    return {
        "mode": mode,
        "kb_id": kb_id,
        "kb_name": kb.get("name"),
        "question": question,
        "results": [
            {
                "rank": i + 1,
                "doc_id": h.doc_id,
                "filename": h.filename,
                "heading_path": h.heading_path,
                "page": h.page,
                "score": h.score,
                "gate_score": h.gate_score,
                "channels": h.channels,
                "retrieval": h.retrieval,
                "vector_score": h.vector_score,
                "lexical_score": h.lexical_score,
                "meta_boost": h.meta_boost,
                "preview": h.text[:160],
            }
            for i, h in enumerate(hits)
        ],
    }


@router.post("/answer")
def answer(body: dict, request: Request):
    """运行一次完整问答(非流式),返回答案 / 引用 / 命中片段 / 耗时。"""
    question = (body.get("question") or "").strip()
    kb_id = body.get("kb_id")
    if not question or not kb_id:
        raise HTTPException(400, "question 与 kb_id 均不能为空")
    if not ollama.ping():
        raise HTTPException(503, "Ollama 未运行或无响应。")

    actor = auth_service.actor_from_headers(request.headers)
    try:
        kb = kb_service.get_kb(kb_id)
    except kb_service.KbNotFound:
        raise HTTPException(404, "知识库不存在")
    try:
        auth_service.require_kb(kb, actor, action="eval_answer")
    except auth_service.PermissionDenied as e:
        raise HTTPException(403, str(e))

    import time
    t0 = time.perf_counter()
    answer_text, citations, no_context, conflict = "", {}, None, None
    try:
        for ev in qa_service.answer_stream(question, kb_id, kb.get("name"), actor=actor):
            t = ev.get("type")
            if t == "delta":
                answer_text += ev.get("text", "")
            elif t == "done":
                answer_text = ev.get("answer") or answer_text
                citations = ev.get("citations") or {}
            elif t == "no_context":
                no_context = ev.get("reason")
            elif t == "conflict":
                conflict = {"version": ev.get("version_conflicts"), "genuine": ev.get("genuine_conflicts")}
            elif t == "error":
                raise HTTPException(503, str(ev.get("message")))
    except OllamaError as e:
        raise HTTPException(503, str(e))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "kb_id": kb_id,
        "kb_name": kb.get("name"),
        "question": question,
        "answer": answer_text,
        "refused": no_context is not None,
        "refusal_reason": no_context,
        "citations": citations,
        "citation_count": len(citations),
        "conflict": conflict,
        "elapsed_ms": elapsed_ms,
    }


@router.post("/conflict")
def conflict(body: dict, request: Request):
    """冲突检测的评测入口(返回结构化冲突清单与分类)。"""
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    actor = auth_service.actor_from_headers(request.headers)
    try:
        return conflict_service.detect(question, kb_ids=body.get("kb_ids"), actor=actor)
    except OllamaError as e:
        raise HTTPException(503, str(e))
