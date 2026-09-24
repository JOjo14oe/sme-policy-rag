"""全局配置:数据目录、Ollama 地址与模型、检索参数。

全部可通过环境变量覆盖,也可通过 data/config.json 持久化覆盖(默认不生成)。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 目录层级: config.py 位于 <root>/backend/app/core/config.py
APP_DIR = Path(__file__).resolve().parent.parent          # backend/app
BACKEND_DIR = APP_DIR.parent                              # backend
PROJECT_ROOT = BACKEND_DIR.parent                         # rag-local

# 让 `python -m app.main` 或 `uvicorn app.main:app` 均能工作
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _resolve_data_root() -> Path:
    env = os.environ.get("RAG_LOCAL_DATA", "").strip()
    return Path(env) if env else PROJECT_ROOT / "data"


@dataclass
class Settings:
    # ---- 路径 ----
    data_root: Path = field(default_factory=_resolve_data_root)
    meta_db_path: Path | None = None
    chroma_dir: Path | None = None          # 旧版单实例向量目录(保留字段,用于一次性迁移)
    vectors_dir: Path | None = None         # 【创新点1】每知识库独立向量实例根目录
    files_dir: Path | None = None
    web_dir: Path | None = None

    # ---- Ollama ----
    ollama_host: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    chat_model: str = field(default_factory=lambda: os.environ.get("RAG_CHAT_MODEL", "deepseek-r1:7b"))
    embed_model: str = field(default_factory=lambda: os.environ.get("RAG_EMBED_MODEL", "bge-m3"))
    embed_dim: int = 1024  # bge-m3 默认维度,首次嵌入后按实际校准
    # keep_alive 控制模型驻留时间;配合 OLLAMA_MAX_LOADED_MODELS=1 防显存挤爆
    ollama_keep_alive: str = field(default_factory=lambda: os.environ.get("OLLAMA_KEEP_ALIVE", "10m"))

    # ---- 切分 ----
    chunk_max_chars: int = 600
    chunk_overlap_chars: int = 80

    # ---- 检索 ----
    top_k: int = 6
    # 相关度门槛(拒答判定):基于【向量余弦相似度】的绝对量纲。
    # 经评测集校准(scripts/eval_retrieval.py,2026-09):域内问题门槛分 0.65~0.81,
    # 域外问题(天气/写诗)0.29~0.45 → 取 0.55 可干净分离,避免"硬编"无关问题。
    score_threshold: float = 0.55
    # 上下文纳入门槛:低于该值的片段不进入生成上下文(比拒答门槛宽松,
    # 避免多要点问题中"次要但必要"的条款被一并滤掉 —— 实测修复案例:
    # 问"年假几天+能否结转"时,天数条款因门槛过高被丢弃导致答不全)。
    context_min_score: float = 0.34
    # 上下文装配:同一文档最多取多少块(多要点问题需要同一制度的多个条款;
    # 实测修复案例:问"年假几天+能否结转"时,天数条款因上限=3 被挤出上下文)
    max_chunks_per_doc: int = 5
    route_min_sim: float = 0.36        # 自动路由的最低相似度
    route_top_n: int = 3                # 候选呈现的库数量

    # ---- 【改进】混合检索(向量 + BM25 词法)与确定性元数据重排 ----
    enable_vector_channel: bool = True      # 向量通道开关
    enable_lexical_channel: bool = True     # 词法(BM25)通道开关
    rrf_k: int = 60                         # RRF 融合常数
    rrf_vector_weight: float = 1.0          # 向量通道权重
    rrf_lexical_weight: float = 1.0         # 词法通道权重
    meta_weight_total: float = 0.25         # 元数据权重占比上限(其余留给 RRF 融合分)
    meta_boost_docno: float = 0.18          # 制度编号命中加权
    meta_boost_dept: float = 0.06           # 部门匹配加权
    meta_boost_recency: float = 0.03        # 版本时效加权
    meta_boost_heading: float = 0.03        # 条款路径命中加权

    # ---- 问答生成 ----
    temperature: float = 0.3
    max_tokens: int = 1024
    context_chars_limit: int = 6000     # 送入模型的上下文总字符上限

    # ---- 自动分类 ----
    # 说明:bge-m3 中文长文本余弦基线偏高(无关文档间普遍 0.35~0.5),
    # 阈值过低会把所有文档链成一个大簇;经实测 0.48~0.52 可较好切分主题。
    cluster_sim_threshold: float = 0.48  # 与已建簇质心相似度低于该值则新建簇
    classify_doc_sample_chars: int = 1500  # 单文档用于聚类的采样字符数

    # ---- 【创新点3】跨知识库冲突检测 ----
    enable_conflict_check: bool = True      # 是否开启多库检索 + 矛盾识别
    conflict_multi_kb_n: int = 3            # 参与冲突检测的知识库数量上限
    conflict_per_kb: int = 2                # 每个库取多少片段参与比对
    conflict_pairs_limit: int = 6           # 送入判定模型的片段对上限
    conflict_topic_sim: float = 0.10        # 话题相关性初筛(字符二元组 Jaccard)
    conflict_min_score: float = 0.42        # 片段相关度下限(低于则不参与比对)
    conflict_numeric_check: bool = True     # 确定性数值分歧检测(弥补小模型不稳定)
    conflict_numeric_topic_sim: float = 0.30  # 数值分歧采纳所需的最低话题相似度

    # ---- 【增强A】制度版本与生效期 ----
    exclude_expired_docs: bool = True       # 检索时排除已废止/未生效版本
    expiring_soon_days: int = 30            # 到期提醒窗口(天)
    prefer_current_version: bool = True     # 同制度多版本时优先现行版

    # ---- 【增强B】身份与权限 ----
    enforce_permissions: bool = True        # 是否执行知识库可见性鉴权
    default_role: str = "admin"             # 未携带身份时的默认角色(本机单人使用为 admin)
    default_dept: str = ""                  # 未携带身份时的默认部门
    default_actor_name: str = "local"       # 默认操作者名称

    # ---- 运行时 ----
    host: str = "127.0.0.1"
    port: int = int(os.environ.get("RAG_PORT", "8000"))

    def __post_init__(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.meta_db_path = self.meta_db_path or (self.data_root / "meta.db")
        self.chroma_dir = self.chroma_dir or (self.data_root / "chroma")
        self.vectors_dir = self.vectors_dir or (self.data_root / "vectors")
        self.files_dir = self.files_dir or (self.data_root / "files")
        self.web_dir = self.web_dir or (BACKEND_DIR / "web")
        for d in (self.data_root, self.chroma_dir, self.vectors_dir, self.files_dir, self.web_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save(self, path: Path | None = None) -> None:
        path = path or (self.data_root / "config.json")
        data = {
            "chat_model": self.chat_model,
            "embed_model": self.embed_model,
            "chunk_max_chars": self.chunk_max_chars,
            "chunk_overlap_chars": self.chunk_overlap_chars,
            "top_k": self.top_k,
            "score_threshold": self.score_threshold,
            "route_min_sim": self.route_min_sim,
            "temperature": self.temperature,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_user_overrides(self) -> None:
        """若 data/config.json 存在则覆盖默认参数(用户可手动编辑)。"""
        cfg = self.data_root / "config.json"
        if not cfg.exists():
            return
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            return
        for k, v in data.items():
            if hasattr(self, k) and k not in ("host", "port", "data_root"):
                setattr(self, k, v)


settings = Settings()
settings.load_user_overrides()
