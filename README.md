# restore-ai-tabs

[![CI](https://github.com/lijy-forge/restore-ai-tabs/actions/workflows/ci.yml/badge.svg)](https://github.com/lijy-forge/restore-ai-tabs/actions/workflows/ci.yml)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> 死机、重启或终端崩溃之后，一条命令把所有 Claude Code 和 Codex 会话恢复成终端 tab。支持 **macOS + iTerm2** 和 **Windows + Windows Terminal**。

[English](#english)

开十几个 tab 同时跑 Claude Code 和 Codex，电脑一卡死，全没了。重启后要一个个 `cd` 进目录、运行 `claude --resume`、在列表里找半天是哪个会话……

`restore-ai-tabs` 直接读取 Claude Code 和 Codex 本地保存的会话记录，用复选框界面让你勾选，然后在当前终端窗口里一次性打开，每个 tab 自动进入对应目录，并 resume 到死机前的对话。

```
恢复 AI 会话 · 已选 3 / 11
↑↓ 移动  空格 勾选  a 全选  n 全不选  / 搜索  回车 打开（未勾选时只开当前行）  q 退出

❯ ◯ claude ~/code/blog            帮我把文章列表改成分页加载…         运行中
  ◯ codex  ~/code/todo-app        把这个项目部署到 Cloudflare 上…     运行中
  ◉ claude ~/code/api-server      登录接口偶尔返回 500，查一下原因…   12分钟前
  ◉ claude ~/research/rl-exp      跑一下第三组对照实验，记录结果…     15分钟前
  ◉ codex  ~/code/mobile-h5       适配一下 iPhone 小屏的布局…         20分钟前
```

## 为什么做这个

Windows Terminal、iTerm2 自带的"恢复窗口"只恢复布局和目录，不会恢复里面的 AI 对话。[cmux](https://cmux.com/docs/session-restore) 能自动恢复，但需要换用它的终端，还要提前装好钩子。

`restore-ai-tabs` 面向**不想换终端**的人：继续用你的 iTerm2 / Windows Terminal，**死机之后再装也来得及**，因为它读的是 Claude Code / Codex 本来就保存在本地的会话记录。

## 特点

- **事后补救，不用提前设置**：不需要事先保存窗口布局。
- **自动识别"正在运行"的会话**：已经开着的会话标灰，避免同一个会话开两份。
- **Claude Code 和 Codex 一起恢复**：按最近活动时间排序，显示目录和你的第一句话，一眼就能认出是哪个。
- **加到当前窗口**：新开的 tab 名自动设为项目目录名。
- **按对话内容搜索**：`restore-ai-tabs search Redis 限流`，在所有历史会话里找"那个聊过 Redis 限流的会话"，勾选后直接恢复。
- **启动时自动提醒**：终端重新打开时，如果有中断的会话，会问你要不要恢复，不用记命令。
- **进阶（macOS + iTerm2）**：精确还原死机前的窗口、tab 和分屏布局；左侧 tab 栏实时显示每个 Agent 的状态：🔵 运行中、🟡 等你确认、✅ 已完成。
- **单文件、几乎零依赖**：macOS 上只用系统自带的 Python；Windows 只多一个 `windows-curses`，用于复选框界面。
- **中文友好**：中文路径、中文标题都能正常对齐。

## 安装

**推荐（macOS / Windows 通用）**：

```bash
pipx install git+https://github.com/lijy-forge/restore-ai-tabs
```

没有 pipx 的话，用 `pip install --user git+https://github.com/lijy-forge/restore-ai-tabs` 也可以。

**macOS 不想装 pip 包**：直接下载单个文件。

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/lijy-forge/restore-ai-tabs/main/restore_ai_tabs.py -o ~/.local/bin/restore-ai-tabs
chmod +x ~/.local/bin/restore-ai-tabs
```

macOS 第一次运行时，系统会询问是否允许控制 iTerm，选"好"。

## 用法

```bash
restore-ai-tabs              # 复选框界面，最近 6 小时的会话
restore-ai-tabs -H 24        # 最近 24 小时
restore-ai-tabs --all        # 不询问，恢复所有没在运行的会话
restore-ai-tabs --list       # 只列出
restore-ai-tabs --dry-run    # 只打印将要执行的命令
restore-ai-tabs --new-window # 在新窗口打开
restore-ai-tabs search Redis 限流  # 按对话内容搜索全部历史会话，多个词需同时出现
```

### 全文搜索

`search` 会搜你说过的话和 AI 的回答（不含工具输出、文件内容），结果按时间排序，标题位置显示命中的那一句，勾选后照常恢复。可以加 `-H 72` 只搜最近 3 天，加 `--list` 只列出。复选框界面里按 `/` 搜索时，同样会搜对话内容。

为了快，对话文字会缓存在 `~/.cache/restore-ai-tabs/search-index.json`：

- 大小约等于对话文字本身（实测 68 个会话 ≈ 7MB，而原始记录有 590MB）
- 第一次建立需要几秒，之后只读取新增的内容，每次搜索约 0.1 秒
- 没有常驻进程，不搜索时不占内存；Claude Code 清理掉的会话，缓存里也会同步删除
- 缓存是对话文字的明文副本，删掉也没关系，下次搜索会自动重建

| 按键 | 作用 |
|---|---|
| `↑` `↓` / `j` `k` | 移动 |
| `空格` | 勾选 / 取消 |
| `a` / `n` | 全选 / 全不选（作用于当前搜索结果） |
| `/` | 搜索目录、标题和对话内容，`Esc` 清空 |
| `回车` | 打开勾选的会话；一个都没勾时，只打开光标所在的那个 |
| `q` | 退出 |

### 忘了命令？让终端启动时自己提醒

把下面这行加进 shell 启动脚本：

```bash
# macOS（zsh）
restore-ai-tabs --print-hook zsh >> ~/.zshrc
```

```powershell
# Windows（PowerShell）
restore-ai-tabs --print-hook powershell >> $PROFILE
```

之后每次终端启动（比如死机重启后），第一个 shell 会检查最近 3 小时内有没有中断了、还没恢复的会话。有的话会提示：

```
⟲ 发现 5 个最近 3 小时内中断的 Claude / Codex 会话。回车 = 选择恢复，其他键 = 跳过
```

每次终端启动只提醒一次，没有中断的会话时不会有任何输出。检查的时间范围可以用环境变量 `RESTORE_AI_TABS_CHECK_HOURS` 调整。

## 进阶：精确恢复布局 + tab 状态（macOS + iTerm2）

基础功能只能"推测"死机前开着哪些会话。运行一次 `setup` 后，工具会在后台持续记录，恢复时能做到和死机前完全一样：

```bash
restore-ai-tabs setup
```

然后在 iTerm2 里打开 Python API：Settings → General → Magic → 勾选 **Enable Python API**（在 Settings 右上角搜索 `Python` 能直接找到）。第一次运行后台脚本时，iTerm2 会提示下载它自带的 Python 运行环境，选下载即可。

**1. 精确恢复布局**

后台脚本随 iTerm2 启动，布局一变化（开 tab、分屏、关窗格）就保存快照：每个窗口、每个 tab、分屏方向，以及每个窗格的目录和里面跑的会话。iTerm2 下次启动时会提示：

```
⟲ 上次 iTerm 关闭前（3分钟前）有 2 个窗口、10 个 tab、11 个窗格，其中 10 个 AI 会话。
回车 = 按原布局恢复，s = 逐个选择会话，其他键 = 跳过
```

按回车，就会按原来的窗口、tab 顺序和分屏结构重建，并在每个窗格里 resume 对应的会话；没跑 AI 的窗格会回到原来的目录。也可以手动运行 `restore-ai-tabs --layout`，或用 `restore-ai-tabs --layout --dry-run` 先看看会恢复成什么样。

**2. tab 状态**

`setup` 会在 Claude Code 里装上钩子，后台脚本据此给 tab 改标题和颜色：

| 状态 | 什么时候 |
|---|---|
| 🔵 运行中 | 你发出消息后、工具调用进行中 |
| 🟡 等你确认 | Claude 请求权限，需要你点确认 |
| ✅ 已完成 | 这一轮回答结束 |

建议把 tab 栏放到左边（Settings → Appearance → General → Tab bar location 选 Left），效果类似 cmux 的侧边栏。tab 标题会显示成"状态 + 项目目录名"。

`restore-ai-tabs uninstall` 可以一键移除钩子和后台脚本，你在 Claude Code 里自己配置的其他钩子不受影响。

## 支持的平台

| 系统 | 终端 | 打开方式 |
|---|---|---|
| macOS | iTerm2 | AppleScript，在当前窗口新建 tab |
| Windows | Windows Terminal | `wt -w 0 new-tab ...`，一条命令打开所有 tab |
| Windows | 没有 Windows Terminal | 每个会话开一个新的控制台窗口 |
| 其他 | — | 打印恢复命令，自己粘贴运行 |

可以用 `--terminal iterm|wt|console|print` 手动指定。

## 原理

| 工具 | 会话文件 | 恢复命令 |
|---|---|---|
| Claude Code | `~/.claude/projects/*/<id>.jsonl` | `claude --resume <id>` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `codex resume <id>` |

**怎么判断"正在运行"：**

- macOS：满足以下任一条件就算运行中：
  - Claude Code 为每个运行中的进程写了 `~/.claude/sessions/<pid>.json`，里面就是当前会话 ID（最准确）
  - 会话文件正被某个进程打开着（Codex）
  - 某个进程的启动参数里带着这个会话的 ID（`claude --resume <id>`、`codex resume <id>`）
  - 以上都没有时：某个 `claude` 进程的工作目录和会话目录一致，并且这个会话是该目录下最近活动的
- Windows：拿不到进程的工作目录，所以采用：
  - 进程启动参数里带着会话 ID
  - 有 `claude` 进程在运行时，最近 2 分钟内还在写入的会话也算运行中

工具只读取本地文件，不联网，不修改任何会话数据。

## 开发

```bash
python -m unittest discover -s tests -v
```

CI 会在 macOS、Windows、Ubuntu 上，分别用 Python 3.9 和 3.12 跑完整测试。

## 计划

- [x] 死机前快照：精确还原窗口、tab、分屏和会话（macOS + iTerm2）
- [x] tab 状态：运行中 / 等你确认 / 已完成
- [ ] Codex 的 tab 状态
- [ ] 还原分屏比例（目前按平均分）
- [ ] Windows Terminal 的布局恢复
- [ ] 支持 Terminal.app、Warp、tmux
- [x] 按对话内容全文搜索
- [ ] 显示每个会话的最后一条消息作为预览
- [ ] 支持更多 AI CLI（Gemini CLI、Aider……）

欢迎提 issue 和 PR。

---

## English

**restore-ai-tabs** brings back your Claude Code and Codex sessions as terminal tabs after a crash, reboot or accidental quit — on **macOS + iTerm2** and **Windows + Windows Terminal**, with no setup needed beforehand.

Terminal "restore windows" features bring back layouts but not the AI conversations inside them, and tools like cmux require switching terminals. This tool reads the session logs that Claude Code (`~/.claude/projects`) and Codex (`~/.codex/sessions`) already keep, shows a checkbox picker of recent sessions (still-running ones are greyed out), and opens each selected session in a new tab of your current window, running `claude --resume <id>` / `codex resume <id>` in the right directory.

```bash
pipx install git+https://github.com/lijy-forge/restore-ai-tabs
restore-ai-tabs                              # pick sessions to restore
restore-ai-tabs --print-hook zsh >> ~/.zshrc # prompt automatically after a crash
restore-ai-tabs search redis rate limit      # full-text search over all past conversations
```

On macOS + iTerm2, `restore-ai-tabs setup` adds an iTerm2 background script and Claude Code hooks: your exact window / tab / split layout is snapshotted on every change and rebuilt on the next launch, and each tab shows the agent's state (🔵 running, 🟡 needs approval, ✅ done) — a cmux-style sidebar when the tab bar is on the left.

Flags: `-H/--hours`, `-s/--search`, `--all`, `--list`, `--dry-run`, `--new-window`, `--terminal`, `--layout`, `--sessions`, `--check`, `--print-hook`. Commands: `setup`, `uninstall`, `search`.

## License

MIT
