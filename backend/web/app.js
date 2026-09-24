/* 内嵌 Web 界面逻辑:无构建、原生 fetch + SSE。 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let KB_CACHE = [];
let currentKbId = null;
let classifySession = null;
let qaBusy = false;

/* ---------------- 【增强B】当前身份 ---------------- */
const DEFAULT_ACTOR = { name: "local", role: "admin", dept: "" };
let ACTOR = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem("rag_actor") || "null");
    return saved && saved.role ? { ...DEFAULT_ACTOR, ...saved } : { ...DEFAULT_ACTOR };
  } catch (_) { return { ...DEFAULT_ACTOR }; }
})();

function actorHeaders(extra = {}) {
  // HTTP 头只允许 latin-1:中文部门/名称需百分号编码(后端会自动解码)
  return {
    "X-Actor-Name": encodeURIComponent(ACTOR.name || "local"),
    "X-Actor-Role": encodeURIComponent(ACTOR.role || "admin"),
    "X-Actor-Dept": encodeURIComponent(ACTOR.dept || ""),
    ...extra,
  };
}

function syncActorUI() {
  const r = $("#actorRole"), d = $("#actorDept");
  if (r) r.value = ACTOR.role || "";
  if (d) d.value = ACTOR.dept || "";
  const hint = $("#actorHint");
  if (hint) {
    hint.textContent = ACTOR.role === "admin"
      ? "管理员:可见全部知识库"
      : `非管理员:仅可见「全员」及允许「${ACTOR.dept || "未指定部门"}」的库`;
  }
}

function saveActor() {
  ACTOR = {
    name: ACTOR.name || "local",
    role: ($("#actorRole").value || "staff").trim().toLowerCase(),
    dept: ($("#actorDept").value || "").trim(),
  };
  localStorage.setItem("rag_actor", JSON.stringify(ACTOR));
  syncActorUI();
  refreshKbSelect();
  refreshKbs();
  const l = $("#status-line");
  if (l) l.dataset.actor = JSON.stringify(ACTOR);
}

/* ---------------- API 工具 ---------------- */
async function api(path, opts = {}) {
  const merged = { ...opts, headers: actorHeaders(opts.headers || {}) };
  const res = await fetch(path, merged);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}
function postJSON(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ---------------- 导航 ---------------- */
function switchView(name) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "kbs") refreshKbs();
  if (name === "system") refreshSystem();
  if (name === "audit") refreshAudit();
  if (name === "chat") { refreshKbSelect(); if (KB_CACHE.length && !$("#chatLog").children.length) {} }
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));

/* ---------------- 状态栏 ---------------- */
async function refreshStatusLine() {
  try {
    const s = await api("/api/system/status");
    const line = $("#status-line");
    const ok = s.ollama_connected && s.chat_ok && s.embed_ok;
    line.className = `sidebar-foot ${ok ? "ok" : "bad"}`;
    line.innerHTML = ok
      ? `<b>● 系统就绪</b><br>${s.kbs} 个知识库 · ${s.docs} 篇文档 · ${s.chunks} 个分块<br>${s.chat_model} / ${s.embed_model}`
      : `<b>⚠ 模型未就绪</b><br>Ollama 连接: ${s.ollama_connected}<br>生成模型: ${s.chat_ok ? "OK" : "缺失"}(ollama pull ${s.chat_model})<br>嵌入模型: ${s.embed_ok ? "OK" : "缺失"}(ollama pull ${s.embed_model})`;
  } catch (e) {
    $("#status-line").innerHTML = `<b class="bad">⚠ 后端不可达</b><br>${esc(e.message)}`;
  }
}

/* ================= 问答 ================= */
let chatHistory = [];

async function refreshKbSelect() {
  try {
    KB_CACHE = (await api("/api/kb")).kbs;
  } catch (_) { KB_CACHE = []; }
  const sel = $("#manualKbSelect");
  sel.innerHTML = '<option value="">-- 选择知识库(兜底) --</option>' +
    KB_CACHE.map((k) => `<option value="${k.id}">${esc(k.name)} (${k.doc_count}篇)</option>`).join("");
}

function setModeManual(manual) {
  $("#manualKbSelect").disabled = !manual;
}

$$('input[name="qaMode"]').forEach((r) =>
  r.addEventListener("change", () => setModeManual(r.value === "manual"))
);

function addMsg(role, html, cls = "") {
  $("#chatEmpty")?.remove();
  const div = document.createElement("div");
  div.className = `msg ${role} ${cls}`;
  div.innerHTML = `<div class="bubble">${html}</div>`;
  $("#chatLog").appendChild(div);
  return div.querySelector(".bubble");
}
function addUserMsg(text) { return addMsg("user", esc(text)); }

function renderCitations(bubble, citations) {
  const map = Object.entries(citations || {});
  if (!map.length) return;
  const box = document.createElement("div");
  box.className = "citations";
  box.innerHTML = `<b>📎 引用来源</b>` + map.map(([k, c]) =>
    `<div>[${k}] ${esc(c.filename)}${c.heading_path ? ` — ${esc(c.heading_path)}` : ""}${c.page > 0 ? ` (第${c.page}页)` : ""}<span class="dim"> · doc:${esc(c.doc_id.slice(0, 8))}</span></div>`
  ).join("");
  bubble.appendChild(box);
}

async function ask() {
  if (qaBusy) return;
  const input = $("#questionInput");
  const question = input.value.trim();
  if (!question) return;
  const mode = document.querySelector('input[name="qaMode"]:checked').value;
  const manualKb = $("#manualKbSelect").value;

  input.value = "";
  addUserMsg(question);
  const bubble = addMsg("assistant", '<span class="typing">思考中…</span>');
  qaBusy = true;
  $("#sendBtn").disabled = true;
  $("#routeHint").textContent = "";
  const stopBtn = $("#stopBtn");
  stopBtn.classList.remove("hidden");

  // 超时与中断控制:总超时 + 空闲看门狗(长时间无数据视为卡死)
  const controller = new AbortController();
  const TOTAL_TIMEOUT = 10 * 60 * 1000;   // 10 分钟总上限(本地大模型冷加载可能很慢)
  const IDLE_TIMEOUT = 90 * 1000;         // 90 秒无任何增量视为卡死
  let lastDataAt = Date.now();
  let timedOut = false;
  const totalTimer = setTimeout(() => { timedOut = true; controller.abort(); }, TOTAL_TIMEOUT);
  const idleTimer = setInterval(() => {
    if (!timedOut && Date.now() - lastDataAt > IDLE_TIMEOUT) {
      timedOut = true;
      controller.abort();
    }
  }, 20000);
  const abortNow = () => { timedOut = true; controller.abort(); };
  stopBtn.onclick = abortNow;

  try {
    const res = await fetch("/api/qa/ask", {
      method: "POST",
      headers: actorHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question, mode, kb_id: mode === "manual" ? manualKb : undefined }),
      signal: controller.signal,
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let hits = [];
    let citations = {};
    let answerParts = [];

    while (true) {
      const { value, done: streamEnd } = await reader.read();
      if (streamEnd) break;
      lastDataAt = Date.now();
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const evLine = block.split("\n").find((l) => l.startsWith("event: "));
        const dataLine = block.split("\n").find((l) => l.startsWith("data: "));
        if (!evLine || !dataLine) continue;
        const ev = evLine.slice(7).trim();
        const data = JSON.parse(dataLine.slice(6));
        handleQaEvent(ev, data, bubble, (h) => { hits = h; }, (c) => { citations = c; }, (t) => { answerParts.push(t); });
      }
    }
    if (!answerParts.length && !bubble.textContent.includes("未找到")) {
      // 事件流正常结束但无文本:可能已给出 no_context/error,不再覆盖
    }
  } catch (e) {
    const aborted = e.name === "AbortError";
    bubble.parentElement.classList.add(aborted ? "noctx" : "error");
    bubble.textContent = aborted
      ? (timedOut ? "⚠ 已停止/超时中断(模型加载或生成过慢)。可稍后重试;若经常超时,建议减小文档分块或更换更快模型。" : "⚠ 已手动停止。")
      : `请求失败: ${e.message}`;
  } finally {
    clearTimeout(totalTimer);
    clearInterval(idleTimer);
    qaBusy = false;
    $("#sendBtn").disabled = false;
    stopBtn.classList.add("hidden");
    scrollChat();
  }
}

function handleQaEvent(ev, data, bubble, setHits, setCites, pushDelta) {
  switch (ev) {
    case "route_info": {
      const hint = $("#routeHint");
      if (data.auto_kb_id) hint.textContent = `已自动路由到知识库「${data.auto_kb_name}」`;
      else hint.textContent = "自动路由未匹配到合适知识库 → 将提示人工指定(兜底)";
      break;
    }
    case "hits":
      bubble.innerHTML = `<span class="dim">已检索到 ${data.hits.length} 段相关内容,正在生成回答…</span>`;
      setHits(data.hits);
      break;
    case "conflict": {
      // 【创新点3 + 增强A】冲突告警面板(区分「制度版本差异」与「跨来源冲突」)
      bubble.innerHTML = `<span class="dim">⚠ 检测到信息冲突,正在生成对比说明…</span>`;
      const panel = document.createElement("div");
      panel.className = "conflict-panel";
      const items = (data.conflicts || []).map((c, i) => {
        const isVer = c.kind === "version_conflict";
        const pref = c.preferred || {};
        const side = (side, tagCls) => `
          <div class="conflict-side">
            <span class="kb-tag ${tagCls}">${esc(side.kb_name)}</span>
            <span>${esc(side.claim || side.text || "")}</span>
            <span class="dim"> · ${esc(side.doc_no || "无编号")}${side.effective_date ? " · 生效 " + esc(side.effective_date) : ""}${side.state ? " · " + esc(side.state) : ""} · 来源:${esc(side.filename || "-")}</span>
          </div>`;
        return `<div class="conflict-item">
          <div class="conflict-point">
            <span class="kind-badge ${isVer ? "ver" : "genuine"}">${esc(c.kind_label || (isVer ? "制度版本差异" : "跨来源规定冲突"))}</span>
            冲突 ${i + 1}:${esc(c.point || "同一事实说法不一致")}
          </div>
          ${side(c.a, "")}
          ${side(c.b, "alt")}
          ${isVer && pref.title ? `<div class="dim">✔ 建议依据现行版本:${esc(pref.title)} ${esc(pref.doc_no || "")}${pref.effective_date ? "(生效 " + esc(pref.effective_date) + ")" : ""}</div>` : ""}
        </div>`;
      }).join("");
      panel.innerHTML = `<b>⚠ ${data.version_conflicts ? "检测到制度版本差异" : "跨知识库信息冲突告警"}</b>
        <div class="dim">在 ${data.kb_count || 0} 个知识库中比对 ${data.pairs_considered || 0} 组片段;版本差异 ${data.version_conflicts ?? 0} 处、跨来源冲突 ${data.genuine_conflicts ?? 0} 处。</div>
        ${items}`;
      const wrap = bubble.parentElement;
      wrap.appendChild(panel);
      scrollChat();
      break;
    }
    case "delta":
      if (bubble.textContent.includes("正在生成") || bubble.textContent.startsWith("已检索")
          || bubble.textContent.includes("检测到跨知识库")) {
        bubble.textContent = "";
        bubble.classList.remove("typing");
      }
      bubble.textContent += data.text;
      pushDelta(data.text);
      scrollChat();
      break;
    case "done": {
      bubble.textContent = data.answer;
      bubble.classList.remove("typing");
      renderCitations(bubble, data.citations);
      setCites(data.citations);
      break;
    }
    case "no_context": {
      bubble.parentElement.classList.add("noctx");
      bubble.innerHTML = `<b>⚠ ${esc(data.reason)}</b>`;
      if (data.hits && data.hits.length) {
        const frag = document.createElement("div");
        frag.className = "citations";
        frag.innerHTML = "<b>最接近的片段(低于采纳阈值):</b>" + data.hits.map((h) =>
          `<div>${esc(h.text)}<span class="dim"> · 相似度 ${h.score} · ${esc(h.filename)}</span></div>`).join("");
        bubble.appendChild(frag);
      }
      break;
    }
    case "denied": {
      bubble.parentElement.classList.add("error");
      bubble.innerHTML = `<b>⛔ 权限不足</b><div>${esc(data.message)}</div>
        <div class="dim">提示:可在左下角切换身份(角色/部门)后重试。</div>`;
      break;
    }
    case "error":
      bubble.parentElement.classList.add("error");
      bubble.textContent = `错误: ${data.message}`;
      break;
  }
}

function scrollChat() {
  const log = $("#chatLog");
  log.scrollTop = log.scrollHeight;
}
$("#sendBtn").addEventListener("click", ask);
$("#questionInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
});

/* ================= 知识库管理 ================= */
async function refreshKbs() {
  try {
    const data = await api("/api/kb");
    KB_CACHE = data.kbs;
    const list = $("#kbList");
    if (!KB_CACHE.length) {
      list.innerHTML = `<div class="empty-note">还没有知识库。点击右上角「新建知识库」开始,或到「自动分类建库」批量导入混杂文档。</div>`;
      return;
    }
    list.innerHTML = `<div class="kb-grid">${KB_CACHE.map(kbCard).join("")}</div>`;
    $$(".kb-card").forEach((card) =>
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        openDocPanel(card.dataset.kbId);
      })
    );
    // 操作按钮
    $$(".kb-card .open-btn").forEach((b) => b.addEventListener("click", () => openDocPanel(b.dataset.kbId)));
    $$(".kb-card .del-btn").forEach((b) => b.addEventListener("click", () => deleteKb(b.dataset.kbId)));
    $$(".kb-card .ren-btn").forEach((b) => b.addEventListener("click", () => renameKb(b.dataset.kbId)));
  } catch (e) {
    $("#kbList").innerHTML = `<div class="empty-note">加载失败: ${esc(e.message)}</div>`;
  }
}

function kbCard(k) {
  const st = k.storage || {};
  const restricted = (k.visibility || "all") === "restricted";
  return `<div class="kb-card" data-kb-id="${k.id}">
    <h3>📚 ${esc(k.name)}
      <span class="kind-badge ${restricted ? "genuine" : "ver"}">${restricted ? "受限:" + esc(k.allowed_depts || "未配置") : "全员可见"}</span>
    </h3>
    <p>${esc(k.description || "暂无描述")}</p>
    <div class="kb-meta">
      <span>${k.doc_count} 篇文档</span><span>${k.chunk_count} 个分块</span>
      ${k.doc_category ? `<span>类别:${esc(k.doc_category)}</span>` : ""}
      ${k.owner_dept ? `<span>归属:${esc(k.owner_dept)}</span>` : ""}
      ${k.has_profile ? "<span>✓ 可自动路由</span>" : ""}
      ${st.size_kb != null ? `<span>🧱 独立向量 ${st.size_kb} KB</span>` : ""}
    </div>
    ${st.path ? `<div class="storage-path" title="该库独立的 Chroma 存储目录(物理隔离)">${esc(st.path)}</div>` : ""}
    <div class="kb-actions">
      <button class="open-btn" data-kb-id="${k.id}">管理文档</button>
      <button class="ren-btn" data-kb-id="${k.id}">设置</button>
      <button class="del-btn" data-kb-id="${k.id}">删除</button>
    </div>
  </div>`;
}

async function createKb() {
  const name = prompt("知识库名称(如:人力资源制度):");
  if (!name) return;
  const desc = prompt("描述(可选):") || "";
  const category = prompt("制度类别(如 人事/财务/行政,可空):") || "";
  const owner = prompt("归属部门(可空):") || "";
  const restricted = confirm("是否限制可见范围?\n\n确定 = 仅指定部门可见(适合薪酬、绩效等敏感制度)\n取消 = 全员可见");
  let visibility = "all", allowed = "";
  if (restricted) {
    visibility = "restricted";
    allowed = prompt("允许访问的部门(多个用逗号分隔,如 人力资源部,财务部):") || "";
    if (!allowed.trim()) { alert("已取消:受限库必须指定允许部门"); return; }
  }
  try {
    await postJSON("/api/kb", {
      name, description: desc, doc_category: category, owner_dept: owner,
      visibility, allowed_depts: allowed,
    });
    await refreshKbs();
  } catch (e) { alert(`创建失败: ${e.message}`); }
}
$("#createKbBtn").addEventListener("click", createKb);

async function renameKb(kbId) {
  const kb = KB_CACHE.find((k) => k.id === kbId);
  if (!kb) return;
  const name = prompt("名称:", kb.name);
  if (!name) return;
  const desc = prompt("描述:", kb.description);
  if (desc === null) return;
  const category = prompt("制度类别:", kb.doc_category || "") ?? kb.doc_category;
  const owner = prompt("归属部门:", kb.owner_dept || "") ?? kb.owner_dept;
  const cur = (kb.visibility || "all") === "restricted" ? "restricted" : "all";
  const visInput = prompt("可见范围:输入 all=全员 / restricted=指定部门", cur) || cur;
  const visibility = visInput.trim().toLowerCase() === "restricted" ? "restricted" : "all";
  let allowed = kb.allowed_depts || "";
  if (visibility === "restricted") {
    allowed = prompt("允许访问的部门(逗号分隔):", allowed) ?? allowed;
    if (!allowed.trim()) { alert("受限库必须指定允许部门,已取消"); return; }
  }
  try {
    await api(`/api/kb/${kbId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, description: desc, visibility, allowed_depts: allowed,
        doc_category: category, owner_dept: owner,
      }),
    });
    await refreshKbs();
    if (currentKbId === kbId) loadDocs(kbId);
  } catch (e) { alert(`保存失败: ${e.message}`); }
}

async function deleteKb(kbId) {
  const kb = KB_CACHE.find((k) => k.id === kbId);
  if (!confirm(`确认删除知识库「${kb.name}」?\n将删除其中全部 ${kb.doc_count} 篇文档的向量、元数据与归档文件,不可恢复。`)) return;
  try {
    await api(`/api/kb/${kbId}`, { method: "DELETE" });
    if (currentKbId === kbId) closeDocPanel();
    await refreshKbs();
  } catch (e) { alert(`删除失败: ${e.message}`); }
}

async function reconcileAll() {
  const btn = $("#reconcileBtn");
  btn.disabled = true;
  $("#reconcileResult").textContent = "对账中…";
  try {
    const r = await postJSON("/api/kb/reconcile", {});
    const rows = Object.entries(r.report).map(([kid, rep]) =>
      `${kid}: 清理孤儿分块 ${rep.orphan_chunks_removed} 个(库内 ${rep.docs_in_db} 篇, 向量侧 ${rep.docs_in_vector} 篇)`
    ).join("\n");
    $("#reconcileResult").textContent = rows ? rows : "✅ 全部一致,无孤儿向量";
    await refreshKbs();
  } catch (e) { $("#reconcileResult").textContent = `对账失败: ${e.message}`; }
  finally { btn.disabled = false; }
}
$("#reconcileBtn").addEventListener("click", reconcileAll);

/* ---- 文档管理抽屉 ---- */
function openDocPanel(kbId) {
  currentKbId = kbId;
  const kb = KB_CACHE.find((k) => k.id === kbId);
  $("#docPanelTitle").textContent = `📚 ${kb.name} — 文档管理`;
  $("#docPanel").classList.remove("hidden");
  $("#docPanel").scrollIntoView({ behavior: "smooth" });
  loadDocs(kbId);
}
function closeDocPanel() {
  currentKbId = null;
  $("#docPanel").classList.add("hidden");
}
$("#docBackBtn").addEventListener("click", closeDocPanel);

async function loadDocs(kbId) {
  try {
    const { docs } = await api(`/api/doc/list/${kbId}`);
    const cnt = await api(`/api/doc/count/${kbId}`);
    $("#docPanelStats").textContent = `${docs.length} 篇文档 · 向量 ${cnt.chunk_count} 块`;
    const box = $("#docList");
    if (!docs.length) { box.innerHTML = `<div class="empty-note">该知识库还没有文档,请在上方选择文件后点击「上传文档入库」。</div>`; return; }
    const stateBadge = (d) => {
      const cls = d.state === "effective" ? "ver" : "genuine";
      return `<span class="kind-badge ${cls}" title="制度状态">${esc(d.state_label || d.state || "")}</span>`;
    };
    box.innerHTML = docs.map((d) => `
      <div class="doc-item" data-doc-id="${d.doc_id}">
        <span class="name">📄 ${esc(d.filename)} ${stateBadge(d)}</span>
        <span class="meta">${esc(d.doc_no || "无编号")}${d.effective_date ? " · 生效 " + esc(d.effective_date) : ""}${d.expiry_date ? " · 失效 " + esc(d.expiry_date) : ""}${d.superseded_by ? " · 被 " + esc(d.superseded_by) + " 替代" : ""}</span>
        <span class="meta">${d.chunk_count} 块 · v${d.version}</span>
        <button class="meta-btn" data-kb="${kbId}" data-doc="${d.doc_id}" data-name="${esc(d.filename)}">制度属性</button>
        <button class="ver-btn" data-kb="${kbId}" data-doc="${d.doc_id}" data-name="${esc(d.filename)}">版本链</button>
        <button class="dl-btn" data-kb="${kbId}" data-doc="${d.doc_id}" data-name="${esc(d.filename)}">原文</button>
        <button class="upd-btn" data-kb="${kbId}" data-doc="${d.doc_id}" data-name="${esc(d.filename)}">更新</button>
        <button class="del-btn" data-kb="${kbId}" data-doc="${d.doc_id}" data-name="${esc(d.filename)}">删除</button>
      </div>`).join("");
    $$("#docList .dl-btn").forEach((b) => b.addEventListener("click", () => window.open(`/api/doc/file/${b.dataset.kb}/${b.dataset.doc}`, "_blank")));
    $$("#docList .del-btn").forEach((b) => b.addEventListener("click", () => deleteDoc(b.dataset.kb, b.dataset.doc, b.dataset.name)));
    $$("#docList .upd-btn").forEach((b) => b.addEventListener("click", () => updateDocFlow(b.dataset.kb, b.dataset.doc, b.dataset.name)));
    $$("#docList .meta-btn").forEach((b) => b.addEventListener("click", () => editDocMeta(b.dataset.kb, b.dataset.doc)));
    $$("#docList .ver-btn").forEach((b) => b.addEventListener("click", () => showVersionChain(b.dataset.kb, b.dataset.doc, b.dataset.name)));
  } catch (e) { $("#docList").innerHTML = `<div class="empty-note">加载失败: ${esc(e.message)}</div>`; }
}

/* 【增强A】编辑制度属性 */
async function editDocMeta(kbId, docId) {
  let doc;
  try {
    const list = await api(`/api/doc/list/${kbId}`);
    doc = (list.docs || []).find((d) => d.doc_id === docId);
  } catch (_) {}
  if (!doc) { alert("未找到该文档"); return; }
  const doc_no = prompt("文件编号(如 HR-2025-001):", doc.doc_no || "");
  if (doc_no === null) return;
  const title = prompt("制度名称:", doc.title || doc.filename) ?? doc.title;
  const effective_date = prompt("生效日期(YYYY-MM-DD,可空):", doc.effective_date || "") ?? doc.effective_date;
  const expiry_date = prompt("失效日期(YYYY-MM-DD,可空;用于到期提醒):", doc.expiry_date || "") ?? doc.expiry_date;
  const doc_status = prompt("状态:effective=现行 / superseded=已被替代 / draft=草案 / expired=已失效",
    doc.doc_status || "effective") ?? doc.doc_status;
  const issuer = prompt("发布部门:", doc.issuer || "") ?? doc.issuer;
  const dept_scope = prompt("适用部门(逗号分隔,空=全员):", doc.dept_scope || "") ?? doc.dept_scope;
  const supersedes = prompt("本文件替代的文件编号(可空;填写后会自动将旧版标记为已废止):",
    doc.supersedes || "") ?? doc.supersedes;
  try {
    const r = await api(`/api/doc/meta/${kbId}/${docId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_no, title, effective_date, expiry_date, doc_status, issuer, dept_scope, supersedes }),
    });
    const links = r.doc.supersede_links || [];
    alert(`已保存制度属性。当前状态:${r.doc.state_label || r.doc.state}` +
      (links.length ? `\n\n已自动标记 ${links.length} 份旧版为「已被替代」:\n` + links.map((l) => "· " + l.filename).join("\n") : ""));
    await loadDocs(kbId);
  } catch (e) { alert(`保存失败: ${e.message}`); }
}

/* 【增强A】查看版本链 */
async function showVersionChain(kbId, docId, name) {
  try {
    const r = await api(`/api/doc/versions/${kbId}?doc_id=${encodeURIComponent(docId)}`);
    if (!r.chain.length) { alert(`「${name}」未找到关联版本(可通过「制度属性」填写文件编号与替代关系来建立版本链)`); return; }
    const lines = r.chain.map((d, i) =>
      `${i + 1}. ${d.filename}\n   编号:${d.doc_no || "无"}  状态:${d.state_label}\n   生效:${d.effective_date || "未填写"}  失效:${d.expiry_date || "未填写"}` +
      (d.supersedes ? `\n   替代:${d.supersedes}` : "") + (d.superseded_by ? `\n   被替代:${d.superseded_by}` : ""));
    alert(`「${name}」版本链(从旧到新,共 ${r.chain.length} 个版本):\n\n` + lines.join("\n\n"));
  } catch (e) { alert(`查询版本链失败: ${e.message}`); }
}

async function uploadDocs() {
  if (!currentKbId) return;
  const files = $("#docFileInput").files;
  if (!files.length) { alert("请先选择文件"); return; }
  const btn = $("#uploadDocBtn");
  btn.disabled = true;
  btn.textContent = "上传中…";
  const results = [];
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api(`/api/doc/add/${currentKbId}`, { method: "POST", body: fd });
      results.push(`✅ ${f.name} → ${r.doc.chunk_count} 块`);
    } catch (e) { results.push(`❌ ${f.name}: ${e.message}`); }
  }
  btn.disabled = false;
  btn.textContent = "上传文档入库";
  alert(results.join("\n"));
  $("#docFileInput").value = "";
  await loadDocs(currentKbId);
  await refreshKbs();
}
$("#uploadDocBtn").addEventListener("click", uploadDocs);

async function deleteDoc(kbId, docId, name) {
  if (!confirm(`删除文档「${name}」?将清除其全部 ${""} 向量与元数据(可验证无残留)。`)) return;
  try {
    const r = await api(`/api/doc/${kbId}/${docId}`, { method: "DELETE" });
    alert(`已删除,清除向量分块 ${r.deleted_chunks} 个`);
    await loadDocs(kbId);
    await refreshKbs();
  } catch (e) { alert(`删除失败: ${e.message}`); }
}

function updateDocFlow(kbId, docId, name) {
  const input = document.createElement("input");
  input.type = "file";
  input.onchange = async () => {
    if (!input.files.length) return;
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      const r = await api(`/api/doc/update/${kbId}/${docId}`, { method: "PUT", body: fd });
      const st = r.doc.update_stats || {};
      alert(
        `已更新为 v${r.doc.version},分块 ${r.doc.chunk_count} 个\n\n` +
        `【增量更新】复用未变更块向量 ${st.reused_chunks ?? "-"} 个,仅重新嵌入 ${st.embedded_chunks ?? "-"} 个\n` +
        `复用率 ${st.reuse_ratio != null ? (st.reuse_ratio * 100).toFixed(1) + "%" : "-"}\n` +
        `实际耗时 ${st.actual_ms ?? "-"} ms;若整篇重嵌入约需 ${st.estimated_full_reembed_ms ?? "-"} ms` +
        (st.time_saved_ms ? `(节省约 ${(st.time_saved_ms / 1000).toFixed(1)} s)` : "") +
        `\n旧向量清除 ${st.removed_old_vectors ?? "-"} 条,零残留`
      );
      await loadDocs(kbId);
    } catch (e) { alert(`更新失败: ${e.message}`); }
  };
  input.click();
}

/* 预览切分 */
async function previewDoc() {
  const files = $("#docFileInput").files;
  if (!files.length) { alert("请先选择文件"); return; }
  const f = files[0];
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/doc/preview", { method: "POST", body: fd });
    const box = $("#previewBox");
    box.classList.remove("hidden");
    box.innerHTML = `<b>${esc(r.filename)}</b> → 共 ${r.chunk_count} 块 / ${r.total_chars} 字符(预览前 ${r.chunks.length} 块):<br><pre>${esc(r.chunks.map((c, i) => `[${i}] ${c.heading_path ? "(" + c.heading_path + ") " : ""}${c.text.slice(0, 220)}${c.text.length > 220 ? "…" : ""}`).join("\n\n"))}</pre>`;
  } catch (e) { alert(`预览失败: ${e.message}`); }
}
$("#previewBtn").addEventListener("click", previewDoc);

/* ================= 自动分类建库 ================= */
async function runClassify() {
  const files = $("#classifyFileInput").files;
  if (!files.length) { alert("请先选择文件"); return; }
  const btn = $("#classifyRunBtn");
  btn.disabled = true;
  btn.textContent = "解析聚类中…";
  $("#classifyProgress").textContent = "";
  try {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const r = await api("/api/classify/prepare", { method: "POST", body: fd });
    classifySession = r.session_id;
    $("#classifyProgress").textContent = `共 ${r.total_files} 个文件,成功聚类 ${r.clustered_files} 个${r.skipped.length ? `,跳过解析失败 ${r.skipped.length} 个(${esc(r.skipped.join("、"))})` : ""}`;
    renderClusters(r.clusters);
    $("#clusterBox").classList.remove("hidden");
    // 自动建议库名
    try {
      const sug = await postJSON("/api/classify/suggest", { session_id: classifySession });
      const byCid = Object.fromEntries(sug.suggestions.map((s) => [s.cluster_id, s]));
      $$(".cluster-card").forEach((card) => {
        const s = byCid[card.dataset.cid];
        if (s) {
          card.querySelector(".name-input").value = s.suggested_name;
          card.querySelector(".desc-input").value = s.suggested_desc;
        }
      });
    } catch (_) {}
  } catch (e) {
    $("#classifyProgress").textContent = `自动分类失败: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "开始自动分类";
  }
}
$("#classifyRunBtn").addEventListener("click", runClassify);

function renderClusters(clusters) {
  if (!clusters.length) {
    $("#clusterList").innerHTML = `<div class="empty-note">未能聚出类别(文档过少或差异过大),请尝试手动建库。</div>`;
    return;
  }
  $("#clusterList").innerHTML = clusters.map((c) => `
    <div class="cluster-card" data-cid="${c.cluster_id}">
      <div class="cluster-top">
        <label><input type="checkbox" class="confirm-chk" checked> 建库</label>
        <input type="text" class="name-input" placeholder="库名(可修改)">
        <span class="dim">${c.size} 篇文档</span>
      </div>
      <input type="text" class="desc-input" placeholder="描述(可修改)">
      <div class="files">📄 ${esc(c.filenames.join("、"))}</div>
      <div class="sample">片段: ${esc(c.sample_text.slice(0, 200))}</div>
    </div>`).join("");
}
$("#suggestNamesBtn").addEventListener("click", async () => {
  if (!classifySession) return;
  $("#suggestNamesBtn").disabled = true;
  try {
    const sug = await postJSON("/api/classify/suggest", { session_id: classifySession });
    const byCid = Object.fromEntries(sug.suggestions.map((s) => [s.cluster_id, s]));
    $$(".cluster-card").forEach((card) => {
      const s = byCid[card.dataset.cid];
      if (s) {
        card.querySelector(".name-input").value = s.suggested_name;
        card.querySelector(".desc-input").value = s.suggested_desc;
      }
    });
  } catch (e) { alert(`生成建议失败: ${e.message}`); }
  finally { $("#suggestNamesBtn").disabled = false; }
});

$("#confirmBuildBtn").addEventListener("click", async () => {
  if (!classifySession) return;
  const clusters = $$(".cluster-card").map((card) => ({
    cluster_id: card.dataset.cid,
    confirmed: card.querySelector(".confirm-chk").checked,
    name: card.querySelector(".name-input").value,
    description: card.querySelector(".desc-input").value,
  }));
  const btn = $("#confirmBuildBtn");
  btn.disabled = true;
  btn.textContent = "建库入库中…(文档较多时较慢)";
  try {
    const r = await postJSON("/api/classify/confirm", { session_id: classifySession, clusters });
    classifySession = null;
    const box = $("#buildResult");
    box.classList.remove("hidden");
    const created = r.created.map((c) =>
      `<li><b>✅ ${esc(c.name)}</b> — 导入 ${c.imported.length} 篇${c.failed.length ? `;失败 ${c.failed.map((f) => esc(f.filename)).join("、")}` : ""}</li>`
    ).join("");
    const errs = r.errors.map((e) => `<li class="fail">❌ ${esc(e.name)}: ${esc(e.message)}</li>`).join("");
    const pending = r.pending.length ? `<li class="fail">⏳ 待人工处理(未确认): ${esc(r.pending.join("、"))}</li>` : "";
    box.innerHTML = `<h4>建库结果</h4><ul>${created}${errs}${pending || ""}</ul>`;
    $("#classifyFileInput").value = "";
    await refreshKbs();
    await refreshStatusLine();
  } catch (e) {
    alert(`建库失败: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "③ 确认并建库入库";
  }
});

/* ================= 系统状态 ================= */
async function refreshSystem() {
  try {
    const s = await api("/api/system/status");
    const iso = await api("/api/system/isolation").catch(() => null);
    const up = s.updates || {};
    const card = (label, val, ok, extra = "") =>
      `<div class="stat-card ${ok ? "ok" : "bad"}"><b>${val}</b><span>${label}</span>${extra}</div>`;
    $("#sysStatus").innerHTML =
      card("Ollama 服务", s.ollama_connected ? "已连接" : "未连接", s.ollama_connected) +
      card("生成模型", s.chat_ok ? "✓ 就绪" : "缺失", s.chat_ok, `<span>${s.chat_model}</span>`) +
      card("嵌入模型", s.embed_ok ? "✓ 就绪" : "缺失", s.embed_ok, `<span>${s.embed_model}</span>`) +
      card("知识库数量", s.kbs, s.kbs > 0) +
      card("文档总数", s.docs, true) +
      card("向量分块总数", s.chunks, true) +
      card("向量隔离实例", iso ? iso.store_count : "-", !!iso && iso.store_count === s.kbs,
           `<span>独立存储目录 ${iso ? iso.distinct_paths : "-"} 个</span>`) +
      card("增量更新复用率", up.chunks_total ? ((up.reuse_ratio || 0) * 100).toFixed(1) + "%" : "—",
           true, `<span>累计 ${up.updates || 0} 次更新 / 节省 ${((up.saved_ms || 0) / 1000).toFixed(1)}s</span>`) +
      card("跨库冲突检测", s.config.enable_conflict_check ? "已启用" : "已停用",
           !!s.config.enable_conflict_check, `<span>最多比对 ${s.config.conflict_multi_kb_n} 库</span>`) +
      card("制度版本管理", s.policy && s.policy.exclude_expired_docs ? "已启用" : "已停用",
           !!(s.policy && s.policy.exclude_expired_docs),
           `<span>检索时${s.policy && s.policy.exclude_expired_docs ? "排除" : "包含"}已废止/未生效版本</span>`) +
      card("权限隔离", s.config.enforce_permissions ? "已启用" : "已停用",
           !!s.config.enforce_permissions,
           `<span>受限库 ${(s.policy && s.policy.restricted_kbs || []).length} 个</span>`) +
      card("审计记录", (s.audit && s.audit.total) || 0, true,
           `<span>越权拒绝 ${(s.audit && s.audit.denied) || 0} 次</span>`) +
      card("检索采纳阈值", s.config.score_threshold, true);

    // 【增强A】制度到期提醒
    const expBox = $("#expiringBox");
    if (expBox) {
      const pol = s.policy || {};
      const items = pol.expiring_items || [];
      if (items.length) {
        expBox.classList.remove("hidden");
        expBox.innerHTML = `<h4>⏰ 制度到期提醒(${pol.expiring_soon_days || 30} 天内到期 ${pol.expiring_count || items.length} 份)</h4>
          <table class="iso-table">
            <tr><th>制度</th><th>编号</th><th>所属库</th><th>失效日期</th><th>剩余天数</th></tr>
            ${items.map((d) => `<tr>
              <td>${esc(d.title || d.filename || "")}</td>
              <td class="mono">${esc(d.doc_no || "-")}</td>
              <td>${esc(d.kb_name || "-")}</td>
              <td class="mono">${esc(d.expiry_date || "-")}</td>
              <td>${d.days_to_expiry ?? "-"}</td>
            </tr>`).join("")}
          </table>`;
      } else {
        expBox.classList.remove("hidden");
        expBox.innerHTML = `<h4>⏰ 制度到期提醒</h4><div class="dim">暂无即将到期的现行制度(可在文档「制度属性」中填写失效日期以启用提醒)。</div>`;
      }
    }

    // 隔离性证据(每个知识库独立存储目录)
    const isoBox = $("#isolationBox");
    if (isoBox) {
      if (iso && iso.kb_stores.length) {
        isoBox.classList.remove("hidden");
        isoBox.innerHTML = `<h4>🧱 向量硬隔离证据(每库独立 Chroma 实例)</h4>
          <div class="dim">根目录:${esc(iso.vectors_dir)} · 布局:${esc(iso.layout)}</div>
          <table class="iso-table">
            <tr><th>知识库</th><th>存储目录</th><th>独立 sqlite</th><th>文件数</th><th>占用</th><th>分块</th></tr>
            ${iso.kb_stores.map((k) => `<tr>
              <td>${esc(k.kb_name)}</td>
              <td class="mono">${esc(k.path)}</td>
              <td class="mono">${esc((k.sqlite || []).join(", ") || "-")}</td>
              <td>${k.file_count}</td>
              <td>${k.size_kb} KB</td>
              <td>${k.chunk_count}</td>
            </tr>`).join("")}
          </table>`;
      } else {
        isoBox.classList.add("hidden");
      }
    }

    // 增量更新收益明细
    const upBox = $("#updateStatsBox");
    if (upBox) {
      upBox.classList.remove("hidden");
      upBox.innerHTML = `<h4>⚡ 文档增量更新收益(累计)</h4>
        <div class="dim">更新次数 ${up.updates || 0} · 总块数 ${up.chunks_total || 0} ·
          复用 ${up.chunks_reused || 0} · 重嵌入 ${up.chunks_embedded || 0} ·
          复用率 ${((up.reuse_ratio || 0) * 100).toFixed(1)}%</div>
        <div class="dim">累计实际耗时 ${((up.actual_ms || 0) / 1000).toFixed(1)}s,
          整篇重嵌入估算 ${((up.estimated_full_reembed_ms || 0) / 1000).toFixed(1)}s,
          节省 ${((up.saved_ms || 0) / 1000).toFixed(1)}s</div>`;
    }

    $("#configJson").textContent = JSON.stringify({ ...s.config, chat_model: s.chat_model, embed_model: s.embed_model }, null, 2);
  } catch (e) {
    $("#sysStatus").innerHTML = `<div class="empty-note">无法连接后端: ${esc(e.message)}</div>`;
  }
}

/* ================= 【增强C】审计日志 ================= */
async function refreshAudit() {
  const action = $("#auditActionFilter") ? $("#auditActionFilter").value : "";
  try {
    const stats = await api("/api/audit/stats").catch(() => null);
    const card = (label, val, ok) =>
      `<div class="stat-card ${ok ? "ok" : "bad"}"><b>${val}</b><span>${label}</span></div>`;
    if (stats && $("#auditStats")) {
      const top = (stats.by_action || []).slice(0, 4)
        .map((a) => `<span>${esc(a.label)} ${a.count}</span>`).join("");
      $("#auditStats").innerHTML =
        card("审计记录总数", stats.total || 0, true) +
        card("越权拒绝次数", stats.denied || 0, (stats.denied || 0) === 0) +
        `<div class="stat-card"><b>—</b><span>最近记录</span><span>${esc(stats.last_at || "无")}</span></div>` +
        `<div class="stat-card"><b>—</b><span>操作分布</span><span>${top || "无"}</span></div>`;
    }
    const data = await api(`/api/audit?limit=100${action ? "&action=" + encodeURIComponent(action) : ""}`);
    const rows = (data.items || []).map((r) => {
      const d = r.detail || {};
      let extra = "";
      if (r.action === "ask") extra = `${esc((d.question || "").slice(0, 40))}${d.conflicts ? ` · 冲突 ${d.conflicts}` : ""}`;
      else if (r.action === "conflict") extra = `版本差异 ${d.version_conflicts ?? 0} / 跨来源 ${d.genuine_conflicts ?? 0}`;
      else if (r.action === "deny") extra = `原因:${esc(d.reason || "")} · 目标:${esc(d.attempt || "")}`;
      else if (r.action === "doc_add" || r.action === "doc_update") extra = `编号 ${esc(d.doc_no || "-")} · ${d.chunks || d.version || ""}`;
      else if (r.action === "kb_update") extra = `${esc(JSON.stringify(d.after || {}))}`;
      else if (r.action === "kb_create") extra = `可见范围 ${esc(d.visibility || "all")} ${esc(d.allowed_depts || "")}`;
      const resultCls = r.result === "denied" || r.result === "error" ? "genuine" : "ver";
      return `<tr>
        <td class="mono">${esc((r.created_at || "").replace("T", " ").slice(0, 19))}</td>
        <td><span class="kind-badge ${resultCls}">${esc(r.action_label || r.action)}</span></td>
        <td>${esc(r.actor_role || "-")}</td>
        <td>${esc(r.actor_dept || "-")}</td>
        <td>${esc(r.target || r.kb_id || "-")}</td>
        <td>${esc(r.result || "")}</td>
        <td class="dim">${extra}</td>
      </tr>`;
    }).join("");
    $("#auditList").innerHTML = rows
      ? `<table class="iso-table"><tr><th>时间</th><th>操作</th><th>角色</th><th>部门</th><th>目标</th><th>结果</th><th>详情</th></tr>${rows}</table>`
      : `<div class="empty-note">暂无审计记录(进行问答或文档操作后自动留痕)。</div>`;
  } catch (e) {
    $("#auditList").innerHTML = `<div class="empty-note">加载失败: ${esc(e.message)}</div>`;
  }
}
if ($("#auditRefreshBtn")) $("#auditRefreshBtn").addEventListener("click", refreshAudit);
if ($("#auditActionFilter")) $("#auditActionFilter").addEventListener("change", refreshAudit);

/* ---------------- 启动 ---------------- */
(async function init() {
  syncActorUI();
  if ($("#actorSaveBtn")) $("#actorSaveBtn").addEventListener("click", saveActor);
  refreshStatusLine();
  refreshKbSelect();
  setInterval(refreshStatusLine, 15000);
})();
