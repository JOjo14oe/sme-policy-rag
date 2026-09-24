"""词法检索索引(BM25)—— 混合检索的"关键字"通道。

为什么需要它
------------
纯向量检索在制度文档场景有两个已知短板:
1. **精确串匹配弱**:制度编号(HR-2025-001)、具体数值(15 天 / 500 元)、专有名词
   在向量空间里区分度不足,容易被语义相近但事实不同的片段压过去;
2. **低频关键词召回差**:嵌入模型对罕见词的敏感性低于关键词检索。

因此引入 BM25 词法通道,与向量通道做 RRF 融合(见 hybrid_retrieval.py)。

中文分词策略
------------
不引入分词依赖(避免额外模型/词典),采用**字符二元组(bigram)+ ASCII 词**:
- 「绩效奖金计算方式」→ 绩效 / 效奖 / 奖金 / 金计 / 计算 / 算方 / 方式
- 「HR-2025-001」→ hr / 2025 / 001
该策略在中文短文本检索上稳定、无外部依赖、与冲突检测使用的相似度口径一致。

索引为**进程内缓存**:按知识库构建,文档增删改时失效,并带 TTL 兜底。
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter

# ASCII 词(编号、数字、英文)
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
# 中日韩统一表意文字
_CJK = re.compile(r"[\u4e00-\u9fff]+")

K1 = 1.5      # BM25 词频饱和参数
B = 0.75      # BM25 长度归一化参数
TTL_SECONDS = 300.0   # 索引最长存活时间(兜底刷新)


def tokenize(text: str) -> list[str]:
    """中文二元组 + ASCII 词。"""
    if not text:
        return []
    low = text.lower()
    tokens: list[str] = _ASCII_TOKEN.findall(low)
    for run in _CJK.findall(low):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class BM25Index:
    """单知识库的 BM25 倒排索引。"""

    def __init__(self, kb_id: str, rows: list[dict]):
        self.kb_id = kb_id
        self.ids: list[str] = []
        self.docs: dict[str, dict] = {}
        self.tf: dict[str, Counter] = {}
        self.len: dict[str, int] = {}
        self.df: Counter = Counter()
        self.avgdl = 1.0
        self.built_at = time.time()

        for r in rows:
            cid = r.get("id") or ""
            if not cid:
                continue
            toks = tokenize(r.get("text") or "")
            if not toks:
                continue
            self.ids.append(cid)
            self.docs[cid] = {"text": r.get("text") or "", "meta": r.get("meta") or {}}
            c = Counter(toks)
            self.tf[cid] = c
            self.len[cid] = len(toks)
            for t in c:
                self.df[t] += 1
        if self.ids:
            self.avgdl = sum(self.len.values()) / len(self.ids)

    def __len__(self) -> int:
        return len(self.ids)

    def _idf(self, term: str) -> float:
        n = len(self.ids)
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """返回 [{id, text, score, meta, channels:[\"lexical\"]}] 按分数降序。"""
        q_tokens = set(tokenize(query))
        if not q_tokens or not self.ids:
            return []
        scores: dict[str, float] = {}
        for term in q_tokens:
            idf = self._idf(term)
            if idf <= 0:
                continue
            for cid, tf in self.tf.items():
                f = tf.get(term)
                if not f:
                    continue
                dl = self.len[cid]
                denom = f + K1 * (1 - B + B * dl / self.avgdl)
                scores[cid] = scores.get(cid, 0.0) + idf * (f * (K1 + 1)) / denom
        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:max(1, top_k)]
        top = ranked[0][1] or 1.0
        out = []
        for cid, s in ranked:
            d = self.docs.get(cid, {})
            out.append({
                "id": cid,
                "text": d.get("text", ""),
                "score": round(s / top, 4),      # 归一化,便于与向量分数量纲对齐展示
                "raw_score": round(s, 4),
                "meta": d.get("meta", {}),
                "channels": ["lexical"],
            })
        return out


class LexicalIndexCache:
    """按知识库缓存 BM25 索引;文档变更时失效,TTL 兜底。"""

    def __init__(self) -> None:
        self._cache: dict[str, BM25Index] = {}
        self._lock = threading.RLock()

    def _stale(self, idx: BM25Index) -> bool:
        return (time.time() - idx.built_at) > TTL_SECONDS

    def get(self, kb_id: str, rebuild: bool = False) -> BM25Index | None:
        with self._lock:
            idx = self._cache.get(kb_id)
            if idx is not None and not rebuild and not self._stale(idx):
                return idx
        rows = _load_chunk_rows(kb_id)
        if rows is None:
            return None
        idx = BM25Index(kb_id, rows)
        with self._lock:
            self._cache[kb_id] = idx
        return idx

    def invalidate(self, kb_id: str | None = None) -> None:
        """文档增删改后调用;kb_id 为空则清空全部。"""
        with self._lock:
            if kb_id is None:
                self._cache.clear()
            else:
                self._cache.pop(kb_id, None)

    def stats(self) -> dict:
        with self._lock:
            return {kb: {"chunks": len(idx), "age_s": round(time.time() - idx.built_at, 1)}
                    for kb, idx in self._cache.items()}


def _load_chunk_rows(kb_id: str) -> list[dict] | None:
    """从该知识库的独立向量实例读取全部块文本(含元数据)。"""
    try:
        from .vector_store import vector_store

        return vector_store.all_chunks(kb_id)
    except Exception:
        return None


lexical_cache = LexicalIndexCache()


def search(kb_id: str, query: str, top_k: int = 10) -> list[dict]:
    idx = lexical_cache.get(kb_id)
    if idx is None or len(idx) == 0:
        return []
    return idx.search(query, top_k=top_k)
