# Contributing to FrameWise

感谢你的贡献 🎉

## 分支策略

```
main           ← 稳定版本，只通过 PR 合并
feat/xxx       ← 新功能分支
fix/xxx        ← Bug 修复分支
docs/xxx       ← 文档更新分支
```

**所有改动请从 `main` 分支拉新分支，完成后提 PR 合并回 `main`。**

## 开发流程

```bash
git checkout main
git pull
git checkout -b feat/your-feature-name
# ... 写代码 ...
git add -A && git commit -m "feat: describe your changes"
git push -u origin feat/your-feature-name
# 在 GitHub 上创建 Pull Request
```

## Commit 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新功能
fix: Bug 修复
docs: 文档更新
refactor: 重构
chore: 构建/工具链
```

## PR 要求

- 描述清楚改了什么、为什么改
- 如果涉及新功能，更新 `.env.example` 中的配置说明
- 中文或英文都可以

## 目录约定

| 目录 | 用途 |
|------|------|
| `backend/services/` | 核心业务逻辑 |
| `extension/` | Chrome 插件 |
| `frontend/` | Web 前端 |
| `项目文档/` | 计划书、设计文档（不上传 git） |

## 问题反馈

- Bug → [GitHub Issues](https://github.com/EgoistaCercis/framewise/issues/new?template=bug_report.md)
- 功能建议 → [GitHub Issues](https://github.com/EgoistaCercis/framewise/issues/new?template=feature_request.md)
- 安全漏洞 → 请私下联系，不要公开提 Issue（见 [SECURITY.md](SECURITY.md)）
