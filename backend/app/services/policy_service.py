"""【增强A】制度版本与生效期管理。

面向中小企业内部制度文档的核心语义:
- 每份制度有 文件编号(doc_no)、生效日期(effective_date)、失效日期(expiry_date)、
  状态(doc_status)、发布部门(issuer)、适用部门(dept_scope)、替代关系(supersedes/superseded_by);
- 检索时**排除已废止/未生效版本**,避免拿旧制度回答新问题;
- 冲突检测据此区分两类:
    · 版本差异(version_conflict):同一制度的新旧版本说法不同 → 应以现行版为准;
    · 真冲突(genuine_conflict):不同制度/不同来源的规定互相矛盾 → 需并列告警。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 状态常量
ST_EFFECTIVE = "effective"
ST_SUPERSEDED = "superseded"
ST_DRAFT = "draft"
ST_EXPIRED = "expired"
ST_SCHEDULED = "scheduled"

STATE_LABEL = {
    ST_EFFECTIVE: "现行有效",
    ST_SUPERSEDED: "已被替代",
    ST_DRAFT: "草案",
    ST_EXPIRED: "已失效",
    ST_SCHEDULED: "尚未生效",
}


def parse_date(value: str | None) -> date | None:
    """解析 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD;失败返回 None。"""
    if not value:
        return None
    s = str(value).strip().replace("/", "-").replace(".", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def doc_state(doc: dict, today: date | None = None) -> str:
    """计算制度文档的当前状态(综合状态字段与生效/失效日期)。"""
    today = today or date.today()
    status = (doc.get("doc_status") or "").strip().lower()
    if status == ST_SUPERSEDED:
        return ST_SUPERSEDED
    if status == ST_DRAFT:
        return ST_DRAFT
    if status == ST_EXPIRED:
        return ST_EXPIRED

    eff = parse_date(doc.get("effective_date"))
    exp = parse_date(doc.get("expiry_date"))
    if eff and today < eff:
        return ST_SCHEDULED
    if exp and today > exp:
        return ST_EXPIRED
    return ST_EFFECTIVE


def is_current(doc: dict, today: date | None = None) -> bool:
    """是否为「现行有效」版本(应参与检索回答)。"""
    return doc_state(doc, today) == ST_EFFECTIVE


def days_to_expiry(doc: dict, today: date | None = None) -> int | None:
    today = today or date.today()
    exp = parse_date(doc.get("expiry_date"))
    if not exp:
        return None
    return (exp - today).days


def enrich(doc: dict, today: date | None = None) -> dict:
    """补充状态字段(不落库),便于前端与接口展示。"""
    st = doc_state(doc, today)
    d = dict(doc)
    d["state"] = st
    d["state_label"] = STATE_LABEL.get(st, st)
    d["is_current"] = st == ST_EFFECTIVE
    d["days_to_expiry"] = days_to_expiry(doc, today)
    return d


def enrich_many(docs: list[dict], today: date | None = None) -> list[dict]:
    return [enrich(d, today) for d in docs]


# ---------------------------------------------------------------- 可见性过滤
def filter_retrievable(docs_meta: dict[str, dict], *, exclude_non_current: bool | None = None) -> set[str]:
    """给定 {doc_id: 元数据},返回允许参与检索回答的 doc_id 集合。"""
    from ..core.config import settings

    if exclude_non_current is None:
        exclude_non_current = settings.exclude_expired_docs
    if not exclude_non_current:
        return set(docs_meta.keys())
    return {did for did, meta in docs_meta.items() if is_current(meta)}


# ---------------------------------------------------------------- 版本链
def version_key(doc: dict) -> tuple:
    """版本排序键:生效日期优先,其次更新时间。"""
    eff = parse_date(doc.get("effective_date"))
    return (eff or date.min, str(doc.get("updated_at") or ""))


def build_version_chain(docs: list[dict]) -> list[dict]:
    """把同一制度(同文件编号或互有替代关系)的文档按版本从旧到新排序。"""
    return sorted(docs, key=version_key)


def same_policy(a: dict, b: dict) -> bool:
    """判断两份文档是否属于同一制度的不同版本。

    依据:文件编号相同 / 互相声明替代关系 / 标题相同且编号家族一致。
    """
    no_a = (a.get("doc_no") or "").strip()
    no_b = (b.get("doc_no") or "").strip()
    if no_a and no_b and no_a == no_b:
        return True
    if no_a and no_b and (a.get("supersedes") == no_b or b.get("supersedes") == no_a):
        return True
    if no_a and b.get("superseded_by") == no_a:
        return True
    if no_b and a.get("superseded_by") == no_b:
        return True
    title_a = (a.get("title") or a.get("filename") or "").strip()
    title_b = (b.get("title") or b.get("filename") or "").strip()
    if title_a and title_a == title_b:
        return True
    # 编号家族:如 HR-2023-001 与 HR-2025-001(前缀+尾号一致)
    if no_a and no_b and no_a != no_b:
        pa, _, sa = no_a.rpartition("-")
        pb, _, sb = no_b.rpartition("-")
        if pa and pb and sa == sb and pa.split("-")[0] == pb.split("-")[0]:
            return True
    return False


def newer(a: dict, b: dict) -> dict:
    """返回两者中「更应作为现行依据」的一份。"""
    st_a, st_b = doc_state(a), doc_state(b)
    if st_a == ST_EFFECTIVE and st_b != ST_EFFECTIVE:
        return a
    if st_b == ST_EFFECTIVE and st_a != ST_EFFECTIVE:
        return b
    return a if version_key(a) >= version_key(b) else b


def classify_conflict(a_meta: dict, b_meta: dict) -> dict:
    """把一对冲突判定为「版本差异」或「真冲突」,并给出建议依据版本。"""
    version_related = same_policy(a_meta, b_meta)
    winner = newer(a_meta, b_meta) if version_related else None
    return {
        "kind": "version_conflict" if version_related else "genuine_conflict",
        "kind_label": "制度版本差异" if version_related else "跨来源规定冲突",
        "preferred": (
            {
                "doc_id": winner.get("doc_id"),
                "doc_no": winner.get("doc_no") or "",
                "title": winner.get("title") or winner.get("filename") or "",
                "effective_date": winner.get("effective_date") or "",
                "state_label": STATE_LABEL.get(doc_state(winner), ""),
            }
            if winner else None
        ),
    }


# ---------------------------------------------------------------- 到期提醒
def expiring_docs(all_docs: list[tuple[dict, str]], days: int | None = None) -> list[dict]:
    """列出即将到期的现行制度。入参 [(document_row, kb_name)]。"""
    from ..core.config import settings

    days = days or settings.expiring_soon_days
    out = []
    for doc, kb_name in all_docs:
        if doc_state(doc) != ST_EFFECTIVE:
            continue
        d = days_to_expiry(doc)
        if d is not None and 0 <= d <= days:
            item = enrich(doc)
            item["kb_name"] = kb_name
            out.append(item)
    return sorted(out, key=lambda x: x.get("days_to_expiry", 0))
