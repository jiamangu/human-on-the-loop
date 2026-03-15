# PR 自动创建与 Slack 通知迁移手册

这份文档用于把当前仓库已经验证过的机制，迁移到另一个 GitHub 仓库中。

目标效果：

- 本地通过脚本自动推送当前分支并创建 PR
- GitHub Actions 在 PR 创建、重新打开、转为 Ready for review、后续同步更新、关闭或合并时，自动向 Slack 发送通知

---

## 1. 前置条件

执行前先确认以下条件已经满足：

- 本机已安装 `git`
- 本机已安装 `gh`（GitHub CLI）
- 本机 `gh auth status` 显示已登录且 token 有 `repo`、`workflow` 权限
- 目标仓库已经配置好远端 `origin`
- 你有权限在目标仓库中配置 GitHub Actions Secrets
- 你已经在 Slack 中创建好 Incoming Webhook URL

建议先执行：

```bash
gh auth status
git remote -v
```

---

## 2. 需要添加的文件

在目标仓库中新增以下文件：

```text
.github/pull_request_template.md
.github/workflows/pr-slack-notify.yml
scripts/create_pr.sh
```

如果目标仓库没有 `scripts/` 目录，可以直接新建。

---

## 3. `scripts/create_pr.sh`

用途：

- 校验当前分支不是默认分支
- 校验工作区是干净的
- 推送当前分支到远端
- 使用 `gh pr create` 创建 PR

建议内容如下：

```bash
#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/create_pr.sh --title "PR title" [--body-file path] [--base main] [--draft]

Description:
  Push the current branch to origin and create a GitHub pull request with gh.

Notes:
  - This script expects your changes to already be committed.
  - If you run it on main, it will stop to avoid opening a PR from the default branch.
  - Required tools: git, gh
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

TITLE=""
BODY_FILE=""
BASE_BRANCH="main"
DRAFT_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)
      TITLE="${2:-}"
      shift 2
      ;;
    --body-file)
      BODY_FILE="${2:-}"
      shift 2
      ;;
    --base)
      BASE_BRANCH="${2:-}"
      shift 2
      ;;
    --draft)
      DRAFT_FLAG="--draft"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TITLE" ]]; then
  echo "--title is required." >&2
  usage >&2
  exit 1
fi

if [[ -n "$BODY_FILE" && ! -f "$BODY_FILE" ]]; then
  echo "Body file not found: $BODY_FILE" >&2
  exit 1
fi

require_cmd git
require_cmd gh

CURRENT_BRANCH="$(git branch --show-current)"

if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "Could not determine current branch." >&2
  exit 1
fi

if [[ "$CURRENT_BRANCH" == "$BASE_BRANCH" ]]; then
  echo "Current branch is $BASE_BRANCH. Please create a feature branch before opening a PR." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Please commit or stash changes before creating a PR." >&2
  exit 1
fi

echo "Pushing branch $CURRENT_BRANCH to origin..."
git push -u origin "$CURRENT_BRANCH"

PR_ARGS=(
  pr create
  --base "$BASE_BRANCH"
  --head "$CURRENT_BRANCH"
  --title "$TITLE"
)

if [[ -n "$BODY_FILE" ]]; then
  PR_ARGS+=(--body-file "$BODY_FILE")
else
  PR_ARGS+=(--fill)
fi

if [[ -n "$DRAFT_FLAG" ]]; then
  PR_ARGS+=("$DRAFT_FLAG")
fi

echo "Creating pull request..."
gh "${PR_ARGS[@]}"
```

添加后执行：

```bash
chmod +x scripts/create_pr.sh
```

---

## 4. `.github/pull_request_template.md`

这个文件不是必须，但建议统一 PR 结构。

建议内容：

```md
## Summary
- 

## Validation
- 

## Notes
- 
```

---

## 5. `.github/workflows/pr-slack-notify.yml`

这个工作流负责在 PR 生命周期关键节点把通知发到 Slack。

建议内容如下：

```yaml
name: PR Slack Notify

on:
  pull_request:
    types:
      - opened
      - reopened
      - ready_for_review
      - synchronize
      - closed

jobs:
  notify:
    runs-on: ubuntu-latest
    env:
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
    steps:
      - name: Skip when webhook is not configured
        if: ${{ env.SLACK_WEBHOOK_URL == '' }}
        run: |
          echo "SLACK_WEBHOOK_URL is not configured; skipping notification."

      - name: Build message payload
        if: ${{ env.SLACK_WEBHOOK_URL != '' }}
        id: payload
        env:
          REPO: ${{ github.repository }}
          ACTION: ${{ github.event.action }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          PR_TITLE: ${{ github.event.pull_request.title }}
          PR_URL: ${{ github.event.pull_request.html_url }}
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
          BASE_REF: ${{ github.event.pull_request.base.ref }}
          HEAD_REF: ${{ github.event.pull_request.head.ref }}
          MERGED: ${{ github.event.pull_request.merged }}
        run: |
          node - <<'NODE' >> "$GITHUB_OUTPUT"
          const action = process.env.ACTION;
          const merged = process.env.MERGED === "true";

          let status = "updated";
          if (action === "opened") status = "opened";
          if (action === "reopened") status = "reopened";
          if (action === "ready_for_review") status = "ready for review";
          if (action === "closed") status = merged ? "merged" : "closed";

          const text = [
            `GitHub PR ${status}: #${process.env.PR_NUMBER} ${process.env.PR_TITLE}`,
            `Repo: ${process.env.REPO}`,
            `Author: ${process.env.PR_AUTHOR}`,
            `Branch: ${process.env.HEAD_REF} -> ${process.env.BASE_REF}`,
            `URL: ${process.env.PR_URL}`,
          ].join("\n");

          process.stdout.write(`payload=${JSON.stringify({ text })}\n`);
          NODE

      - name: Send Slack notification
        if: ${{ env.SLACK_WEBHOOK_URL != '' }}
        env:
          PAYLOAD: ${{ steps.payload.outputs.payload }}
        run: |
          curl -sS -X POST \
            -H 'Content-Type: application/json' \
            --data "$PAYLOAD" \
            "$SLACK_WEBHOOK_URL"
```

---

## 6. GitHub Secret 配置

不要把 Slack Webhook URL 写进仓库文件。

应该配置到目标仓库的 GitHub Secret：

- 名称：`SLACK_WEBHOOK_URL`
- 值：Slack Incoming Webhook 的完整 URL

配置路径：

```text
GitHub 仓库 -> Settings -> Secrets and variables -> Actions -> New repository secret
```

---

## 7. 推荐执行流程

建议在目标仓库按下面顺序执行：

1. 新建分支，例如：`codex/pr-automation-slack-notify`
2. 添加上面三个文件
3. 本地提交 commit
4. 推送分支
5. 创建 PR
6. 检查 GitHub Actions 是否成功
7. 检查 Slack 是否收到通知

示例命令：

```bash
git checkout -b codex/pr-automation-slack-notify
git add .github scripts
git commit -m "Add PR automation and Slack notification workflow"
scripts/create_pr.sh --title "Add PR automation and Slack notification workflow" --body-file .github/pull_request_template.md
```

---

## 8. 验证方式

### 验证 GitHub 登录

```bash
gh auth status
```

预期结果：

- 已登录 GitHub
- token 有 `repo` 与 `workflow` 权限

### 验证 Workflow 是否触发

```bash
gh run list --limit 10
```

预期结果：

- 能看到 `PR Slack Notify`
- 触发事件为 `pull_request`
- 运行结果为 `success`

### 验证 Slack 是否收到消息

预期至少会在以下时机收到通知：

- PR 新建时
- PR 再次 push 新 commit 时
- PR 关闭时
- PR 合并时

---

## 9. 常见问题排查

### 1）`gh auth status` 显示 token invalid

处理方式：

```bash
gh auth login
```

如果之前登录错了账号，也可以先：

```bash
gh auth logout -h github.com -u <username>
gh auth login
```

### 2）Workflow 运行后直接失败，提示可能是 workflow file issue

优先检查：

- YAML 缩进是否正确
- 是否直接在 job 级 `if` 中不兼容地引用了 `secrets`
- `node` 脚本块是否完整

建议优先使用本手册里的工作流内容，不要自行改写触发和判断逻辑。

### 3）GitHub Actions 成功，但 Slack 没消息

优先检查：

- `SLACK_WEBHOOK_URL` 是否配置在正确仓库
- Webhook URL 是否已失效
- Slack App 是否仍然有权限向该频道发消息
- 工作流发送的目标是不是你预期的频道

### 4）创建 PR 时提示当前分支是 `main`

处理方式：

- 先切到功能分支再执行脚本

例如：

```bash
git checkout -b codex/your-feature-name
```

### 5）脚本提示工作区不干净

处理方式：

- 先提交改动
- 或先 stash

脚本这样设计是为了避免把半成品直接推上去创建 PR。

---

## 10. 可选增强

如果你想继续增强这套机制，可以考虑：

- 把 Slack 文案改成中文
- 在通知中加入 PR 描述摘要
- 在 merged 时 @ 指定负责人
- 根据分支名或路径把通知发到不同 Slack 频道
- 加上 PR reviewer、label、draft 状态等字段

---

## 11. 最小迁移清单

如果你只想快速复用，最少做这几步：

1. 复制 `scripts/create_pr.sh`
2. 复制 `.github/workflows/pr-slack-notify.yml`
3. 配置 GitHub Secret `SLACK_WEBHOOK_URL`
4. `chmod +x scripts/create_pr.sh`
5. 用脚本创建一次测试 PR
6. 确认 GitHub Actions success
7. 确认 Slack 收到消息

做到这一步，这套机制就算迁移完成了。
