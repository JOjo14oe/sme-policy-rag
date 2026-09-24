# GitHub 发布清单(提交前逐项核对)

**项目**:面向中小企业内部制度文档的多知识库私有 RAG 智能问答系统(rag-local)
**清单编制日期**:2026-09-24
**当前状态**:代码与文档齐备,**待完成:安装 Git → 初始化仓库 → 首次提交 → 推送到 GitHub**

---

## 0. 发布前需要你先拍板的 4 件事

| # | 决策项 | 建议 | 影响 |
|---|---|---|---|
| 1 | **许可证** | 已放置 `LICENSE`(MIT,著作权人:智启未来 AI 团队)。若需专利条款可改 Apache-2.0 | 决定他人可否商用、是否需保留版权声明 |
| 2 | **仓库名** | `rag-local`(或 `sme-policy-rag`) | 影响 README 徽章、Issue 模板中的链接占位 |
| 3 | **公开范围** | 建议:**公开**源码 + 文档;**不公开**运行数据与真实制度内容 | 决定是否需要私有仓库 |
| 4 | **答辩材料是否入库** | 建议入库(体现完整交付),但仓库为公开时请确认无敏感信息 | `docs/` 下的 PPT/讲稿/评审说明 |

> Issue 模板中的 `OWNER/REPO` 占位符请在仓库创建后替换为实际地址:
> `.github/ISSUE_TEMPLATE/config.yml`。

---

## 1. 安装 Git(本机当前未安装)

```powershell
# 方式 A:winget(推荐)
winget install --id Git.Git -e --source winget

# 方式 B:官网下载安装包
# https://git-scm.com/download/win
```

安装后**重开终端**,执行 `git --version` 应输出版本号。

一次性身份配置(提交作者信息,会写入提交历史):

```powershell
git config --global user.name  "你的名字或团队名"
git config --global user.email "你的邮箱@example.com"
git config --global init.defaultBranch main
git config --global core.autocrlf false    # 换行已由 .gitattributes 统一管理
```

---

## 2. 提交前清理(关键:避免私有数据入库)

```powershell
cd <你的项目目录>          # 例如 D:\code\rag-local

# 2.1 证据文件脱敏(把本机绝对路径替换为 <PROJECT_ROOT>)
backend\.venv\Scripts\python.exe scripts\sanitize_evidence.py --all --check   # 先检查
backend\.venv\Scripts\python.exe scripts\sanitize_evidence.py --all           # 再执行

# 2.2 确认私有数据不会被提交(应无输出)
Test-Path data\meta.db          # 存在正常,但不得进入 git
```

**绝不提交清单**(已写入 `.gitignore`,提交前仍需复查):

| 内容 | 原因 |
|---|---|
| `data/`(meta.db、vectors/、files/、logs/) | 你本人的知识库内容、向量、原文、审计日志 |
| `backend/.venv/` | 虚拟环境(约 420MB),可由 `install.bat` 重建 |
| `__pycache__/`、`*.pyc` | 编译缓存 |
| `*.log`、`data/logs/` | 运行日志(含请求内容) |
| `scripts/demo_docs/` | 演示文档生成物(由脚本重建) |
| `.env`、`data/config.json` | 本地覆写配置(可能含个人路径) |
| 真实企业内部制度文档 | 涉密;评测语料请使用虚构文本 |

---

## 3. 初始化仓库并首次提交

建议**分 3 个逻辑提交**,便于回溯与代码评审:

```powershell
cd <你的项目目录>

git init

# ---- 提交 1:工程骨架与元信息 ----
git add .gitignore .gitattributes LICENSE README.md CHANGELOG.md CONTRIBUTING.md SECURITY.md
git add .github/
git commit -m "chore: 初始化仓库元信息(gitignore/属性/许可证/贡献与安全说明/CI)"

# ---- 提交 2:后端与前端源码 ----
git add backend/requirements.txt backend/requirements-dev.txt
git add backend/app/ backend/web/
git commit -m "feat: 多知识库私有 RAG 问答系统(硬隔离/增量更新/冲突检测/版本治理/权限审计/混合检索)"

# ---- 提交 3:脚本与文档 ----
git add scripts/ docs/ install.bat start.bat
git commit -m "docs+scripts: 守护与评测脚本、交付文档与答辩材料"

# 核对暂存结果(确认无 data/ 与 .venv)
git status
git ls-files | Measure-Object -Line          # 查看纳入版本控制的文件数量
```

### 提交前的自检(逐项打勾)

- [ ] `git status` 输出中**没有** `data/`、`backend/.venv/`、`*.log`
- [ ] `git ls-files | Select-String "\.venv|data/|\.log$|\.db$"` **无输出**
- [ ] 单个文件均小于 10MB(`git ls-files -s` 后可按需检查)
- [ ] 证据 JSON 已脱敏(`sanitize_evidence.py --check` 通过)
- [ ] `.bat` 文件在 `.gitattributes` 中标记为 `eol=crlf`
- [ ] `LICENSE` 著作权人已按需修改

---

## 4. 创建远程仓库并推送

在 GitHub 网页端:New repository → 名称填 `rag-local` → **不要**勾选 "Add README/.gitignore"
(本地已有)→ 创建后复制仓库地址,然后:

```powershell
git branch -M main
git remote add origin https://github.com/<你的用户名>/rag-local.git
git push -u origin main

# 打首个版本标签
git tag -a v1.0.0 -m "v1.0.0: 首个公开版本(制度治理 + 权限审计 + 混合检索 + 评测体系)"
git push origin v1.0.0
```

> 推送需要认证:建议使用 **Personal Access Token**(Settings → Developer settings → Tokens)
> 或 GitHub Desktop / `gh auth login`。

---

## 5. 仓库页面设置建议

| 项 | 建议值 |
|---|---|
| **Description** | 面向中小企业内部制度文档的多知识库私有 RAG 问答系统:库级物理隔离 · 制度版本治理 · 跨库冲突检测 · 部门权限与审计 · 全本地化 |
| **Topics** | `rag` `ollama` `chromadb` `fastapi` `private-deployment` `knowledge-base` `chinese` `sme` `document-qa` `local-llm` |
| **Website** | 可留空(纯本地系统) |
| **Releases** | 用 v1.0.0 标签创建 Release,正文可直接用 `CHANGELOG.md` 的 1.0.0 段落 |
| **Security** | 开启 Private vulnerability reporting(与 `SECURITY.md` 呼应) |
| **About→Include** | 勾选 Releases / Packages 视需要 |

README 顶部可加徽章(仓库创建后替换 `OWNER/REPO`):

```markdown
![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python](https://img.shields.io/badge/Python-3.12-blue.svg)
```

---

## 6. CI 说明(已配置,推送即生效)

`.github/workflows/ci.yml` 包含三个作业,**不需要 GPU / Ollama**,可在 GitHub 免费额度内运行:

| 作业 | 内容 |
|---|---|
| `python-check` | 编译全部 Python 源码 + 导入 FastAPI 应用 + 核心服务模块导入自检 |
| `frontend-check` | `node --check` 校验 `app.js`,确认静态资源齐全 |
| `hygiene` | 拦截私有数据(`data/`、`.venv/`、日志/数据库)与**个人绝对路径**入库 |

> 完整 73 项回归与评测需要本机 Ollama 与模型,不放入 CI;请在提交前本地执行(见 `CONTRIBUTING.md`)。

---

## 7. 发布内容一览(本次将入库的目录/文件)

```
rag-local/
├── .gitignore / .gitattributes / LICENSE
├── README.md / CHANGELOG.md / CONTRIBUTING.md / SECURITY.md
├── start.bat / install.bat
├── .github/
│   ├── workflows/ci.yml
│   ├── ISSUE_TEMPLATE/{bug_report.md, feature_request.md, config.yml}
│   └── PULL_REQUEST_TEMPLATE.md
├── backend/
│   ├── requirements.txt / requirements-dev.txt
│   ├── app/{main.py, core/, rag/, services/, routers/}     # 30 个 Python 模块
│   └── web/{index.html, style.css, app.js}                 # 免构建前端
├── scripts/                                                # 守护 / 评测 / 报告生成
└── docs/
    ├── 项目优势总结.md/.docx · 创新点落地总结报告.md/.docx
    ├── 产品化增强落地报告.md/.docx · 竞品对比与差距分析.md/.docx
    ├── 运维与稳定性.md · 验收报告.md
    ├── 答辩讲稿(5分钟演讲+3分钟问答).md/.docx
    ├── 答辩PPT(5分钟精简版).pptx · 中小企业制度文档RAG系统答辩PPT(改进版).pptx
    └── GitHub发布清单.md(本文件)
```

---

## 8. 常见坑与处理

| 问题 | 原因 | 处理 |
|---|---|---|
| `.bat` 推送后在他人机器上报语法错误 | 换行被转成 LF | 已由 `.gitattributes` 固定为 CRLF;如已误提交:`git add --renormalize .` |
| 误把 `data/` 提交 | 提交时 `.gitignore` 未生效 | `git rm -r --cached data` 后重新提交;必要时用 `git filter-repo` 清历史 |
| 中文文件名在他人机器上乱码 | 未设置 `core.quotepath` | `git config --global core.quotepath false` |
| 推送被拒(认证失败) | 未配置 Token / SSH | 使用 PAT 或 `gh auth login` |
| 克隆后无法运行 | 缺虚拟环境与模型 | 依次执行 `install.bat` → `ollama pull deepseek-r1:7b` → `ollama pull bge-m3` → `start.bat` |
| 想重新生成 PPT 但报"文件被占用" | PowerPoint 正在打开该文件 | 关闭 PowerPoint 后重跑 `scripts/make_defense_ppt.py` |

---

## 9. 推送后的验证(建议做一遍)

```powershell
# 克隆到临时目录,模拟他人首次使用
cd $env:TEMP
git clone https://github.com/<你的用户名>/rag-local.git rag-clone-test
cd rag-clone-test
dir                      # 应看到 backend / scripts / docs / README 等
dir data                 # 应提示"找不到路径"(说明私有数据未被提交)
```

- [ ] 克隆成功,目录结构完整
- [ ] 仓库内**无** `data/`、`.venv/`
- [ ] `docs/` 中无个人绝对路径(`scripts/sanitize_evidence.py --check --all` 在克隆目录中通过)
- [ ] GitHub Actions 三个作业均为绿色
- [ ] Release v1.0.0 已发布,正文含功能清单与已知限制

---

## 10. 后续可选增强(非阻塞)

| 项 | 说明 |
|---|---|
| Docker 部署 | 竞品普遍提供;可作为 v1.1(注意本机模型仍需 Ollama 或容器内 ollama) |
| OCR 支持 | 扫描件制度文档解析(接入 Tesseract/PaddleOCR) |
| 交叉编码器重排 | 需 ≥8GB 显存,与本机生成模型冲突,可按硬件条件开关 |
| 多语言 README | 增加 `README.en.md`(竞品均为双语) |
| 演示 GIF | 录制"建库→提问→冲突告警→越权 403→审计留痕"30 秒演示,放 README 首屏 |
