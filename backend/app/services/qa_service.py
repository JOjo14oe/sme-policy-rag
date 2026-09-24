"""检索问答服务:自动路由 + 手动指定库兜底 + 引用溯源 + 阈值拒答。

流程(双兜底):
1. 自动模式:问题嵌入 → 与各知识库质心比较 → 命中则自动选库;
   低于 route_min_sim 时给出候选列表,不硬答,交由用户手动指定 → 兜底。
2. 手动模式:用户显式指定 kb_id,直接在该库检索。
3. 检索阈值过滤:低于 score_threshold 的片段视为"未找到",宁可拒答也不幻觉。
4. 生成:上下文 + 引用标注 → Ollama 本地模型流式回答。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterator

from ..core.config import settings
from ..core.ollama_client import OllamaError, ollama
from . import kb_service, policy_service
from .vector_store import vector_store

logger = logging.getLogger("rag-local.qa")

_SYSTEM_PROMPT = """你是一个严谨的私有知识库问答助手。你只能依据【参考资料】作答。

铁律(违反即为错误回答):
1. **严禁使用参考资料之外的任何知识、常识或推测**,包括行业惯例、通常做法、估算数值;
2. 若参考资料**没有**说明某个要点,必须明确写"参考资料未提及该内容",**不得给出任何猜测的数值或结论**;
3. 若参考资料整体不足或与问题无关,直接回答"知识库中未找到足够相关内容";
4. 若问题与知识库主题无关(如闲聊、天气),礼貌说明你只能回答知识库内的制度问题。

回答要求:
- 使用简体中文,条理清晰,先给结论再给依据;
- 引用格式:使用上标引用如[1][2],编号对应【参考资料】中每条前面的编号;
- 参考资料中若标注了来源文件、制度编号与版本状态,回答时应据此说明依据的是哪一份制度。"""


@dataclass
class RouteResult:
    kb_id: str | None
    kb_name: str | None
    scores: list[dict] = field(default_factory=list)  # [{kb_id, kb_name, score}]
    auto: bool = True


@dataclass
class SearchHit:
    text: str
    score: float
    doc_id: str
    filename: str
    heading_path: str = ""
    page: int | None = None
    # 【改进】混合检索溯源信息
    channels: list[str] = field(default_factory=list)   # 命中的通道:vector / lexical
    retrieval: str = ""                                 # vector / lexical / hybrid
    vector_score: float = 0.0
    lexical_score: float = 0.0
    meta_boost: float = 0.0
    # 【关键】gate_score = 向量余弦相似度(绝对量纲),用于"是否够相关"的拒答判定;
    # score 为融合分(相对量纲),仅用于排序 —— 二者分离避免拒答阈值被融合破坏。
    gate_score: float | None = None
    # 【改进】制度元数据(用于上下文来源标注,帮助模型区分相似制度)
    doc_no: str = ""
    state_label: str = ""
    effective_date: str = ""

    @property
    def relevance(self) -> float:
        """用于阈值判定的相关度:优先取向量绝对相似度。"""
        return self.gate_score if self.gate_score is not None else self.score


def _cosine(a, b) -> float:
    if a is None or b is None:
        return 0.0
    import numpy as np

    aa = np.asarray(a, dtype=np.float32)
    bb = np.asarray(b, dtype=np.float32)
    if aa.size == 0 or bb.size == 0 or aa.size != bb.size:
        return 0.0
    na = float(np.linalg.norm(aa))
    nb = float(np.linalg.norm(bb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(aa, bb) / (na * nb))


def route_question(question: str, actor=None) -> RouteResult:
    """自动路由:找出与问题最相关的知识库。

    【增强B】仅在该身份可见的知识库中路由;
    【增强A】排除没有现行有效文档的库(避免路由到只剩历史版本的库)。
    """
    qvec = ollama.embed_one(question)
    kbs = kb_service.list_kbs()
    if actor is not None:
        try:
            from . import auth_service

            kbs = auth_service.filter_kbs(kbs, actor)
        except Exception:
            pass
    scored: list[dict] = []
    for kb in kbs:
        prof = kb_service.profile_vector(kb["id"])
        if not prof:
            continue
        s = _cosine(qvec, prof)
        scored.append({"kb_id": kb["id"], "kb_name": kb["name"], "score": round(s, 4),
                       "doc_count": kb["doc_count"],
                       "visibility": kb.get("visibility", "all")})
    scored.sort(key=lambda x: x["score"], reverse=True)
    if not scored:
        return RouteResult(kb_id=None, kb_name=None, scores=scored, auto=True)
    best = scored[0]
    if best["score"] >= settings.route_min_sim:
        return RouteResult(kb_id=best["kb_id"], kb_name=best["kb_name"], scores=scored, auto=True)
    # 未达阈值:不自动选,返回候选让前端提示用户手动兜底
    return RouteResult(kb_id=None, kb_name=None, scores=scored[: settings.route_top_n], auto=True)


def current_doc_ids(kb_id: str) -> set[str]:
    """【增强A】该库中「现行有效」的 doc_id 集合(用于检索过滤)。"""
    from . import doc_service

    meta_map = doc_service.doc_meta_map(kb_id)
    return policy_service.filter_retrievable(meta_map)


def search_kb(kb_id: str, question: str, top_k: int | None = None,
              with_vectors: bool = False, doc_meta: dict[str, dict] | None = None,
              actor=None, kb_meta: dict | None = None,
              mode: str | None = None) -> tuple[list[SearchHit], list[float]]:
    """在指定库检索(混合:向量 + BM25 词法,RRF 融合 + 确定性元数据重排)。

    【增强A】默认剔除已废止/未生效的制度版本,避免用旧制度回答新问题;
    【改进】向量通道与词法通道并行召回后融合,制度编号/部门/时效/条款路径参与重排;
    mode 仅用于评测对比(vector / lexical / hybrid / hybrid_rerank)。
    """
    from . import hybrid_retrieval

    qvec = ollama.embed_one(question)
    raw_k = (top_k or settings.top_k)

    # 制度元数据(版本过滤 + 加权依据)
    meta_map = doc_meta
    if meta_map is None:
        try:
            from . import doc_service

            meta_map = doc_service.doc_meta_map(kb_id)
        except Exception:
            meta_map = {}
    allowed: set[str] | None = None
    if settings.exclude_expired_docs and meta_map:
        allowed = policy_service.filter_retrievable(meta_map)

    fused = hybrid_retrieval.hybrid_search(
        kb_id, question, qvec, top_k=max(raw_k * 3, raw_k),
        doc_meta=meta_map or {}, actor=actor, kb_meta=kb_meta, mode=mode,
    )

    hits: list[SearchHit] = []
    for h in fused:
        meta = h["meta"] or {}
        did = meta.get("doc_id", "")
        if allowed is not None and did and did not in allowed:
            continue                      # 已废止/未生效版本:不参与回答
        dm = (meta_map or {}).get(did, {}) or {}
        st = policy_service.doc_state(dm) if dm else ""
        hits.append(SearchHit(
            text=h["text"],
            score=h["score"],
            doc_id=did,
            filename=meta.get("filename", ""),
            heading_path=meta.get("heading_path", ""),
            page=meta.get("page"),
            channels=h.get("channels") or [],
            retrieval=h.get("retrieval", ""),
            vector_score=h.get("vector_score", 0.0),
            lexical_score=h.get("lexical_score", 0.0),
            meta_boost=h.get("meta_boost", 0.0),
            gate_score=h.get("gate_score"),
            doc_no=dm.get("doc_no", "") or "",
            state_label=policy_service.STATE_LABEL.get(st, "") if st else "",
            effective_date=dm.get("effective_date", "") or "",
        ))
        if len(hits) >= raw_k:
            break
    return hits, qvec


def dedupe_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """同文档相邻片段合并去重,避免上下文塞满同一来源。

    【改进】用 context_min_score(宽松)决定"是否纳入上下文",
    而"是否作答"由 answer_stream 用 score_threshold(严格)基于最高分判定。
    """
    out: list[SearchHit] = []
    seen_docs: set[str] = set()
    per_doc: dict[str, int] = {}
    cap = max(1, settings.max_chunks_per_doc)
    for h in sorted(hits, key=lambda x: x.score, reverse=True):
        if h.relevance < settings.context_min_score:
            continue
        used = per_doc.get(h.doc_id, 0)
        if used < cap:
            per_doc[h.doc_id] = used + 1
            seen_docs.add(h.doc_id)
            out.append(h)
    return out


def _format_context(hits: list[SearchHit]) -> tuple[str, dict[str, dict]]:
    """编号化上下文 + 引用映射。

    【改进】每条资料都显式标注**来源文件名 + 制度编号 + 版本状态 + 条款路径**,
    使模型能够区分"相似但不同"的制度(实测修复:培训住宿被误答为差旅标准的案例)。
    """
    parts: list[str] = []
    cite_map: dict[str, dict] = {}
    budget = settings.context_chars_limit
    used = 0
    for i, h in enumerate(hits, start=1):
        label = f"[{i}] 来源:{h.filename or '未知文件'}"
        if i == 1:
            label += " ★最相关来源(优先依据)"
        meta_bits = []
        if getattr(h, "doc_no", ""):
            meta_bits.append(f"编号 {h.doc_no}")
        if getattr(h, "state_label", ""):
            meta_bits.append(h.state_label)
        if getattr(h, "effective_date", ""):
            meta_bits.append(f"生效 {h.effective_date}")
        if h.heading_path:
            meta_bits.append(h.heading_path)
        if meta_bits:
            label += "(" + " · ".join(meta_bits) + ")"
        seg = f"{label}\n{h.text}"
        if used + len(seg) > budget and parts:
            break
        parts.append(seg)
        used += len(seg)
        cite_map[str(i)] = {
            "doc_id": h.doc_id,
            "filename": h.filename,
            "heading_path": h.heading_path,
            "page": h.page,
        }
    return "\n\n".join(parts), cite_map


def _disambiguation_note(hits: list[SearchHit]) -> str:
    """上下文含多份相似制度时,生成"按来源辨析"的约束提示。"""
    if len(hits) < 2:
        return ""
    docs: dict[str, float] = {}
    for h in hits:
        if h.filename:
            docs[h.filename] = max(docs.get(h.filename, 0.0), h.score)
    if len(docs) < 2:
        return ""
    top = sorted(docs.items(), key=lambda kv: kv[1], reverse=True)
    # 仅当第 2 份来源与第 1 份分数接近(≥85%)时才提示,避免误导
    if top[1][1] < top[0][1] * 0.85:
        return ""
    names = "、".join(f"「{n}」" for n, _ in top[:3])
    return (
        f"【多来源辨析提示】本次检索到 {len(top)} 份主题相近的制度:{names}。"
        "它们可能分属不同场景(如出差与培训、总部与分公司),回答时**必须逐份核对来源与制度编号**,"
        "只采用与问题场景真正对应的那一份的数值与规则,不得把不同制度的数字混用;"
        "若题目场景无法确定对应哪一份,请说明存在多份相近规定并分别列出。"
    )


def _clean_answer(text: str) -> str:
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    return text


def answer_stream(question: str, kb_id: str | None, kb_name: str | None,
                  candidate_kbs: list[dict] | None = None, actor=None) -> Iterator[dict]:
    """问答主流程(生成器,逐事件产出):
     事件: route → hits → conflict(可选) → delta → done
    可能直接产出 no_context / error。

    candidate_kbs: 自动路由候选库,用于跨库冲突检测;
    actor: 请求身份,用于【增强B】可见性过滤与审计。
    """
    if not kb_id:
        # 自动路由未找到足够匹配 → 不生成
        yield {"type": "route", "kb_id": None, "kb_name": None}
        yield {"type": "no_context", "reason": "未能自动匹配到合适的知识库,请手动选择或补充知识库内容"}
        return

    kb_meta = None
    try:
        kb_meta = kb_service.get_kb(kb_id)
    except Exception:
        kb_meta = None
    try:
        hits, _qvec = search_kb(kb_id, question, actor=actor, kb_meta=kb_meta)
    except OllamaError as e:
        yield {"type": "error", "message": str(e)}
        return

    # 判定逻辑:最高相关度决定"是否作答";单个片段用宽松门槛决定是否纳入上下文
    best_rel = max((h.relevance for h in hits), default=0.0)
    good = [h for h in hits if h.relevance >= settings.context_min_score]
    yield {
        "type": "route",
        "kb_id": kb_id,
        "kb_name": kb_name,
        "retrieval": "hybrid" if settings.enable_lexical_channel else "vector",
        "best_relevance": round(best_rel, 4),
        "top_scores": [{"score": h.score, "relevance": round(h.relevance, 4),
                        "filename": h.filename, "retrieval": h.retrieval} for h in hits[:5]],
    }
    if not good or best_rel < settings.score_threshold:
        yield {
            "type": "no_context",
            "reason": (f"在知识库「{kb_name}」中未找到足够相关的内容"
                       f"(最高相关度 {round(best_rel, 3)},门槛 {settings.score_threshold})"),
            "hits": [{"text": h.text[:200], "score": h.score, "filename": h.filename} for h in hits[:5]],
        }
        return

    selected = dedupe_hits(good)
    context, cite_map = _format_context(selected)

    # 【改进】近似多来源辨析提示:当上下文里出现多份"名称/主题相近"的制度时,
    # 明确要求模型按来源逐条比对,避免把不同制度的数值混用
    # (实测修复场景:培训差旅标准 600 元 与 差旅报销标准 500 元 被混答)。
    disambiguate_note = _disambiguation_note(selected)
    yield {"type": "hits", "hits": [
        {"text": h.text, "score": h.score, "filename": h.filename,
         "doc_id": h.doc_id, "heading_path": h.heading_path, "page": h.page,
         "channels": h.channels, "retrieval": h.retrieval,
         "vector_score": h.vector_score, "lexical_score": h.lexical_score,
         "meta_boost": h.meta_boost, "relevance": round(h.relevance, 4),
         "doc_no": h.doc_no, "state_label": h.state_label}
        for h in selected
    ], "disambiguation": bool(disambiguate_note)}

    # ---- 【创新点3】跨知识库冲突检测:命中多库矛盾时告警并约束生成 ----
    conflict_note = ""
    if settings.enable_conflict_check:
        try:
            from . import conflict_service

            scan_ids = list(dict.fromkeys([kb_id] + [c["kb_id"] for c in (candidate_kbs or [])]))
            scan = conflict_service.detect(question, kb_ids=scan_ids, qvec=_qvec, actor=actor)
            logger.info(
                "conflict scan: kbs=%s scanned=%s pairs=%s conflicts=%s (version=%s genuine=%s) elapsed=%sms",
                scan_ids, scan.get("kb_count"), scan.get("pairs_considered"),
                len(scan.get("conflicts") or []), scan.get("version_conflicts"),
                scan.get("genuine_conflicts"), scan.get("elapsed_ms"),
            )
            if scan.get("conflicts"):
                conflict_note = conflict_service.format_conflict_note(scan["conflicts"])
                yield {
                    "type": "conflict",
                    "conflicts": [
                        {
                            "point": c.get("point"),
                            "kind": c.get("kind"),
                            "kind_label": c.get("kind_label"),
                            "preferred": c.get("preferred"),
                            "a": {"kb_name": c["a"]["kb_name"], "filename": c["a"]["filename"],
                                  "text": c["a"]["text"][:240], "claim": c.get("a_claim"),
                                  "doc_no": c["a"].get("doc_no"), "effective_date": c["a"].get("effective_date"),
                                  "state": c["a"].get("state")},
                            "b": {"kb_name": c["b"]["kb_name"], "filename": c["b"]["filename"],
                                  "text": c["b"]["text"][:240], "claim": c.get("b_claim"),
                                  "doc_no": c["b"].get("doc_no"), "effective_date": c["b"].get("effective_date"),
                                  "state": c["b"].get("state")},
                            "topic_sim": c.get("topic_sim"),
                        }
                        for c in scan["conflicts"]
                    ],
                    "kb_count": scan.get("kb_count"),
                    "pairs_considered": scan.get("pairs_considered"),
                    "version_conflicts": scan.get("version_conflicts"),
                    "genuine_conflicts": scan.get("genuine_conflicts"),
                    "elapsed_ms": scan.get("elapsed_ms"),
                }
        except OllamaError as e:
            logger.warning("conflict scan skipped (ollama): %s", e)
        except Exception as e:
            logger.warning("conflict scan failed: %s", e, exc_info=True)

    user_msg = (
        f"【参考资料】\n{context}\n\n"
        + (f"{conflict_note}\n\n" if conflict_note else "")
        + (f"{disambiguate_note}\n\n" if disambiguate_note else "")
        + f"【问题】\n{question}\n\n请结合【参考资料】回答上述问题,并按要求标注引用。"
    )
    try:
        parts: list[str] = []
        for delta in ollama.chat_stream(
            messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
        ):
            parts.append(delta)
            yield {"type": "delta", "text": delta}
        full = _clean_answer("".join(parts))
        yield {"type": "done", "answer": full, "citations": cite_map,
               "conflict_detected": bool(conflict_note)}
    except OllamaError as e:
        yield {"type": "error", "message": str(e)}
