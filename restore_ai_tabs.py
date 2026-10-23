#!/usr/bin/env python3
"""restore-ai-tabs — 死机/重启后，把 Claude Code 和 Codex 会话一键恢复成终端 tab。

支持 macOS + iTerm2、Windows + Windows Terminal；其他终端会打印恢复命令。

用法:
  restore-ai-tabs              # 复选框界面，选择最近 6 小时的会话
  restore-ai-tabs -H 24        # 看最近 24 小时
  restore-ai-tabs --all        # 不询问，恢复所有没在运行的会话
  restore-ai-tabs --list       # 只列出，不打开
  restore-ai-tabs --dry-run    # 打印将要执行的命令，不打开
  restore-ai-tabs --new-window # 在新窗口打开（默认加到当前窗口）
  restore-ai-tabs --check      # 放进 shell 启动脚本：终端启动后第一次开 shell 时，
                               # 如果有最近中断的会话，就提示是否恢复
  restore-ai-tabs --print-hook # 打印上面这段启动脚本
"""
import argparse
import glob
import json
import locale
import os
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata

__version__ = "0.4.0"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
HOME = os.path.expanduser("~")
STATE_FILE = os.path.join(HOME, ".cache", "restore-ai-tabs", "last-launch")
CHECK_HOURS = float(os.environ.get("RESTORE_AI_TABS_CHECK_HOURS", 3))


# ---------- 读取会话 ----------

def first_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "input_text"):
                return b.get("text", "")
    return ""


def one_line(s):
    return " ".join(s.split())


def iter_json(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def claude_sessions(since):
    out = []
    for f in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        mtime = os.path.getmtime(f)
        if mtime < since:
            continue
        sid, cwd, title = os.path.basename(f)[:-6], None, ""
        for d in iter_json(f):
            cwd = cwd or d.get("cwd")
            if not title and d.get("type") == "user" and not d.get("isMeta") and not d.get("isSidechain"):
                t = first_text((d.get("message") or {}).get("content")).strip()
                if t and not t.startswith("<"):
                    title = t
            if cwd and title:
                break
        if cwd:
            out.append(dict(tool="claude", id=sid, cwd=cwd, mtime=mtime, title=one_line(title), path=f))
    return out


def codex_sessions(since):
    out = []
    for f in glob.glob(os.path.join(HOME, ".codex", "sessions", "*", "*", "*", "rollout-*.jsonl")):
        mtime = os.path.getmtime(f)
        if mtime < since:
            continue
        sid, cwd, title = None, None, ""
        for d in iter_json(f):
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                sid, cwd = p.get("id"), p.get("cwd")
            elif not title and d.get("type") == "response_item" and p.get("role") == "user":
                t = first_text(p.get("content")).strip()
                if t and not t.startswith("<"):
                    title = t
            if sid and title:
                break
        if sid and cwd:
            out.append(dict(tool="codex", id=sid, cwd=cwd, mtime=mtime, title=one_line(title), path=f))
    return out


def find_sessions(hours):
    since = time.time() - hours * 3600
    return sorted(claude_sessions(since) + codex_sessions(since), key=lambda x: -x["mtime"])


def resume_args(it):
    if it["tool"] == "claude":
        return ["claude", "--resume", it["id"]]
    return ["codex", "resume", it["id"]]


# ---------- 判断哪些会话正在运行 ----------

def is_agent_cmdline(args):
    # 兼容直接运行（claude ...）和经 node 启动（node /.../bin/codex ...、node ...\claude-code\cli.js）
    for t in args.replace("\\", "/").split()[:3]:
        name = os.path.basename(t.strip('"')).lower()
        if name in ("claude", "claude.exe", "codex", "codex.exe") or "claude-code" in t or "/codex" in t:
            return True
    return False


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def running_processes():
    """返回 [(pid, 命令行, 工作目录或 None)]，只包含 claude / codex 进程。"""
    if IS_WIN:
        out = run(["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'claude|codex' } "
                   "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"])
        try:
            rows = json.loads(out) if out.strip() else []
        except ValueError:
            rows = []
        if isinstance(rows, dict):
            rows = [rows]
        return [(str(r.get("ProcessId")), r.get("CommandLine") or "", None)
                for r in rows if is_agent_cmdline(r.get("CommandLine") or "")]

    procs = {}
    for line in run(["ps", "-Ao", "pid=,args="]).splitlines():
        pid, _, args = line.strip().partition(" ")
        if is_agent_cmdline(args):
            procs[pid] = args
    if not procs:
        return []
    cwds, pid = {}, None
    for line in run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(procs)]).splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid:
            cwds[pid] = line[1:]
    return [(p, a, cwds.get(p)) for p, a in procs.items()]


def open_session_files():
    if IS_WIN:
        return set()
    out = run(["lsof", "-Fn", "+D", os.path.join(HOME, ".codex", "sessions")])
    return {line[1:] for line in out.splitlines() if line.startswith("n")}


def mark_running(items):
    procs = running_processes()
    opened = open_session_files()
    for it in items:
        it["running"] = it["path"] in opened or any(it["id"] in args for _, args, _ in procs)

    unmatched = [(a, cwd) for _, a, cwd in procs if "resume" not in a and "claude" in a.lower()]
    if not unmatched:
        return
    if IS_WIN:
        # Windows 拿不到进程的工作目录：把最近 2 分钟内还在写的会话当作运行中
        for it in items:
            if time.time() - it["mtime"] < 120:
                it["running"] = True
        return
    # 没带会话 ID 启动的 claude：认为它对应同目录下最近活动的那个会话
    for _, cwd in unmatched:
        same = [it for it in items if it["tool"] == "claude" and it["cwd"] == cwd]
        if same:
            max(same, key=lambda x: x["mtime"])["running"] = True


# ---------- 显示 ----------

def char_width(c):
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1


def width(s):
    return sum(char_width(c) for c in s)


def fit(s, w):
    """按显示宽度截断/补齐（中文占两格）。"""
    if width(s) <= w:
        return s + " " * (w - width(s))
    out, used = "", 0
    for c in s:
        if used + char_width(c) > w - 1:
            break
        out += c
        used += char_width(c)
    return out + "…" + " " * (w - used - 1)


def short_cwd(cwd):
    return "~" + cwd[len(HOME):] if cwd.startswith(HOME) else cwd


def ago(t):
    m = int((time.time() - t) / 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return "%d分钟前" % m
    return "%d小时前" % (m // 60) if m < 1440 else "%d天前" % (m // 1440)


def status(it):
    return "运行中" if it["running"] else ago(it["mtime"])


# ---------- 选择界面 ----------

HELP = "↑↓ 移动  空格 勾选  a 全选  n 全不选  / 搜索  回车 打开（未勾选时只开当前行）  q 退出"


def parse_choice(s, n):
    s = s.strip().lower()
    if s in ("a", "all"):
        return list(range(n))
    picked = []
    for part in s.replace(",", " ").split():
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                picked += range(int(a) - 1, int(b))
            else:
                picked.append(int(part) - 1)
        except ValueError:
            continue
    return sorted({i for i in picked if 0 <= i < n})


def simple_picker(items):
    """没有 curses 时（如 Windows 未装 windows-curses）的编号选择。"""
    for i, it in enumerate(items, 1):
        print("%2d. [%-6s] %-8s %s  %s" % (i, it["tool"], status(it), short_cwd(it["cwd"]), it["title"][:50]))
    s = input("\n输入要恢复的编号（如 1 3 5 或 1-4，a=全部，回车取消）: ")
    return [items[i] for i in parse_choice(s, len(items))]


def curses_picker(stdscr, items):
    import curses
    curses.curs_set(0)
    try:
        curses.use_default_colors()
        colors = [curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW,
                  8 if curses.COLORS > 8 else curses.COLOR_WHITE]
        for i, c in enumerate(colors, 1):
            curses.init_pair(i, c, -1)
        CYAN, GREEN, YELLOW, GREY = (curses.color_pair(i) for i in range(1, 5))
    except curses.error:
        CYAN = GREEN = YELLOW = GREY = 0

    checked = set()
    cur, top, query, searching = 0, 0, "", False

    while True:
        vis = [i for i, it in enumerate(items)
               if query.lower() in (it["cwd"] + " " + it["title"] + " " + it["tool"]).lower()]
        cur = max(0, min(cur, len(vis) - 1))
        h, w = stdscr.getmaxyx()
        rows = max(1, h - 5)
        top = min(max(top, cur - rows + 1), cur)

        stdscr.erase()
        stdscr.addnstr(0, 0, "恢复 AI 会话 · 已选 %d / %d" % (len(checked), len(items)), w - 1, curses.A_BOLD)
        stdscr.addnstr(1, 0, ("搜索: " + query + "▏") if searching or query else HELP, w - 1, GREY)

        cwd_w = min(34, max(12, w // 3))
        for row, idx in enumerate(vis[top:top + rows]):
            it, y = items[idx], row + 3
            on_cur = (top + row) == cur
            box = "◉" if idx in checked else "◯"
            tool = "claude" if it["tool"] == "claude" else "codex "
            left = "%s %s %s  %s  " % ("❯" if on_cur else " ", box, tool, fit(short_cwd(it["cwd"]), cwd_w))
            right = "  " + status(it)
            title_w = max(0, w - 1 - width(left) - width(right))
            attr = curses.A_BOLD if on_cur else 0
            if it["running"]:
                attr |= GREY
            try:
                stdscr.addstr(y, 0, left[:2], (CYAN | curses.A_BOLD) if on_cur else 0)
                stdscr.addstr(y, 2, box, GREEN if idx in checked else GREY)
                stdscr.addstr(y, 3, left[3:], attr | (YELLOW if it["tool"] == "codex" and not it["running"] else 0))
                stdscr.addstr(fit(it["title"] or "（无标题）", title_w), attr)
                stdscr.addstr(right, GREY)
            except curses.error:
                pass
        if not vis:
            stdscr.addnstr(3, 2, "没有匹配的会话", w - 3, GREY)
        stdscr.refresh()

        k = stdscr.get_wch()
        if searching:
            if k in ("\n", "\r", curses.KEY_ENTER, "\x1b"):
                searching = False
                if k == "\x1b":
                    query = ""
            elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                query = query[:-1]
            elif isinstance(k, str) and k.isprintable():
                query, cur = query + k, 0
            continue
        if k in (curses.KEY_UP, "k"):
            cur = max(0, cur - 1)
        elif k in (curses.KEY_DOWN, "j"):
            cur = min(len(vis) - 1, cur + 1)
        elif k == " " and vis:
            checked ^= {vis[cur]}
        elif k == "a":
            checked |= set(vis)
        elif k == "n":
            checked -= set(vis)
        elif k == "/":
            searching = True
        elif k in ("\n", "\r", curses.KEY_ENTER):
            # 没勾选任何会话时，回车只打开光标所在的那一个
            if not checked:
                return [items[vis[cur]]] if vis else []
            return [items[i] for i in sorted(checked)]
        elif k in ("q", "\x1b"):
            return []


def pick(items):
    try:
        import curses
    except ImportError:
        if IS_WIN:
            print("提示：pip install windows-curses 后可使用复选框界面。\n")
        return simple_picker(items)
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(curses_picker, items)


# ---------- 打开终端 tab ----------

def tab_title(it):
    return os.path.basename(it["cwd"].rstrip("/\\")) or it["cwd"]


def detect_terminal():
    if IS_MAC and (os.environ.get("TERM_PROGRAM") == "iTerm.app" or os.path.isdir("/Applications/iTerm.app")):
        return "iterm"
    if IS_WIN and shutil.which("wt"):
        return "wt"
    if IS_WIN:
        return "console"
    return "print"


def posix_command(it):
    return "cd %s && %s" % (shlex.quote(it["cwd"]), " ".join(shlex.quote(a) for a in resume_args(it)))


def windows_command(it):
    return subprocess.list2cmdline(resume_args(it))


def as_applescript(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def iterm_script(items, new_window):
    lines = ['tell application "iTerm"', "activate"]
    if new_window:
        lines.append("set w to (create window with default profile)")
    else:
        lines += ["if (count of windows) = 0 then",
                  "set w to (create window with default profile)",
                  "else",
                  "set w to current window",
                  "tell w to create tab with default profile",
                  "end if"]
    for i, it in enumerate(items):
        if i > 0:
            lines.append("tell w to create tab with default profile")
        lines += ["tell current session of w",
                  "set name to %s" % as_applescript(tab_title(it)),
                  "write text %s" % as_applescript(posix_command(it)),
                  "end tell"]
    lines.append("end tell")
    return "\n".join(lines)


def wt_args(items, new_window):
    """Windows Terminal: 一条 wt 命令里用 ; 串起多个 new-tab。用 cmd /k 运行，避免 PowerShell 执行策略拦截 npm 的 .ps1 shim。"""
    args = ["wt", "-w", "new" if new_window else "0"]
    for i, it in enumerate(items):
        if i > 0:
            args.append(";")
        args += ["new-tab", "--title", tab_title(it), "-d", it["cwd"].rstrip("\\") or it["cwd"],
                 "cmd", "/k"] + resume_args(it)
    return args


def open_tabs(items, terminal, new_window):
    if terminal == "iterm":
        subprocess.run(["osascript", "-e", iterm_script(items, new_window)], check=True)
    elif terminal == "wt":
        subprocess.run(wt_args(items, new_window), check=True)
    elif terminal == "console":
        for it in items:
            subprocess.Popen(["cmd", "/k"] + resume_args(it), cwd=it["cwd"],
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        print("当前终端不支持自动开 tab，请在新 tab 里分别运行：\n")
        for it in items:
            print("  " + posix_command(it))
        return False
    return True


def show_commands(items):
    for it in items:
        if IS_WIN:
            print("cd /d %s && %s" % (subprocess.list2cmdline([it["cwd"]]), windows_command(it)))
        else:
            print(posix_command(it))


# ---------- 终端启动时提醒 ----------

HOOKS = {
    "zsh": '[[ $TERM_PROGRAM == iTerm.app ]] && command -v restore-ai-tabs >/dev/null && restore-ai-tabs --check',
    "bash": '[[ $TERM_PROGRAM == iTerm.app ]] && command -v restore-ai-tabs >/dev/null && restore-ai-tabs --check',
    "powershell": 'if ($env:WT_SESSION -and (Get-Command restore-ai-tabs -ErrorAction SilentlyContinue)) { restore-ai-tabs --check }',
}


def terminal_launch_id():
    """当前终端进程的 "pid + 启动时间"，用来保证每次启动终端只提醒一次。"""
    if IS_WIN:
        out = run(["powershell", "-NoProfile", "-Command",
                   "Get-Process WindowsTerminal -ErrorAction SilentlyContinue | Sort-Object StartTime "
                   "| Select-Object -First 1 | ForEach-Object { \"$($_.Id) $($_.StartTime.Ticks)\" }"])
        return out.strip() or None
    for line in run(["ps", "-Ao", "pid=,lstart=,comm="]).splitlines():
        if line.rstrip().endswith("MacOS/iTerm2"):
            return line.rsplit(" ", 1)[0].strip()
    return None


def first_check_since_launch():
    launch = terminal_launch_id()
    if not launch:
        return False
    try:
        with open(STATE_FILE) as fh:
            if fh.read().strip() == launch:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write(launch)
    return True


def read_key():
    if IS_WIN:
        import msvcrt
        return msvcrt.getwch()
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def check():
    if not sys.stdin.isatty() or not first_check_since_launch():
        return
    items = find_sessions(CHECK_HOURS)
    if not items:
        return
    mark_running(items)
    stopped = [it for it in items if not it["running"]]
    if not stopped:
        return
    print("\033[36m⟲ 发现 %d 个最近 %g 小时内中断的 Claude / Codex 会话。\033[0m"
          "回车 = 选择恢复，其他键 = 跳过（之后可随时运行 restore-ai-tabs）" % (len(stopped), CHECK_HOURS))
    if read_key() not in ("\n", "\r"):
        return
    chosen = pick(items)
    if chosen and open_tabs(chosen, detect_terminal(), new_window=False):
        print("已打开 %d 个 tab。" % len(chosen))


# ---------- 入口 ----------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="restore-ai-tabs",
                                 description="一键把最近的 Claude Code / Codex 会话恢复成终端 tab")
    ap.add_argument("-H", "--hours", type=float, default=6, help="查看最近多少小时的会话（默认 6）")
    ap.add_argument("--all", action="store_true", help="不询问，恢复所有没在运行的会话")
    ap.add_argument("--list", action="store_true", help="只列出会话，不打开")
    ap.add_argument("--dry-run", action="store_true", help="打印将要执行的命令，不打开")
    ap.add_argument("--new-window", action="store_true", help="在新窗口打开（默认加到当前窗口）")
    ap.add_argument("--terminal", choices=["auto", "iterm", "wt", "console", "print"], default="auto",
                    help="用哪个终端打开（默认自动识别）")
    ap.add_argument("--check", action="store_true", help="终端启动后首次调用时，如有中断的会话就提示恢复")
    ap.add_argument("--print-hook", choices=sorted(HOOKS), help="打印放进 shell 启动脚本的那一行")
    ap.add_argument("-V", "--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    if args.print_hook:
        print(HOOKS[args.print_hook])
        return
    if args.check:
        try:
            check()
        except (Exception, KeyboardInterrupt):
            pass  # 提醒失败不能影响正常开 shell
        return

    items = find_sessions(args.hours)
    if not items:
        print("最近 %g 小时没有 Claude / Codex 会话，可以用 -H 24 看更久的。" % args.hours)
        return
    mark_running(items)

    if args.list:
        for it in items:
            print("%-6s  %-8s  %s  %s" % (it["tool"], status(it), short_cwd(it["cwd"]), it["title"][:60]))
        return
    if args.all:
        chosen = [it for it in items if not it["running"]]
    elif not sys.stdin.isatty():
        sys.exit("需要在终端里交互运行，或加 --all / --list")
    else:
        chosen = pick(items)

    if not chosen:
        print("没有选择任何会话。")
        return
    if args.dry_run:
        show_commands(chosen)
        return
    terminal = detect_terminal() if args.terminal == "auto" else args.terminal
    if open_tabs(chosen, terminal, args.new_window):
        print("已打开 %d 个 tab。" % len(chosen))


if __name__ == "__main__":
    main()
