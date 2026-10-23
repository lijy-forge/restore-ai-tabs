# restore-ai-tabs

> 死机 / 重启 / iTerm 崩溃之后，一条命令把所有 Claude Code 和 Codex 会话恢复成 iTerm2 的 tab。

[English](#english)

开十几个 tab 同时跑 Claude Code 和 Codex，电脑一卡死，全没了。重启后要一个个 `cd` 进目录、运行 `claude --resume`、在列表里找半天是哪个会话……

`restore-ai-tabs` 直接读取 Claude Code 和 Codex 本地保存的会话记录，用一个复选框界面让你勾选（默认不勾选；直接回车只打开光标所在的一个），然后在当前 iTerm 窗口里一次性打开，每个 tab 自动进入对应目录并 resume 到死机前的对话。

```
恢复 AI 会话 · 已选 3 / 11
↑↓ 移动  空格 勾选  a 全选  n 全不选  / 搜索  回车 打开（未勾选时只开当前行）  q 退出

❯ ◯ claude ~/code/blog            帮我把文章列表改成分页加载…         运行中
  ◯ codex  ~/code/todo-app        把这个项目部署到 Cloudflare 上…     运行中
  ◉ claude ~/code/api-server      登录接口偶尔返回 500，查一下原因…   12分钟前
  ◉ claude ~/research/rl-exp      跑一下第三组对照实验，记录结果…     15分钟前
  ◉ codex  ~/code/mobile-h5       适配一下 iPhone 小屏的布局…         20分钟前
```

## 特点

- **事后补救，不用提前设置**：不需要事先保存窗口布局，死机之后再装也能用。数据来自 Claude Code / Codex 自己的会话文件。
- **自动识别"正在运行"的会话**：已经打开的会话会标灰、默认不勾选，避免同一个会话开两份。
- **Claude Code 和 Codex 一起恢复**，按最近活动时间排序，显示目录和你的第一句话，一眼认出是哪个。
- **加到当前窗口**的新 tab，tab 名自动设为项目目录名。
- **零依赖**：单个 Python 文件，用 macOS 自带的 Python 3 就能跑。
- **中文友好**：中文路径、中文对话标题对齐正常。

## 安装

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/lijy-forge/restore-ai-tabs/main/restore-ai-tabs -o ~/.local/bin/restore-ai-tabs
chmod +x ~/.local/bin/restore-ai-tabs
```

确保 `~/.local/bin` 在 `PATH` 里。第一次运行时 macOS 会询问是否允许控制 iTerm，选"好"。

## 用法

```bash
restore-ai-tabs              # 复选框界面，最近 6 小时的会话
restore-ai-tabs -H 24        # 最近 24 小时
restore-ai-tabs --all        # 不询问，恢复所有没在运行的会话
restore-ai-tabs --list       # 只列出
restore-ai-tabs --dry-run    # 只打印将要执行的命令
restore-ai-tabs --new-window # 在新窗口打开
```

### 忘了命令？让它开机自己提醒

在 `~/.zshrc` 末尾加一行：

```bash
[[ $TERM_PROGRAM == iTerm.app ]] && command -v restore-ai-tabs >/dev/null && restore-ai-tabs --check
```

之后每次 iTerm 启动（比如死机重启后），第一个打开的 shell 会检查最近 3 小时有没有"中断了、还没恢复"的会话。有的话会提示：

```
⟲ 发现 5 个最近 3 小时内中断的 Claude / Codex 会话。回车 = 选择恢复，其他键 = 跳过
```

按回车进入复选框界面，按其他键跳过。每次 iTerm 启动只提醒一次，没有中断的会话就完全不出声，检查耗时不到 0.1 秒。时间范围可以用环境变量 `RESTORE_AI_TABS_CHECK_HOURS` 调整。

| 按键 | 作用 |
|---|---|
| `↑` `↓` / `j` `k` | 移动 |
| `空格` | 勾选 / 取消 |
| `a` / `n` | 全选 / 全不选（作用于当前搜索结果） |
| `/` | 搜索目录或标题，`Esc` 清空 |
| `回车` | 打开选中的会话 |
| `q` | 退出 |

## 原理

| 工具 | 会话文件 | 恢复命令 |
|---|---|---|
| Claude Code | `~/.claude/projects/*/<id>.jsonl` | `claude --resume <id>` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `codex resume <id>` |

"运行中"的判断：会话文件正被进程打开（Codex）、进程参数里带着会话 ID，或者某个 `claude` 进程的工作目录与会话目录一致且该会话是这个目录下最近活动的。

脚本只读本地文件，不联网，不修改任何会话数据。

## 要求

- macOS + [iTerm2](https://iterm2.com)
- Python 3.8+（macOS 自带）
- `claude` / `codex` 命令在 `PATH` 里

## 计划

- [ ] 支持 Terminal.app、Warp、tmux
- [ ] 支持 Linux
- [ ] 显示每个会话的最后一条消息作为预览
- [ ] 支持更多 AI CLI（Gemini CLI、Aider……）

欢迎提 issue 和 PR。

---

## English

**restore-ai-tabs** brings back all your Claude Code and Codex sessions as iTerm2 tabs after a crash, reboot or accidental quit — no setup needed beforehand.

It reads the session logs that Claude Code (`~/.claude/projects`) and Codex (`~/.codex/sessions`) already keep, shows a checkbox picker of recent sessions (sessions that are still running are greyed out and unchecked), and opens each selected one in a new tab of the current iTerm window with `claude --resume <id>` / `codex resume <id>` in the right directory.

Single Python file, no dependencies. See the usage section above; flags are `-H/--hours`, `--all`, `--list`, `--dry-run`, `--new-window`.

## License

MIT
