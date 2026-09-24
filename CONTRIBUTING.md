# 贡献指南

感谢参与改进。本项目聚焦**中小企业内部制度文档**的私有 RAG 问答,欢迎以下方向的贡献:
检索质量、制度治理能力、权限与审计、文档解析、评测用例、稳定性与文档。

## 一、开发环境

```bat
:: 1) 运行期依赖(必须)
install.bat

:: 2) 文档/评测工具依赖(仅生成报告或 PPT 时需要)
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

:: 3) 本地模型(必须)
ollama pull deepseek-r1:7b
ollama pull bge-m3
```

启动服务:`start.bat`(守护模式)或

```bat
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 二、代码结构约定

```
backend/app/
├── core/       配置、SQLite、Ollama 客户端(并发闸/超时/探活)
├── rag/        文档解析(pdf/docx/txt/md/csv)与切分
├── services/   业务:kb / doc / qa / classify / vector_store / lexical_index
│               hybrid_retrieval / conflict_service / policy_service / auth / audit
└── routers/    HTTP 接口(kb / doc / qa / classify / audit / eval / system)
backend/web/   免构建单页界面(index.html / style.css / app.js)
scripts/       守护脚本、评测脚本、报告与 PPT 生成器
```

约定:
- **HTTP 端点一律使用同步 `def`**(FastAPI 自动放入线程池),避免在事件循环中做阻塞 IO;
- **不使用 async def 端点内直接调用 Ollama**;
- 涉及**阈值判定**时,区分「排序分」与「门槛分」(见 `hybrid_retrieval.py` 注释);
- 新增可配置项统一放在 `core/config.py`,并在 README/运维手册中登记。

## 三、提交前必须自检

```bat
:: 1) 语法检查
backend\.venv\Scripts\python.exe -m compileall -q backend\app scripts

:: 2) 前端语法
node --check backend\web\app.js

:: 3) 回归(需服务已启动;会真实调用本地模型)
backend\.venv\Scripts\python.exe scripts\smoke_test.py
backend\.venv\Scripts\python.exe scripts\verify_innovations.py
backend\.venv\Scripts\python.exe scripts\verify_productization.py

:: 4) 检索/答案评测(改动检索相关代码时必跑)
backend\.venv\Scripts\python.exe scripts\eval_retrieval.py
```

**更省事的做法**:一条命令复现 CI 的全部检查(推送前建议先跑,避免 CI 变红):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci_local.ps1        # 与 CI 等价的 4 组检查
powershell -ExecutionPolicy Bypass -File scripts\ci_local.ps1 -Full  # 追加完整回归与评测(需 Ollama)
```

**通过标准**:本地 CI 全绿;三套回归共 73 项断言全部通过;检索指标不低于基线
(hit@1 = 1.000、引用命中 = 1.000、事实命中 = 1.000、拒答正确率 = 1.000)。

改动检索/切分/提示词后,请在 PR 描述中给出**改进前后的指标对比**(评测脚本会输出 JSON 证据)。

## 四、提交信息规范

采用 Conventional Commits:

```
<type>(<scope>): <简要说明>

feat(retrieval): 引入 MMR 去冗余,提升多要点问题的条款覆盖
fix(qa): 修复门槛分与排序分混用导致的拒答失效
docs(ops): 补充检索调参章节
test(eval): 增加编号歧义用例
chore(ci): 增加 JS 语法检查
```

- type:`feat` / `fix` / `docs` / `test` / `refactor` / `perf` / `chore`
- scope 建议:`retrieval` / `qa` / `doc` / `kb` / `policy` / `auth` / `audit` / `ui` / `ops`
- 一个 PR 只做一件事;涉及阈值调整必须附评测数据。

## 五、PR 检查清单

- [ ] 已跑通三套回归(73 项断言)
- [ ] 改动检索/切分/提示词时,已附评测指标对比
- [ ] 未提交 `data/`、`backend/.venv/`、日志或含真实制度内容的文件
- [ ] 新增配置项已在 README 与运维手册登记
- [ ] 涉及新能力时,已补充或更新评测用例

## 六、禁止事项

- **不要提交任何真实企业内部制度文档**(评测语料请使用虚构文本);
- 不要提交含个人绝对路径的产物(证据 JSON 请先运行 `scripts/sanitize_evidence.py`);
- 不要移除 `.gitattributes` 中的换行规则(`.bat` 必须 CRLF,否则 cmd 解析异常)。
