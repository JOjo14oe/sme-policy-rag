"""混合检索与确定性重排 —— 向量通道 + BM25 词法通道 + 元数据加权。

为什么这样设计(对标主流开源 RAG 的检索实践)
--------------------------------------------
主流方案(RAGFlow / AnythingLLM / MaxKB / FastGPT 等)普遍具备"向量 + 关键字"的
混合检索与重排能力;纯向量检索在制度文档场景会漏掉**精确串扰**:
编号(HR-2025-001)、数值(15 天 / 500 元)、专有名词等。

本模块实现(全部本地、零额外模型):
1. **RRF 融合**(Reciprocal Rank Fusion):对向量名次与词法名次做
   score = w_v/(K+rank_v) + w_l/(K+rank_l),无需调参即可稳定融合两个通道;
2. **确定性重排**:在融合分上叠加可解释的元数据权重 ——
   · 制度编号命中(查询里出现某文件编号 → 该编号文档加权)
   · 部门匹配(请求身份部门与文档适用部门/发布部门一致 → 加权)
   · 版本时效(生效日期更近的现行版本 → 轻微加权)
   · 条款位置(命中标题/条款路径 → 轻微加权)
3. 输出保留**通道来源**(vector / lexical)与各分项权重,便于评测与解释。

注意:所有加权都发生在**已通过权限与版本过滤**的候选集内(见 qa_service),权重只影响排序。
"""
from __future__ import annotations

from ..core.config import settings
from . import lexical_index
from .vector_store import vector_store


def _rrf(rank: int, k: int) -> float:
    return 1.0 / (k + max(1, rank))


def _norm_meta(meta: dict | None) -> dict:
    return meta or {}


def hybrid_search(
    kb_id: str,
    question: str,
    qvec: list[float],
    top_k: int | None = None,
    doc_meta: dict[str, dict] | None = None,
    actor=None,
    kb_meta: dict | None = None,
    mode: str | None = None,
) -> list[dict]:
    """混合检索:返回 [{id, text, score, meta, channels, breakdown}] 按最终分降序。

    doc_meta: {doc_id: 制度元数据}(用于编号/部门/时效加权与版本过滤,已在调用侧过滤)
    mode: 评测用检索模式覆盖
      · "vector"        仅向量通道
      · "lexical"       仅词法通道
      · "hybrid"        双通道 RRF 融合(不做元数据加权)
      · "hybrid_rerank" 双通道融合 + 确定性元数据重排(默认,生产模式)
    """
    top_k = top_k or settings.top_k
    pool = max(top_k * 3, top_k)          # 每个通道的候选池
    doc_meta = doc_meta or {}

    mode = (mode or ("hybrid_rerank" if settings.enable_lexical_channel else "vector")).lower()
    use_vector = mode in ("vector", "hybrid", "hybrid_rerank")
    use_lexical = mode in ("lexical", "hybrid", "hybrid_rerank")
    use_rerank = mode == "hybrid_rerank"

    vector_hits: list[dict] = []
    lexical_hits: list[dict] = []

    if use_vector and settings.enable_vector_channel:
        try:
            vector_hits = vector_store.query(kb_id, qvec, top_k=pool)
            for h in vector_hits:
                h["channels"] = ["vector"]
        except Exception:
            vector_hits = []

    if use_lexical and settings.enable_lexical_channel:
        try:
            lexical_hits = lexical_index.search(kb_id, question, top_k=pool)
        except Exception:
            lexical_hits = []

    # 单通道模式:直接按该通道分数返回(用于消融评测)
    if mode in ("vector", "lexical"):
        base = vector_hits if mode == "vector" else lexical_hits
        out = []
        for h in base[:top_k]:
            out.append({
                "id": h.get("id", ""), "text": h.get("text", ""), "meta": _norm_meta(h.get("meta")),
                "score": float(h.get("score") or 0.0),
                "gate_score": float(h.get("score") or 0.0) if mode == "vector" else None,
                "rrf_score": 0.0,
                "vector_score": float(h.get("score") or 0.0) if mode == "vector" else 0.0,
                "lexical_score": float(h.get("score") or 0.0) if mode == "lexical" else 0.0,
                "channels": [mode], "breakdown": {}, "meta_boost": 0.0, "retrieval": mode,
            })
        return out

    K = settings.rrf_k
    wv, wl = settings.rrf_vector_weight, settings.rrf_lexical_weight

    merged: dict[str, dict] = {}
    for rank, h in enumerate(vector_hits, start=1):
        cid = h.get("id") or ""
        if not cid:
            continue
        e = merged.setdefault(cid, {"id": cid, "text": h.get("text", ""), "meta": _norm_meta(h.get("meta")),
                                    "vector_score": 0.0, "lexical_score": 0.0,
                                    "channels": [], "rrf": 0.0})
        e["vector_score"] = max(e["vector_score"], float(h.get("score") or 0.0))
        e["rrf"] += wv * _rrf(rank, K)
        if "vector" not in e["channels"]:
            e["channels"].append("vector")

    for rank, h in enumerate(lexical_hits, start=1):
        cid = h.get("id") or ""
        if not cid:
            continue
        e = merged.setdefault(cid, {"id": cid, "text": h.get("text", ""), "meta": _norm_meta(h.get("meta")),
                                    "vector_score": 0.0, "lexical_score": 0.0,
                                    "channels": [], "rrf": 0.0})
        e["lexical_score"] = max(e["lexical_score"], float(h.get("score") or 0.0))
        if not e.get("text"):
            e["text"] = h.get("text", "")
        e["rrf"] += wl * _rrf(rank, K)
        if "lexical" not in e["channels"]:
            e["channels"].append("lexical")

    if not merged:
        return []

    # ---------------- 确定性元数据重排 ----------------
    q_low = (question or "").lower()
    actor_dept = getattr(actor, "dept", "") or ""
    kb_depts = ""
    if kb_meta:
        kb_depts = (kb_meta.get("allowed_depts") or "") + "," + (kb_meta.get("owner_dept") or "")

    max_rrf = max(e["rrf"] for e in merged.values()) or 1.0
    out: list[dict] = []
    for e in merged.values():
        meta = e["meta"]
        did = meta.get("doc_id", "")
        dm = doc_meta.get(did, {})

        boost = 0.0
        bd: dict[str, float] = {}

        # 1) 制度编号命中:查询中出现该文档编号 → 强信号
        doc_no = (dm.get("doc_no") or "").strip().lower()
        if doc_no and doc_no in q_low:
            b = settings.meta_boost_docno
            boost += b
            bd["doc_no"] = b

        # 2) 部门匹配:身份部门命中文档适用范围或知识库归属部门
        scope = (dm.get("dept_scope") or "").strip()
        issuer = (dm.get("issuer") or "").strip()
        if actor_dept:
            if scope and actor_dept in scope:
                b = settings.meta_boost_dept
                boost += b
                bd["dept_scope"] = b
            elif issuer and actor_dept == issuer:
                b = settings.meta_boost_dept * 0.6
                boost += b
                bd["dept_issuer"] = b
            elif kb_depts and actor_dept in kb_depts:
                b = settings.meta_boost_dept * 0.4
                boost += b
                bd["kb_dept"] = b

        # 3) 版本时效:生效日期更近的现行版本轻微加权
        eff = (dm.get("effective_date") or "").strip()
        if eff and settings.meta_boost_recency > 0:
            try:
                year = int(eff[:4])
                if year >= 2023:
                    b = settings.meta_boost_recency * min(1.0, (year - 2022) / 3)
                    boost += b
                    bd["recency"] = round(b, 4)
            except (ValueError, TypeError):
                pass

        # 4) 条款/标题命中:查询关键词出现在条款路径中(结构定位信号)
        heading = (meta.get("heading_path") or "")
        if heading:
            hits = sum(1 for tok in _query_terms(question) if tok in heading)
            if hits:
                b = settings.meta_boost_heading * min(1.0, hits / 3)
                boost += b
                bd["heading"] = round(b, 4)

        norm_rrf = e["rrf"] / max_rrf                      # 0~1
        final = norm_rrf * (1.0 - settings.meta_weight_total) + boost
        # gate_score = 向量余弦相似度(绝对量纲,用于"是否够相关"的拒答判定);
        # score = 融合分(相对量纲,仅用于排序)。二者分离,避免阈值语义被融合破坏。
        gate = e["vector_score"] if use_vector else None
        out.append({
            "id": e["id"],
            "text": e["text"],
            "meta": meta,
            "score": round(final, 4),
            "gate_score": round(gate, 4) if gate is not None else None,
            "rrf_score": round(norm_rrf, 4),
            "vector_score": round(e["vector_score"], 4),
            "lexical_score": round(e["lexical_score"], 4),
            "channels": e["channels"],
            "breakdown": bd,
            "meta_boost": round(boost, 4),
            "retrieval": "hybrid" if len(e["channels"]) > 1 else e["channels"][0],
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top_k]


def _query_terms(question: str) -> list[str]:
    """查询关键词(用于条款路径命中判定):二元组截断到较短长度以避免噪声。"""
    toks = lexical_index.tokenize(question or "")
    seen, out = set(), []
    for t in toks:
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def retrieval_stats(kb_id: str) -> dict:
    """检索通道健康状态(供系统页展示)。"""
    idx = lexical_index.lexical_cache.get(kb_id)
    return {
        "kb_id": kb_id,
        "lexical_index_chunks": len(idx) if idx else 0,
        "vector_chunks": vector_store.count(kb_id),
        "lexical_enabled": settings.enable_lexical_channel,
        "vector_enabled": settings.enable_vector_channel,
    }
