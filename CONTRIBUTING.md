# 开发流程

本文档说明项目的分支与合并规范，避免提交历史混乱。

## 分支模型

- `master`：主分支，保持可发布状态
- `fix-auto-mode`：开发分支，所有改动在此进行

## 提交流程

1. 在 `fix-auto-mode` 分支开发、提交并推送：

   ```bash
   git checkout fix-auto-mode
   # ... 开发 ...
   git add .
   git commit -m "feat: xxx"
   git push origin fix-auto-mode
   ```

2. 在 GitHub 上开 Pull Request，base 为 `master`，compare 为 `fix-auto-mode`。

3. **合并时使用「Squash and merge」**（点合并按钮右侧下拉箭头选择）。
   这样整个 PR 被压缩为一个干净提交，不会产生 `Merge pull request` 噪音提交。

## 合并后同步分支

PR 合并后，**不要用 `git pull`**（会产生 `Merge branch 'master' into fix-auto-mode` 噪音提交），改用 reset 让分支对齐 master：

```bash
git checkout master
git pull
git checkout fix-auto-mode
git reset --hard origin/master   # 丢弃已合并的本地提交，分支对齐 master
```

## 提交信息规范

采用 Conventional Commits 风格，前缀标识改动类型：

- `feat:` 新功能
- `fix:` 修复
- `refactor:` 重构（不改变行为）
- `docs:` 文档
- `chore:` 杂项

示例：`feat: 引入 Agent Loop 与独立 Memory Agent`

## 为什么这样

直接使用「Create a merge commit」+ `git pull` 会让每个功能在 master 上产生三个提交
（功能提交 + 两个 merge 提交），历史混乱。改用 Squash and merge + reset 同步，
master 历史保持每个功能一个提交的线性整洁状态。
