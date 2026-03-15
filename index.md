---
layout: default
title: Home
---

# Human on the Loop

如果你也不喜欢守着 CLI 等 Agent 干活，这里有两份可以直接粘贴给 Claude Code 执行的 Playbook，帮你把关键事件推送到 Slack。

---

## Playbooks

### [Agent Team → Slack 实时通知](claude-agent-slack-integration)

通过 Claude Code Hooks，在 Agent Team 协作时自动创建 Slack Thread，实时推送成员加入、任务完成、会话汇总等信息。

**适用场景：** 多 Agent 并行工作时，不想逐个切 tab 看进度

**包含：** Hook 事件字段参考 · 脚本实现要求 · settings.local.json 配置 · 踩坑记录

---

### [PR 自动创建与 Slack 通知](pr-automation-slack-playbook)

一键推分支、创建 PR，GitHub Actions 自动在 PR 生命周期关键节点发 Slack 通知。

**适用场景：** 想把 PR 创建和通知全自动化，不手动操作 GitHub 网页

**包含：** create_pr.sh 脚本 · GitHub Actions Workflow · Secret 配置 · 排障清单

---

## 使用方式

1. 打开上面任意一份 Playbook
2. 复制全文
3. 粘贴给 Claude Code
4. 它会自动创建所有文件并完成配置

## 关于

这个项目的核心理念：**人类掌舵，Agent 执行**。你不需要盯着终端看 Agent 做事，让通知系统替你值班，有事它会叫你。
