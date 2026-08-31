# 开发流程

本文档说明项目的开发与提交规范。

## 当前工作流（单分支，单人开发）

单人开发阶段，直接在 `master` 分支上开发与推送，不使用额外分支和 PR。

```bash
git checkout master
git pull --ff-only origin master   # 拉取最新（如有远程更新）

# ... 开发、改代码 ...

git add .
git commit -m "feat: xxx"
git push origin master             # 直接推 master
```

## 提交信息规范

采用 Conventional Commits 风格，前缀标识改动类型：

- `feat:` 新功能
- `fix:` 修复
- `refactor:` 重构（不改变行为）
- `docs:` 文档
- `chore:` 杂项

示例：`feat: Agent 流式输出`

## 多人协作时

当有其他开发者参与、需要代码审查时，再开启分支保护（GitHub Settings → Branches），
并改用以下流程：

1. 创建特性分支：`git checkout -b feature/xxx`
2. 开发并推送：`git push origin feature/xxx`
3. 开 Pull Request（base 为 master，compare 为 feature/xxx）
4. 合并时使用 **Squash and merge**（保持 master 历史干净，每个功能一个提交）
5. 合并后同步分支（不要用 `git pull`，避免产生 merge 噪音提交）：
   ```bash
   git checkout master && git pull
   git checkout feature/xxx && git reset --hard origin/master
   ```

## 为什么

- **单分支**：单人开发时最省事，无需分支切换与 PR 往返。
- **多人协作再开保护 + Squash merge**：有审查需求时保证质量，且 master 历史保持每个功能一个干净提交，避免「功能提交 + 多个 merge 提交」的混乱。
