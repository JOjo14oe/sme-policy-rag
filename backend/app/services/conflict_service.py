"""【创新点3】跨知识库信息冲突检测。

问题:多知识库并存时,同一问题可能检索到来自不同库、彼此矛盾的片段;
若直接拼进上下文交给大模型,模型会把矛盾内容"合并"成一个自洽但错误的答案。

方案(全本地、可控成本):
1. 多库检索:对同一问题在多个知识库分别召回片段(复用同一个问题向量);
2. 话题相关性初筛:用字符二元组 Jaccard 快速筛出"在谈同一件事"的跨库片段对,
   控制送入模型的比对数量(默认 ≤6 对);
3. 本地模型判定:一次批量调用 Ollama(deepseek-r1:7b),严格 JSON 输出
   conflict / consistent / unrelated,并给出冲突点与双方说法;
4. 告警链路:命中冲突时向问答链路注入"冲突提示"事件与生成约束,
   要求模型分别陈述各库说法、标注来源、明确告知用户存在冲突,不得擅自合并。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..core.config import settings
from ..core.ollama_client import OllamaError, ollama
from . import kb_service, policy_service
from .vector_store import vector_store


@dataclass
class Passage:
    kb_id: str
    kb_name: str
    text: str
    score: float
    doc_id: str = ""
    filename: str = ""
    heading_path: str = ""
    page: int | None = None

    def to_dict(self) -> dict:
        return {
            "kb_id": self.kb_id, "kb_name": self.kb_name, "text": self.text,
            "score": self.score, "doc_id": self.doc_id, "filename": self.filename,
            "heading_path": self.heading_path, "page": self.page,
        }


@dataclass
class KBGroup:
    kb_id: str
    kb_name: str
    passages: list[Passage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kb_id": self.kb_id, "kb_name": self.kb_name,
                "passages": [p.to_dict() for p in self.passages]}


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---- 确定性数值分歧检测(弥补小模型判定不稳定) ----
_ARABIC_NUM = re.compile(r"\d+(?:\.\d+)?")
_CN_NUM_UNIT = re.compile(r"([一二三四五六七八九十两]+)\s*(天|个工作日|工作日|个月|月|年|次|元|日|周|小时|分钟|%|％)")
_CN_DIGIT = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _cn_to_int(s: str) -> str:
    """极简中文数字转换(覆盖 1~99 的常见写法,用于规则类数值比对)。"""
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if s.startswith("十"):
        rest = s[1:]
        return str(10 + int(_CN_DIGIT.get(rest, "0")))
    if "十" in s:
        tens, _, ones = s.partition("十")
        t = _CN_DIGIT.get(tens, "1")
        o = _CN_DIGIT.get(ones, "0") if ones else "0"
        return str(int(t) * 10 + int(o))
    return s


def numbers_in(text: str) -> set[str]:
    """抽取文本中的数值(阿拉伯数字 + 带单位的中文数字),用于数值一致性比对。"""
    out: set[str] = set(_ARABIC_NUM.findall(text or ""))
    for m in _CN_NUM_UNIT.finditer(text or ""):
        out.add(_cn_to_int(m.group(1)))
    return out


def numeric_divergence(a_text: str, b_text: str) -> dict | None:
    """若双方都给出数值且存在互不相交的取值,返回分歧明细(确定性判定)。"""
    na, nb = numbers_in(a_text), numbers_in(b_text)
    if not na or not nb:
        return None
    only_a, only_b = sorted(na - nb), sorted(nb - na)
    if only_a and only_b:
        return {"a_only": only_a, "b_only": only_b}
    return None


def _cosine(a, b) -> float:
    import numpy as np

    if a is None or b is None:
        return 0.0
    aa = np.asarray(a, dtype=np.float32)
    bb = np.asarray(b, dtype=np.float32)
    if aa.size == 0 or aa.size != bb.size:
        return 0.0
    na, nb = float(np.linalg.norm(aa)), float(np.linalg.norm(bb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(aa, bb) / (na * nb))


def multi_kb_retrieve(
    question: str,
    kb_ids: list[str] | None = None,
    per_kb: int | None = None,
    max_kbs: int | None = None,
    min_score: float | None = None,
    qvec: list[float] | None = None,
    actor=None,
) -> list[KBGroup]:
    """在多个知识库分别召回片段(单次问题嵌入,复用向量)。

    【增强B】只检索该身份可见的知识库;
    【增强A】剔除已废止/未生效版本,避免把历史版本当成现行规定参与冲突判定。
    """
    per_kb = per_kb or settings.conflict_per_kb
    max_kbs = max_kbs or settings.conflict_multi_kb_n
    min_score = settings.conflict_min_score if min_score is None else min_score

    all_list = kb_service.list_kbs()
    if actor is not None:
        try:
            from . import auth_service

            all_list = auth_service.filter_kbs(all_list, actor)
        except Exception:
            pass
    all_kbs = {k["id"]: k for k in all_list}
    if qvec is None:
        qvec = ollama.embed_one(question)

    if kb_ids:
        ordered = [kid for kid in kb_ids if kid in all_kbs][:max_kbs]
    else:
        # 未指定时:按"库质心与问题的相似度"排序,取最相关的若干个库参与检测
        scored: list[tuple[float, str]] = []
        for kid in all_kbs:
            prof = kb_service.profile_vector(kid)
            scored.append((_cosine(prof, qvec), kid))
        scored.sort(reverse=True)
        ordered = [kid for _, kid in scored[:max_kbs]]

    from . import doc_service, hybrid_retrieval

    groups: list[KBGroup] = []
    for kid in ordered:
        kb = all_kbs[kid]
        if vector_store.count(kid) == 0:
            continue
        meta_map: dict = {}
        try:
            meta_map = doc_service.doc_meta_map(kid)
        except Exception:
            meta_map = {}
        allowed: set[str] | None = None
        if settings.exclude_expired_docs and meta_map:
            try:
                allowed = set(policy_service.filter_retrievable(meta_map))
            except Exception:
                allowed = None
        # 【改进】冲突检测同样使用混合检索(向量 + 词法),提升跨库片段召回质量
        fused = hybrid_retrieval.hybrid_search(
            kid, question, qvec, top_k=max(per_kb * 3, per_kb),
            doc_meta=meta_map, actor=actor, kb_meta=kb,
        )
        passages = []
        for h in fused:
            # 相关度门槛用向量绝对相似度(gate_score),避免融合分相对量纲导致门槛失效
            gate = h.get("gate_score")
            if gate is not None and gate < min_score:
                continue
            if gate is None and h["score"] < min_score:
                continue
            meta = h["meta"] or {}
            did = meta.get("doc_id", "")
            if allowed is not None and did and did not in allowed:
                continue
            passages.append(Passage(
                kb_id=kid, kb_name=kb["name"], text=h["text"], score=h["score"],
                doc_id=did, filename=meta.get("filename", ""),
                heading_path=meta.get("heading_path", ""),
                page=(meta.get("page") if (meta.get("page") or -1) > 0 else None),
            ))
            if len(passages) >= per_kb:
                break
        if passages:
            groups.append(KBGroup(kb_id=kid, kb_name=kb["name"], passages=passages))
    return groups


def build_pairs(groups: list[KBGroup], limit: int | None = None,
                topic_sim: float | None = None) -> list[dict]:
    """跨库片段对初筛:保留话题相关的对,并附确定性数值分歧标记。"""
    limit = limit or settings.conflict_pairs_limit
    topic_sim = settings.conflict_topic_sim if topic_sim is None else topic_sim

    cands: list[dict] = []
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            for pa in groups[i].passages:
                bg_a = _bigrams(pa.text)
                for pb in groups[j].passages:
                    sim = _jaccard(bg_a, _bigrams(pb.text))
                    cands.append({
                        "a": pa, "b": pb,
                        "topic_sim": round(sim, 4),
                        "numeric": numeric_divergence(pa.text, pb.text),
                        "score_sum": pa.score + pb.score,
                    })
    # 排序优先级:存在数值分歧的对 > 话题相似度 > 检索分之和
    cands.sort(key=lambda c: (1 if c["numeric"] else 0, c["topic_sim"], c["score_sum"]), reverse=True)
    kept = [c for c in cands if c["topic_sim"] >= topic_sim or c["numeric"]][:limit]
    if not kept:  # 无达标对时取少量高分对做弱比对,避免漏检
        kept = cands[:min(2, len(cands))]
    return kept


_JUDGE_SYSTEM = """你是严谨的事实一致性审查员,负责跨知识库信息冲突检测。

给定若干【片段对】,每对来自不同的知识库。请判断每对就同一话题的陈述关系:
- "conflict": 就同一事项给出互相排斥的结论,不能同时为真。
  重点比对:数字/天数/期限/比例/金额/日期、可否/是否这类极性相反的规则、相互排斥的流程要求。
  只要同一事项的取值不同(例如一个说 5 天、另一个说 8 天;一个说"不结转"、另一个说"可结转"),即判 conflict。
- "consistent": 说法一致或互为补充,不矛盾(例如同一数值、同一规则的不同表述)。
- "unrelated": 讨论的不是同一话题,无法比较。

只输出 JSON 数组,不要任何解释文字。数组元素格式:
{"pair": 1, "relation": "conflict", "point": "冲突点(一句话)", "a": "A方说法(简短)", "b": "B方说法(简短)"}
每个输入片段对都必须出现在输出数组中。"""


def _extract_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        # 容错:逐个对象解析
        out = []
        for obj in re.finditer(r"\{[^{}]*\}", m.group(0), re.S):
            try:
                out.append(json.loads(obj.group(0)))
            except json.JSONDecodeError:
                continue
        return out


def judge_pairs(question: str, pairs: list[dict]) -> tuple[list[dict], int]:
    """一次批量调用本地模型判定所有片段对。返回 (relations, elapsed_ms)。"""
    if not pairs:
        return [], 0
    lines = []
    for idx, p in enumerate(pairs, start=1):
        lines.append(
            f"【片段对 {idx}】\n"
            f"A(知识库:{p['a'].kb_name}):{p['a'].text[:400]}\n"
            f"B(知识库:{p['b'].kb_name}):{p['b'].text[:400]}"
        )
    user = f"【用户问题】{question or '(未提供)'}\n\n" + "\n\n".join(lines)
    t0 = time.perf_counter()
    try:
        raw = ollama.chat(
            [{"role": "system", "content": _JUDGE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.0, max_tokens=700,
        )
    except OllamaError:
        raise
    elapsed = int((time.perf_counter() - t0) * 1000)
    return _extract_json_array(raw), elapsed


def detect(question: str, kb_ids: list[str] | None = None,
           qvec: list[float] | None = None, actor=None) -> dict:
    """完整冲突检测:多库检索 → 初筛 → 模型判定 → 版本/真冲突分类 → 结构化结果。"""
    t0 = time.perf_counter()
    groups = multi_kb_retrieve(question, kb_ids=kb_ids, qvec=qvec, actor=actor)
    if len(groups) < 2:
        return {
            "checked": False,
            "reason": f"参与检测的知识库不足 2 个(实际 {len(groups)} 个),无跨库冲突可能",
            "groups": [g.to_dict() for g in groups],
            "pairs_considered": 0,
            "relations": [],
            "conflicts": [],
            "version_conflicts": 0,
            "genuine_conflicts": 0,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    pairs = build_pairs(groups)
    relations, judge_ms = judge_pairs(question, pairs)

    # 【增强A】取相关文档的制度元数据,用于区分「版本差异」与「真冲突」
    from . import doc_service

    meta_cache: dict[tuple[str, str], dict] = {}

    def _meta(kb_id: str, doc_id: str) -> dict:
        key = (kb_id, doc_id)
        if key not in meta_cache:
            try:
                meta_cache[key] = doc_service.doc_meta_map(kb_id, [doc_id]).get(doc_id, {}) or {}
            except Exception:
                meta_cache[key] = {}
        return meta_cache[key]

    conflicts: list[dict] = []
    rel_list: list[dict] = []
    llm_by_pair: dict[int, dict] = {}
    for item in relations:
        try:
            idx = int(item.get("pair", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(pairs):
            llm_by_pair[idx] = item

    for idx, pair in enumerate(pairs, start=1):
        item = llm_by_pair.get(idx, {})
        relation = str(item.get("relation", "")).strip().lower()
        num = pair.get("numeric")
        # 确定性数值分歧:仅在话题足够相关时采纳,作为对小模型判定的互补兜底
        numeric_conflict = bool(
            num and settings.conflict_numeric_check
            and pair["topic_sim"] >= settings.conflict_numeric_topic_sim
        )
        if numeric_conflict:
            relation = "conflict"
        if relation not in ("conflict", "consistent", "unrelated"):
            relation = "conflict" if numeric_conflict else "unrelated"

        a_meta = _meta(pair["a"].kb_id, pair["a"].doc_id)
        b_meta = _meta(pair["b"].kb_id, pair["b"].doc_id)
        verdict = policy_service.classify_conflict(a_meta, b_meta) if a_meta and b_meta else {
            "kind": "genuine_conflict", "kind_label": "跨来源规定冲突", "preferred": None,
        }

        entry = {
            "pair": idx,
            "relation": relation,
            "point": str(item.get("point", "")).strip(),
            "a_claim": str(item.get("a", "")).strip(),
            "b_claim": str(item.get("b", "")).strip(),
            "topic_sim": pair["topic_sim"],
            "numeric_divergence": num,
            "kind": verdict["kind"],
            "kind_label": verdict["kind_label"],
            "preferred": verdict.get("preferred"),
            "detected_by": "numeric+llm" if (numeric_conflict and item.get("relation") == "conflict")
                           else ("numeric" if numeric_conflict else "llm"),
            "a": {**pair["a"].to_dict(),
                  "doc_no": a_meta.get("doc_no", ""), "effective_date": a_meta.get("effective_date", ""),
                  "title": a_meta.get("title", ""), "state": policy_service.doc_state(a_meta) if a_meta else ""},
            "b": {**pair["b"].to_dict(),
                  "doc_no": b_meta.get("doc_no", ""), "effective_date": b_meta.get("effective_date", ""),
                  "title": b_meta.get("title", ""), "state": policy_service.doc_state(b_meta) if b_meta else ""},
        }
        if not entry["point"]:
            if numeric_conflict and num:
                entry["point"] = (f"自动检出数值分歧:一方取值 {','.join(num['a_only'])} 与另一方 "
                                  f"{','.join(num['b_only'])} 互不相符(同一事项取值不一致)")
            elif relation == "conflict":
                entry["point"] = "同一事项的说法互相排斥"
        rel_list.append(entry)
        if relation == "conflict":
            conflicts.append(entry)

    vc = sum(1 for c in conflicts if c["kind"] == "version_conflict")
    gc = len(conflicts) - vc
    return {
        "checked": True,
        "groups": [g.to_dict() for g in groups],
        "pairs_considered": len(pairs),
        "relations": rel_list,
        "conflicts": conflicts,
        "version_conflicts": vc,
        "genuine_conflicts": gc,
        "judge_ms": judge_ms,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "kb_count": len(groups),
    }


def format_conflict_note(conflicts: list[dict]) -> str:
    """把冲突转成给生成模型的中文约束说明(区分版本差异与真冲突)。"""
    if not conflicts:
        return ""
    version_cs = [c for c in conflicts if c.get("kind") == "version_conflict"]
    genuine_cs = [c for c in conflicts if c.get("kind") != "version_conflict"]

    parts: list[str] = []
    if version_cs:
        parts.append("【制度版本差异提醒】同一制度存在多个版本,说法不一致,应以现行有效版本为准:")
        for i, c in enumerate(version_cs, start=1):
            pref = c.get("preferred") or {}
            parts.append(
                f"{i}. 冲突点:{c.get('point') or '新旧版本规定不一致'}\n"
                f"   - 版本A「{c['a']['kb_name']}」{c['a'].get('doc_no') or ''}"
                f"(生效 {c['a'].get('effective_date') or '未填写'}):{c.get('a_claim') or c['a']['text'][:110]}\n"
                f"   - 版本B「{c['b']['kb_name']}」{c['b'].get('doc_no') or ''}"
                f"(生效 {c['b'].get('effective_date') or '未填写'}):{c.get('b_claim') or c['b']['text'][:110]}\n"
                f"   - 建议依据:{pref.get('title') or ''} {pref.get('doc_no') or ''}"
                f"(生效 {pref.get('effective_date') or '未填写'},{pref.get('state_label') or ''})"
            )
        parts.append("回答要求:以现行版本为准给出结论,同时简要说明与旧版本的差异,并标注来源。")
    if genuine_cs:
        parts.append("【跨知识库冲突告警】检索到的资料来自不同制度/来源且存在互相矛盾的说法:")
        for i, c in enumerate(genuine_cs, start=1):
            parts.append(
                f"{i}. 冲突点:{c.get('point') or '同一事实说法不一致'}\n"
                f"   - 「{c['a']['kb_name']}」的说法:{c.get('a_claim') or c['a']['text'][:120]}\n"
                f"   - 「{c['b']['kb_name']}」的说法:{c.get('b_claim') or c['b']['text'][:120]}"
            )
        parts.append(
            "回答要求:必须分别陈述各来源的对应说法并标注来源,明确提示用户存在冲突,"
            "不得擅自合并、折中或只择其一给出结论。"
        )
    return "\n".join(parts)
