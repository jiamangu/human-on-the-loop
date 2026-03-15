---
layout: default
title: Agent Team → Slack 实时通知
---

# Claude Code Agent Team → Slack 实时通知

> 将本文件粘贴给 Claude Code，它会自动创建所有文件并完成配置。

---

## 任务

在 Claude Code 的 Agent Team 工作时，自动在 Slack 创建 Thread，实时推送会话主题、成员加入、任务完成等信息。会话结束后发送汇总统计并标记完成。

---

## 目录结构

```
项目/
├── .claude/
│   ├── hooks/
│   │   └── slack_notifier.py   ← 核心通知脚本
│   └── settings.local.json     ← hooks 配置 + 环境变量（不进 git）
```

---

## Hook 事件字段参考

Claude Code hooks 通过 stdin 传入 JSON。**以下是各事件实际传入的关键字段**（踩坑总结，务必按此实现）：

| 事件 | 关键字段 | 说明 |
|------|---------|------|
| SessionStart | `cwd`, `session_id`, `source` | `source` 可能是 startup/resume/clear/compact |
| UserPromptSubmit | `prompt`, `session_id` | `prompt` 是用户输入的完整文本 |
| SubagentStart | `agent_type`, `agent_id` | 用 `agent_type` 取可读名称（如 "cpo"），内置类型如 "Explore"/"general-purpose" 需特殊处理 |
| SubagentStop | `agent_type`, `agent_id`, `last_assistant_message` | **`agent_type` 有时为空**，需 fallback 到 id 映射 |
| TaskCompleted | `task_subject`, `task_description`, `teammate_name` | 优先取 `task_subject` |
| SessionEnd | `cwd`, `reason` | `reason`: clear/logout/prompt_input_exit 等 |

所有事件都包含 `cwd`、`session_id`、`hook_event_name`。

---

## 脚本实现要求

### 环境变量
- `SLACK_BOT_TOKEN`（xoxb- 开头）
- `SLACK_CHANNEL_ID`（C 开头）

### 状态文件
- 路径：`~/.claude/agent-team-slack-state/{cwd_hash}.json`
- key 基于 `cwd`（非 session_id），确保 Lead 和所有 Teammate 共享同一 Thread
- 存储：`thread_ts`、`top_msg_ts`、`top_msg_text`、`session_id`、`members`、`agent_id_map`、`topic`、`task_count`、`start_time`、`round`
- **必须用 `fcntl.flock` 做文件锁**，锁文件使用 `.lock` 后缀（与 state 文件分离），Slack API 调用放在锁外
- 锁采用 `_locked_read_modify_write(cwd, modifier)` 模式：acquire lock → read → modify → write → release lock，modifier 函数内不应调用 Slack API

### Session 隔离
- `SessionStart` 将 `session_id` 写入 state
- 所有其他 handler 在执行前校验 `hook.session_id == state.session_id`
- 不匹配则跳过，防止不同 Claude Code 会话的消息串到同一 Thread

### Agent 名称解析
- 自定义 agent（ceo、cto、cpo 等团队成员）：直接用 `agent_type` 作为显示名
- 内置 agent 类型（`general-purpose`、`Explore`、`Plan`、`statusline-setup`、`claude-code-guide`）：统一显示为 **"C.C."**
- `SubagentStart` 时存储 `agent_id → 显示名` 映射到 state
- `SubagentStop` 时 `agent_type` 可能为空，需查映射取可读名称，最终 fallback 为 "C.C."

### Slack API 限流重试
- 捕获 HTTP 429 和 `error=ratelimited`，读 `Retry-After` header
- 最多重试 3 次，错误输出到 stderr

### 6 个事件的处理逻辑

**SessionStart**
- **不再直接创建 Slack Thread**，仅初始化内存状态
- 存储 `session_id`，初始化 `members`、`agent_id_map`、`task_count` 等
- 清除前一次会话的 `thread_ts`、`top_msg_ts`、`top_msg_text`、`topic`
- Thread 创建延迟到首个团队成员 SubagentStart 时触发（见下方）

**UserPromptSubmit**
- 只在首次触发时执行（state 中 `topic` 为空）
- **前置条件**：`thread_ts` 必须已存在（Thread 尚未创建时直接跳过）
- 从 `prompt` 截取前 40 字符作为会话主题（换行符替换为空格）
- 用 `chat.update` 更新顶层消息：`🤖 Agent 团队会话：{项目名} | {主题} | {时间}`
- 同步更新 `state["top_msg_text"]`（SessionEnd 会读它追加 ✅）

**SubagentStart**（含 Thread 懒创建逻辑）
- 解析显示名（团队成员用 agent_type，内置类型用 "C.C."）
- 存储 `agent_id → 显示名` 映射
- **Thread 懒创建**：
  - 内置 agent（Explore、general-purpose 等）且 Thread 尚未创建 → 仅存 id 映射，不创建 Thread，不发通知
  - 团队成员 agent（ceo、cto 等）检测到 Thread 不存在 → 调用 `_ensure_thread()` 创建顶层消息 `🤖 Agent 团队会话：{项目名} | {时间}`，将 `ts` 存为 `thread_ts`
  - `_ensure_thread()` 内部有并发保护：多个 agent 同时触发时，只有第一个会实际创建 Thread
- 同名成员只通知一次
- Thread 内发送：`👤 {名称} 加入讨论（当前成员：xxx、xxx）`

**SubagentStop**
- 解析显示名：优先 `agent_type`（非空且非 `"unknown"`），否则查 `agent_id_map`，最终 fallback "C.C."
- 无 `thread_ts` 时直接跳过（Thread 未创建的纯内置 agent 场景）
- Thread 内发送：`✅ {名称} 完成 ┃ {时间}`
- 附带 `last_assistant_message` 摘要（截断 500 字）

**TaskCompleted**
- `✅ 任务完成：{task_subject}`（截断 300 字），递增 `task_count`

**SessionEnd**
- 发送汇总统计（成员、对话轮次、任务数、持续时间）
- 用 `chat.update` 更新顶层消息追加 ✅
- **已知问题**：对话轮次（`round`）始终为 0，因为当前代码没有任何事件递增该计数器

---

## settings.local.json 配置

```json
{
  "env": {
    "SLACK_BOT_TOKEN": "xoxb-你的Token",
    "SLACK_CHANNEL_ID": "C你的ChannelID"
  },
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}],
    "SubagentStart": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}],
    "TaskCompleted": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}]
  }
}
```

用 `settings.local.json`（非 `settings.json`），避免 Token 进 git。

---

## Slack Thread 效果示例

```
🤖 Agent 团队会话：voice-agent-project | 讨论 Voice Agent 下一阶段技术优先级 | 23:41:32 ✅
  ├─ 👤 ceo 加入讨论（当前成员：ceo）
  ├─ 👤 cto 加入讨论（当前成员：ceo、cto）
  ├─ ✅ ceo 完成 ┃ 23:45:11
  │   CEO-CTO 双人会议总结 ...
  ├─ ✅ cto 完成 ┃ 23:45:28
  │   技术侧最终确认 ...
  ├─ ✅ C.C. 完成 ┃ 23:46:03
  │   C.C. 报到，Slack 通知系统运行正常。
  ├─ ✅ 任务完成：更新 CONTEXT.md
  └─ 🏁 团队会话结束
     👥 参与成员：ceo、cto、C.C.
     💬 对话轮次：5 轮
     ✅ 完成任务：1 个
     ⏱️ 持续时间：12 分钟
```

---

## 验证方法

```bash
SLACK_BOT_TOKEN="xoxb-xxx" SLACK_CHANNEL_ID="Cxxx" \
  echo '{"hook_event_name":"SessionStart","cwd":"'"$(pwd)"'","session_id":"test-123"}' | \
  python3 .claude/hooks/slack_notifier.py 2>&1
```

Slack 收到消息即成功。如果有报错看 stderr 输出。

---

## 获取 Slack Bot Token

1. 访问 [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. OAuth & Permissions → Bot Token Scopes 添加：`chat:write`、`chat:write.public`
3. Install to Workspace → 复制 Bot User OAuth Token（xoxb- 开头）
4. 在 Slack 中获取 Channel ID：右键 Channel → 查看详情 → 底部
5. 私有 Channel 需 `/invite @你的App名`

---

## 踩坑记录

### 1. 字段名和文档不一致（最常见）

| 错误写法 | 正确写法 | 影响 |
|---------|---------|------|
| `hook.get("agent_name")` | `hook.get("agent_type")` | SubagentStart/Stop 拿不到可读名称 |
| `hook.get("tool_output")` | `hook.get("tool_response")` | PostToolUse 取不到工具返回值 |
| `hook.get("task_description")` | `hook.get("task_subject")` | TaskCompleted 取不到任务标题 |

### 2. SubagentStop 的 agent_type 可能为空

SubagentStart 能拿到 `agent_type`（如 "ceo"），但 SubagentStop 有时只有 `agent_id`（十六进制串如 `aad8fc744691eacb2`）。必须在 SubagentStart 时存映射，SubagentStop 时查映射。

### 3. 内置 agent 类型会混入成员列表

Claude Code 的 `Explore`、`general-purpose`、`Plan` 等内置 agent 也会触发 SubagentStart/Stop。如果不过滤，成员列表会出现这些技术名称。用 `BUILTIN_AGENT_TYPES` 集合统一映射为 "C.C."。

### 4. 同 cwd 不同 session 会串 Thread

State 文件按 `cwd` hash 存储。如果前一个 session 的 state 没被新 session 的 SessionStart 覆盖（比如 SessionStart 失败），后续事件会发到旧 Thread。用 `session_id` 校验解决。

### 5. macOS Python SSL 证书缺失

python.org 安装的 Python（非 Homebrew）默认没有 SSL 证书，调 Slack API 报 `SSL: CERTIFICATE_VERIFY_FAILED`。

修复：运行一次 `/Applications/Python {版本}/Install Certificates.command`

### 6. SessionStart 不再创建 Thread

原始设计让 SessionStart 直接创建 Slack Thread。当前实现改为 **懒创建**：SessionStart 只初始化状态，Thread 在首个团队成员 SubagentStart 时才创建。这样纯 Harness 模式（不启动团队成员）不会产生空 Thread。后续事件通过状态文件读取 `thread_ts`，如果还没写入就静默跳过。

### 7. 对话轮次计数器未实现

SessionEnd 汇总里的"对话轮次"读取 `state["round"]`，但没有任何事件递增这个值，导致始终显示 0。如需修复，应在 `UserPromptSubmit` 中每次触发时递增 `round`（不仅限首次）。

---

## 附录：PostToolUse 事件（可选扩展）

当前方案不监听 PostToolUse，因为实践中发现 **噪音远大于信号**。如果未来需要监听工具调用，以下是要点：

### 字段

| 字段 | 说明 |
|------|------|
| `tool_name` | 工具名称 |
| `tool_input` | 工具输入参数 |
| `tool_response` | 工具返回值（**不是** `tool_output`） |

注意：PostToolUse 没有字段区分当前是主会话还是子 agent。

### 噪音问题

12 个 agent 并行时，单次讨论可以产生 **100+ 条文件操作消息**（Read、Glob、Grep 等），完全淹没有用信息。

### 过滤建议

如果要启用，必须做白名单过滤：

- **跳过**：Read、Write、Edit、Glob、Grep、Skill、ToolSearch
- **只记录高信号工具**：
  - `SendMessage`：`📨 第N轮 ┃ {sender} → {recipient}`
  - `TeamCreate`：`👥 团队已创建：{name}`
  - `Agent`：`🚀 启动 Agent ┃ {描述}`
  - `WebSearch`/`WebFetch`：`🌐 网络调用 ┃ {输入摘要}`
  - `Bash`：`⚙️ 命令执行 ┃ {命令摘要}`
  - `mcp__*`：`🔧 MCP ┃ {工具名}`
- **不要输出 `tool_response`**，只记录 `tool_input` 摘要，减少噪音

### 配置

在 `settings.local.json` 的 hooks 中加一条即可：

```json
"PostToolUse": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/slack_notifier.py\"", "timeout": 10, "async": true}]}]
```

然后在 `slack_notifier.py` 的 HANDLERS 中注册 `"PostToolUse": handle_post_tool_use`。
