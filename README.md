# 多知识库私有 RAG 问答系统(rag-local)

**面向中小企业内部制度文档**的多知识库私有 RAG 问答系统:解决内部规章、人事制度、管理文件分散,以及传统通用 RAG 存在的**知识库隔离差、文档更新成本高、不同版本制度信息互相冲突难以识别、私有内部数据外泄风险**等现实痛点。

**完全独立、自包含**:一个文件夹 + 本机 Ollama 即可长期运行,不依赖任何在线服务或宿主会话。

## 业务痛点 → 系统能力

| 业务痛点 | 系统能力 | 状态 |
|---|---|---|
| 规章/人事/管理文件分散 | 多知识库管理 + 无标签文档 AI 自动分类建库 | ✅ |
| 知识库隔离差 | **向量硬隔离**:每库独立 Chroma 实例与存储文件(删库不污染它库) | ✅ |
| 文档更新成本高 | **块级增量更新**:仅重嵌入变更条目,实测复用率 50%+ | ✅ |
| **不同版本制度冲突难识别** | **制度版本与生效期管理**:检索排除已废止/未生效版本;冲突分为「版本差异(以现行版为准)」与「跨来源冲突」;版本链 + 到期提醒 | ✅ |
| **敏感制度越权访问** | **部门/角色权限隔离**:库可见范围(全员/指定部门),全链路鉴权 + 越权留痕 | ✅ |
| **合规留痕要求** | **审计日志**:问答/冲突/越权/文档与库变更全程留痕,可查询统计 | ✅ |
| 私有数据外泄风险 | **全本地化**:解析/检索/问答/检测全部本机 Ollama,零 Token、不出内网 | ✅ |

详见 [`docs/产品化增强落地报告.md`](docs/产品化增强落地报告.md)、[`docs/竞品对比与差距分析.md`](docs/竞品对比与差距分析.md)。

## 特性一览

| 层 | 内容 |
|---|---|
| 后端 | Python 3.12 + FastAPI(重 IO 端点线程池化,事件循环不阻塞) |
| 向量库 | ChromaDB **每知识库一个独立实例**(`data/vectors/<kb_id>/`),分块向量与 doc_id 强绑定 |
| 检索 | **混合检索**:BM25(中文二元组,零分词依赖)+ 向量 + **RRF 融合** + 确定性元数据重排(编号/部门/时效/条款);排序分与拒答门槛分分离,门槛经评测集校准 |
| 元数据库 | SQLite(库 / 文档 / 制度元数据 / 块级哈希账本 / 更新收益日志 / 审计日志) |
| 模型 | Ollama 本地 `deepseek-r1:7b`(生成)+ `bge-m3`(嵌入),零 Token、数据不出本地 |
| 稳定性 | 守护进程(后端崩溃自愈 / Ollama 宕机唤醒)· 推理全局串行(防显存挤爆)· 前端 SSE 超时中断 |
| 评测 | **内置可复现评测集**:hit@1/hit@3/MRR 检索消融 + 引用命中率/事实命中率/拒答正确率 |
| 前端 | 单页 Web 界面(免构建):身份切换 / 冲突告警面板 / 制度属性与版本链 / 隔离证据表 / 增量收益 / 审计页 |

---

## 快速开始

### 前置要求
1. **Python 3.10+**(推荐 3.12);未装可设置环境变量 `PYTHON_EXE` 指向 `python.exe` 供脚本使用;
2. **Ollama**(https://ollama.com 下载安装,Windows 托盘常驻)。

### 1. 安装(仅首次)

```bat
install.bat
```
自动探测 Python → 创建 `.venv` → 安装依赖。

### 2. 准备模型

```bat
ollama pull deepseek-r1:7b
ollama pull bge-m3
```

### 3. 启动

```bat
start.bat
```
双击后自动:写入 Ollama 显存保护变量 → 拉起/唤醒 Ollama → 启动后端守护(崩溃 5s 自愈)→ 打开浏览器 **http://127.0.0.1:8000**。
关闭方式:守护窗口 `Ctrl+C`。

> 也可不带守护直接调试:`cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`

---

## 目录结构

```
rag-local/
├── .gitignore / .gitattributes          # 版本控制规则(私有数据排除、.bat 保持 CRLF)
├── LICENSE / CHANGELOG.md               # MIT 许可证 / 变更日志
├── CONTRIBUTING.md / SECURITY.md         # 贡献指南(含提交前自检)/ 安全与隐私说明
├── start.bat / install.bat              # 入口:启动(守护)/ 安装
├── .github/                             # CI 工作流 + Issue / PR 模板
├── backend/
│   ├── requirements.txt        # 运行与报告生成所需依赖(含 python-docx / pymupdf)
│   ├── app/                     # 后端源码(FastAPI)
│   │   ├── main.py              # 入口(启动时执行向量布局迁移与清理)
│   │   ├── core/                # config / db(含块级账本) / ollama_client(并发闸、探活、超时)
│   │   ├── rag/                 # parsers(pdf/docx/txt/md/csv,含表格结构化) / chunker
│   │   ├── services/            # kb / doc / qa / classify / vector_store / lexical_index
│   │   │                        # hybrid_retrieval / conflict_service / policy_service / auth / audit
│   │   └── routers/             # kb / doc / qa / classify / audit / eval / system
│   ├── web/                     # 前端(index.html / style.css / app.js,免构建)
│   └── .venv/                   # 虚拟环境(install.bat 创建,不入库)
├── data/                        # 全部运行数据(私有;不入库;备份整个目录即迁移)
│   ├── meta.db                  # SQLite(库/文档/制度元数据/块哈希账本/更新账本/审计日志)
│   ├── vectors/<kb_id>/         # 【创新点1】每个知识库独立的 Chroma 实例与 sqlite
│   ├── chroma_legacy_backup/    # 旧单实例布局的迁移备份(可回滚)
│   ├── files/<kb_id>/           # 文档原文归档
│   └── logs/                    # guard.log / backend.out.log / backend.err.log
├── docs/                        # 交付文档(报告 / 手册 / 发布清单)
│   ├── 产品化增强落地报告.md/.docx      # ★ 版本治理 / 权限隔离 / 审计日志(含实测证据)
│   ├── 竞品对比与差距分析.md/.docx      # ★ 对标 9 个 GitHub 开源项目的能力矩阵与差距
│   ├── 验收报告.md                      # 原端到端验收记录(19 项)
│   ├── 运维与稳定性.md                  # 根因清单、启动方式、FAQ、检索调参、评测用法
│   └── GitHub发布清单.md                # 提交到 GitHub 的逐项核对清单
└── scripts/
    ├── run_server.ps1           # 守护脚本(自愈核心)
    ├── make_demo_docs.py        # 生成演示文档
    ├── seed_demo_kbs.py         # 预置演示知识库
    ├── smoke_test.py            # 端到端验收测试(19 项)
    ├── verify_innovations.py    # 三项创新验证套件(27 项,输出证据 JSON)
    ├── verify_productization.py # 产品化增强验证套件(27 项,输出证据 JSON)
    ├── eval_retrieval.py        # 检索/答案质量评测(四模式消融,输出证据 JSON)
    ├── sanitize_evidence.py     # 提交前证据脱敏(替换本机绝对路径)
    ├── make_productization_report.py  # 生成《产品化增强落地报告》Word(docx)
    ├── md_to_docx.py            # 通用 Markdown → Word 转换(竞品对比等文档)
    └── docx_to_md.py            # Word → Markdown 反向转换
```

---

## 功能对照《项目目标总结》

| 核心目标 | 使用位置 | 说明 |
|---|---|---|
| 1. 结构化知识库管理 | 「知识库管理」+「自动分类建库」 | 手动建库(可设可见范围/归属部门/制度类别);无标签混杂文档批量上传 → AI 聚类 → 建议库名 → 确认后自动建库入库 |
| 2. 文档全生命周期管控 | 文档管理抽屉 | 唯一 `doc_id` 绑定全部向量;上传=新增、换文件=**增量更新**(版本+1)、删除=按 doc_id 精准清向量;**制度属性(编号/生效期/替代关系)**可单独维护;「对账清理」扫孤儿向量,零残留 |
| 3. 双兜底问答路由 | 「智能问答」 | 自动路由(问题嵌入 × 库质心)选库;手动指定知识库兜底;低相关片段拒答不硬编,回答带引用;**跨库矛盾/版本差异自动告警**;检索自动排除已废止版本 |
| 4. 全本地化零 Token | 全系统 | 解析/分类/检索/问答/冲突检测全部本机 Ollama;无外部 API;断网可用 |
| 5.(产品化)权限与合规 | 侧栏身份切换 + 「审计日志」页 | 受限库按部门隔离并全链路鉴权;问答/冲突/越权/文档与库变更全程留痕 |

---

## 验收与质量

- **原端到端验收 19/19**、**三项创新验证 27/27**、**产品化增强验证 27/27**,合计 **73 项断言全部通过**;
- **检索与答案评测**(`scripts/eval_retrieval.py`,10 篇制度语料 / 17 条标注问题):
  | 检索模式 | hit@1 | hit@3 | MRR |
  |---|---|---|---|
  | vector(仅向量) | 0.933 | 1.000 | 0.967 |
  | lexical(仅 BM25) | 0.800 | 1.000 | 0.900 |
  | **hybrid(RRF 融合)** | **1.000** | **1.000** | **1.000** |
  | hybrid_rerank(+元数据重排,默认) | 1.000 | 1.000 | 1.000 |
  答案质量:**引用命中 1.000 · 事实命中 1.000 · 拒答正确率 1.000**,平均约 8 秒/答;
- 故障恢复实测:后端强杀 → 5s 自愈;Ollama 宕机 → 守护唤醒;
- 增量更新实测(条款级切分后):五段制度文档改一段 → 复用 **4/5 块(80%)**,**344 ms** vs 全量重嵌入 **1628 ms**。

### 相关接口速查

| 接口 | 用途 |
|---|---|
| `GET /api/system/isolation` | 【创新点①】各库独立存储目录 / sqlite 文件 / 占用(隔离性证据) |
| `GET /api/system/update-stats` | 【创新点②】增量更新累计收益(复用率、节省耗时) |
| `POST /api/qa/conflict-check` | 【创新点③+增强A】跨库冲突检测(含版本差异分类与建议依据版本) |
| `PATCH /api/doc/meta/{kb_id}/{doc_id}` | 【增强A】修改制度属性(编号/名称/生效期/状态/发布部门/适用范围/替代关系) |
| `GET /api/doc/versions/{kb_id}?doc_id=` | 【增强A】制度版本链 |
| `GET /api/doc/expiring?days=30` | 【增强A】即将到期的现行制度提醒 |
| `GET /api/audit` · `GET /api/audit/stats` | 【增强C】审计记录查询与统计 |
| `POST /api/eval/retrieval` · `POST /api/eval/answer` | 【评测】检索消融(vector/lexical/hybrid/hybrid_rerank)与答案质量评测 |
| `GET /api/system/status` | 含检索配置(通道开关/RRF 参数/元数据权重)与词法索引缓存状态 |

### 检索调优(data/config.json)

| 参数 | 默认 | 说明 |
|---|---|---|
| enable_lexical_channel | true | 是否启用 BM25 词法通道 |
| rrf_k / rrf_vector_weight / rrf_lexical_weight | 60 / 1.0 / 1.0 | RRF 融合参数 |
| score_threshold | 0.55 | **拒答门槛**(向量余弦绝对量纲,经评测集校准) |
| context_min_score | 0.34 | 片段纳入上下文的下限(比拒答门槛宽松) |
| max_chunks_per_doc | 5 | 同一文档最多纳入的块数(多要点问题需要) |
| meta_boost_docno / dept / recency / heading | 0.18 / 0.06 / 0.03 / 0.03 | 元数据重排权重 |

### 身份与权限(增强B)

请求头携带身份,中文需百分号编码(前端已自动处理):

```
X-Actor-Role: hr          # admin 为管理员,可见全部
X-Actor-Dept: %E4%BA%BA... # 部门(如「人力资源部」)
X-Actor-Name: zhangsan
```

界面左下角可直接切换角色/部门来验证权限效果。受限库(visibility=restricted)仅对 `allowed_depts` 内部门与管理员可见。

---

## 运维、故障排查与调参

遇到任何"无响应 / 卡死 / 崩溃"问题,先读 **[`docs/运维与稳定性.md`](docs/运维与稳定性.md)**:含根因清单、正确启动姿势、常见问题处理、模型资源参考、`data/config.json` 调参说明。

---

## 迁移与备份

整个 `data\` 目录即全部数据(库、文档、向量、日志),拷贝即备份/迁移;目标机器装好 Python + Ollama + 模型后,把整个 `rag-local` 文件夹拷过去,运行 `install.bat`(重建 .venv)与 `start.bat` 即可。

## 已知限制

- 纯扫描件 PDF 需另接 OCR(当前跳过并提示);
- 自动分类用质心增量聚类,簇粒度由 `cluster_sim_threshold`(默认 0.48,config.json 可调)控制;
- 会话与分类中间态存内存,重启后需重新上传;
- 单机 ChromaDB 建议 ≤ 数十万分块,更大规模可换 Qdrant(保留同一 doc_id 元数据约定即可平滑迁移)。
