# Pull Request

## 这个 PR 做了什么
<!-- 一句话概括;关联 Issue:Close #123 -->

## 改动类型
- [ ] 新功能(feat)
- [ ] 缺陷修复(fix)
- [ ] 文档(docs)
- [ ] 测试 / 评测(test)
- [ ] 重构或性能(refactor / perf)
- [ ] 工程与 CI(chore)

## 改动范围(可多选)
- [ ] 检索(hybrid_retrieval / lexical_index / 切分)
- [ ] 问答与提示词(qa_service)
- [ ] 制度治理(policy_service / 文档元数据)
- [ ] 冲突检测(conflict_service)
- [ ] 权限与审计(auth_service / audit_service)
- [ ] 文档解析(parsers)
- [ ] 接口 / 界面
- [ ] 脚本与文档

## 验证情况(必填)
- [ ] `python -m compileall -q backend/app scripts` 通过
- [ ] `node --check backend/web/app.js` 通过
- [ ] `scripts/smoke_test.py` 通过(19 项)
- [ ] `scripts/verify_innovations.py` 通过(27 项)
- [ ] `scripts/verify_productization.py` 通过(27 项)
- [ ] 涉及检索/切分/提示词时,已运行 `scripts/eval_retrieval.py` 并附指标

### 指标对比(如适用)
| 指标 | 改动前 | 改动后 |
|---|---|---|
| hit@1 | | |
| MRR | | |
| 引用命中率 | | |
| 事实命中率 | | |
| 拒答正确率 | | |

## 自查
- [ ] 未提交 `data/`、`backend/.venv/`、日志或真实制度内容
- [ ] 证据 JSON 已脱敏(`scripts/sanitize_evidence.py`)
- [ ] 新增配置项已在 README / `docs/运维与稳定性.md` 登记
- [ ] 未破坏 `.gitattributes` 的换行规则(`.bat` 保持 CRLF)
