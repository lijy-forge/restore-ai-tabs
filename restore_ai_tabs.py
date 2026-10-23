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

__version__ = "0.5.0"
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
    live = {} if IS_WIN else live_claude_sessions()
    live_ids = {a["id"] for a in live.values()}
    for it in items:
        it["running"] = (it["path"] in opened or it["id"] in live_ids
                         or any(it["id"] in args for _, args, _ in procs))

    # 剩下的 claude 进程（没有 ~/.claude/sessions 记录、也没带会话 ID）只能按目录推测
    unmatched = [(a, cwd) for pid, a, cwd in procs
                 if pid not in live and "resume" not in a and "claude" in a.lower()]
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
    snap = previous_snapshot() if IS_MAC else None
    if snap and snapshot_stats(snap)[3]:
        key = layout_prompt(snap)
        mark_consumed(snap)
        if key in ("\n", "\r"):
            restore_layout(snap)
        elif key.lower() == "s":
            items = find_sessions(24)
            mark_running(items)
            chosen = pick(items) if items else []
            if chosen and open_tabs(chosen, detect_terminal(), new_window=False):
                print("已打开 %d 个 tab。" % len(chosen))
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


# ---------- 运行中的 AI 会话 → 所在终端 ----------

STATE_DIR = os.path.join(HOME, ".local", "state", "restore-ai-tabs")
SNAPSHOT_DIR = os.path.join(STATE_DIR, "snapshots")
STATUS_DIR = os.path.join(STATE_DIR, "status")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def live_claude_sessions():
    """Claude Code 会为每个运行中的进程写 ~/.claude/sessions/<pid>.json，里面有当前会话 ID。"""
    out = {}
    for f in glob.glob(os.path.join(HOME, ".claude", "sessions", "*.json")):
        d = read_json(f) or {}
        pid, sid = d.get("pid"), d.get("sessionId")
        if not pid or not sid:
            continue
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            continue
        out[str(pid)] = {"tool": "claude", "id": sid, "cwd": d.get("cwd", "")}
    return out


def agents_by_tty():
    """{"/dev/ttys003": {"tool", "id", "cwd"}}：每个终端里正在跑的 Claude / Codex 会话。"""
    if IS_WIN:
        return {}
    import re
    ttys, codex, by_args = {}, [], []
    for line in run(["ps", "-Ao", "pid=,tty=,args="]).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] not in ("?", "??"):
            ttys[parts[0]] = "/dev/" + parts[1]
            if not is_agent_cmdline(parts[2]):
                continue
            tool = "claude" if "claude" in parts[2].lower() else "codex"
            if tool == "codex":
                codex.append(parts[0])
            m = re.search(r"resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", parts[2])
            if m:
                by_args.append((ttys[parts[0]], {"tool": tool, "id": m.group(1), "cwd": ""}))

    found = {}
    for pid, agent in live_claude_sessions().items():
        if pid in ttys:
            found.setdefault(ttys[pid], agent)
    # 用 `claude --resume <id>` / `codex resume <id>` 启动的进程，命令行里就有会话 ID
    for tty, agent in by_args:
        found.setdefault(tty, agent)
    if codex:
        pid = None
        for line in run(["lsof", "-a", "-p", ",".join(codex), "-Fn"]).splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("n") and "/.codex/sessions/" in line and line.endswith(".jsonl") and pid in ttys:
                meta = next(iter_json(line[1:]), {}).get("payload") or {}
                if meta.get("id"):
                    found.setdefault(ttys[pid], {"tool": "codex", "id": meta["id"], "cwd": meta.get("cwd", "")})
    return found


# ---------- 布局快照 ----------

def save_snapshot(launch, snapshot):
    import hashlib
    name = hashlib.sha1(launch.encode()).hexdigest()[:12] + ".json"
    write_json(os.path.join(SNAPSHOT_DIR, name), dict(snapshot, launch=launch, saved_at=time.time()))
    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json")), key=os.path.getmtime)
    for old in files[:-10]:
        os.remove(old)


def iter_panes(node):
    if "pane" in node:
        yield node["pane"]
    else:
        for c in node["children"]:
            for p in iter_panes(c):
                yield p


def snapshot_stats(snap):
    tabs = [t for w in snap["windows"] for t in w["tabs"]]
    panes = [p for t in tabs for p in iter_panes(t["root"])]
    return len(snap["windows"]), len(tabs), len(panes), sum(1 for p in panes if p.get("agent"))


def previous_snapshot(include_consumed=False):
    """上一次 iTerm 运行期间最后保存的布局（当前这次运行的不算）。"""
    current = terminal_launch_id()
    consumed = read_json(os.path.join(STATE_DIR, "consumed.json"), {})
    for f in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json")), key=os.path.getmtime, reverse=True):
        snap = read_json(f)
        if not snap or snap.get("launch") == current:
            continue
        if not include_consumed and consumed.get(os.path.basename(f)) == snap.get("saved_at"):
            return None
        snap["_file"] = os.path.basename(f)
        return snap
    return None


def mark_consumed(snap):
    path = os.path.join(STATE_DIR, "consumed.json")
    consumed = read_json(path, {})
    consumed[snap["_file"]] = snap.get("saved_at")
    write_json(path, consumed)


def pane_command(pane, running_ids):
    agent = pane.get("agent")
    cd = "cd %s" % shlex.quote(pane["cwd"]) if pane.get("cwd") else "cd"
    if agent and agent["id"] not in running_ids:
        return cd + " && " + " ".join(shlex.quote(a) for a in resume_args(agent))
    return cd


def layout_script(snap, running_ids):
    """按快照重建窗口 / tab / 分屏，并在每个窗格里恢复会话。分屏比例按平均分。"""
    lines = ['tell application "iTerm"', "activate"]
    counter = [0]

    def new_var():
        counter[0] += 1
        return "s%d" % counter[0]

    def build(node, var):
        if "pane" in node:
            lines.append("tell %s to write text %s" % (var, as_applescript(pane_command(node["pane"], running_ids))))
            return
        verb = "vertically" if node["split"] == "vertical" else "horizontally"
        vars_ = [var]
        for _ in node["children"][1:]:
            nv = new_var()
            lines.append("tell %s to set %s to (split %s with default profile)" % (vars_[-1], nv, verb))
            vars_.append(nv)
        for child, v in zip(node["children"], vars_):
            build(child, v)

    for wi, win in enumerate(snap["windows"]):
        w = "w%d" % wi
        for ti, tab in enumerate(win["tabs"]):
            if ti == 0:
                lines.append("set %s to (create window with default profile)" % w)
            else:
                lines.append("tell %s to create tab with default profile" % w)
            var = new_var()
            lines.append("set %s to current session of %s" % (var, w))
            build(tab["root"], var)
    lines.append("end tell")
    return "\n".join(lines)


def describe_layout(snap, running_ids):
    out = []

    def walk(node, depth):
        pad = "    " * depth
        if "pane" in node:
            p = node["pane"]
            a = p.get("agent")
            tag = ("%s %s" % (a["tool"], "（已在运行，只进入目录）" if a["id"] in running_ids else "resume")) if a else "终端"
            out.append("%s- [%s] %s" % (pad, tag, short_cwd(p.get("cwd") or "~")))
        else:
            out.append("%s%s分屏:" % (pad, "左右" if node["split"] == "vertical" else "上下"))
            for c in node["children"]:
                walk(c, depth + 1)

    for wi, win in enumerate(snap["windows"], 1):
        out.append("窗口 %d" % wi)
        for ti, tab in enumerate(win["tabs"], 1):
            out.append("  tab %d" % ti)
            walk(tab["root"], 2)
    return "\n".join(out)


def restore_layout(snap, dry_run=False):
    running_ids = {a["id"] for a in agents_by_tty().values()}
    if dry_run:
        print(describe_layout(snap, running_ids))
        return
    subprocess.run(["osascript", "-e", layout_script(snap, running_ids)], check=True)
    mark_consumed(snap)
    w, t, p, a = snapshot_stats(snap)
    print("已按原布局恢复 %d 个窗口、%d 个 tab、%d 个窗格（%d 个 AI 会话）。" % (w, t, p, a))


def layout_prompt(snap):
    w, t, p, a = snapshot_stats(snap)
    age = ago(snap.get("saved_at", time.time()))
    print("\033[36m⟲ 上次 iTerm 关闭前（%s）有 %d 个窗口、%d 个 tab、%d 个窗格，其中 %d 个 AI 会话。\033[0m"
          % (age, w, t, p, a))
    print("回车 = 按原布局恢复，s = 逐个选择会话，其他键 = 跳过")
    return read_key()


# ---------- Claude Code 钩子：上报状态 ----------

def hook():
    """由 Claude Code 钩子调用，从 stdin 读事件，把会话状态写到 STATUS_DIR。绝不能报错或输出内容。"""
    try:
        d = json.loads(sys.stdin.read() or "{}")
        sid, event = d.get("session_id"), d.get("hook_event_name")
        if not sid:
            return
        path = os.path.join(STATUS_DIR, sid + ".json")
        if event in ("UserPromptSubmit", "PostToolUse"):
            state = "running"
        elif event == "Notification":
            # 空闲提醒（"等你输入"）不算需要确认，保持原状态
            if d.get("notification_type") == "idle_prompt" or "waiting for your input" in d.get("message", ""):
                return
            state = "waiting"
        elif event == "Stop":
            state = "done"
        elif event in ("SessionEnd", "SessionStart"):
            if os.path.exists(path):
                os.remove(path)
            return
        else:
            return
        write_json(path, {"state": state, "ts": time.time()})
    except Exception:
        pass


def read_statuses():
    out = {}
    for f in glob.glob(os.path.join(STATUS_DIR, "*.json")):
        d = read_json(f)
        if d and time.time() - d.get("ts", 0) < 7 * 86400:
            out[os.path.basename(f)[:-5]] = d.get("state")
    return out


# ---------- setup / uninstall ----------

HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "Stop", "SessionEnd"]
HOOK_MARK = "restore_ai_tabs"
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
AUTOLAUNCH = os.path.join(HOME, "Library", "Application Support", "iTerm2", "Scripts", "AutoLaunch",
                          "restore_ai_tabs_autolaunch.py")


def module_dir():
    return os.path.dirname(os.path.realpath(__file__))


def hook_command():
    return "%s %s hook" % (shlex.quote(sys.executable),
                           shlex.quote(os.path.join(module_dir(), "restore_ai_tabs.py")))


def install_claude_hooks():
    settings = read_json(CLAUDE_SETTINGS, {})
    if os.path.exists(CLAUDE_SETTINGS):
        shutil.copy(CLAUDE_SETTINGS, CLAUDE_SETTINGS + ".bak-restore-ai-tabs")
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = [g for g in hooks.get(event, []) if HOOK_MARK not in json.dumps(g)]
        entry = {"hooks": [{"type": "command", "command": hook_command(), "timeout": 5}]}
        if event == "PostToolUse":
            entry["matcher"] = "*"
        hooks[event] = groups + [entry]
    write_json(CLAUDE_SETTINGS, settings)


def remove_claude_hooks():
    settings = read_json(CLAUDE_SETTINGS)
    if not settings or "hooks" not in settings:
        return
    for event in list(settings["hooks"]):
        groups = [g for g in settings["hooks"][event] if HOOK_MARK not in json.dumps(g)]
        if groups:
            settings["hooks"][event] = groups
        else:
            del settings["hooks"][event]
    if not settings["hooks"]:
        del settings["hooks"]
    write_json(CLAUDE_SETTINGS, settings)


def install_autolaunch():
    os.makedirs(os.path.dirname(AUTOLAUNCH), exist_ok=True)
    with open(AUTOLAUNCH, "w") as fh:
        fh.write("# 由 restore-ai-tabs setup 安装，restore-ai-tabs uninstall 会删除\n"
                 "import sys\n"
                 "sys.path.append(%r)\n"
                 "import restore_ai_tabs_iterm\n"
                 "restore_ai_tabs_iterm.run()\n" % module_dir())


def setup():
    if not IS_MAC:
        sys.exit("setup 目前只支持 macOS + iTerm2。")
    install_claude_hooks()
    print("✓ 已在 Claude Code 里安装状态钩子（%s，原文件备份为 .bak-restore-ai-tabs）" % short_cwd(CLAUDE_SETTINGS))
    install_autolaunch()
    print("✓ 已安装 iTerm2 后台脚本（%s）" % short_cwd(AUTOLAUNCH))
    if run(["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"]).strip() != "1":
        print("\n! 还需要打开 iTerm2 的 Python API：Settings → General → Magic → 勾选 Enable Python API")
    print("\n后台脚本会在下次启动 iTerm2 时自动运行；想立即启动：iTerm2 菜单 Scripts → AutoLaunch → "
          "restore_ai_tabs_autolaunch.py。第一次运行时 iTerm2 会提示下载 Python 运行环境，选下载即可。")
    print("\n建议：Settings → Appearance → General → Tab bar location 选 Left，状态会显示在左侧 tab 栏。")
    print("启动时自动提醒恢复：restore-ai-tabs --print-hook zsh >> ~/.zshrc")


def uninstall():
    remove_claude_hooks()
    if os.path.exists(AUTOLAUNCH):
        os.remove(AUTOLAUNCH)
    print("已移除 Claude Code 钩子和 iTerm2 后台脚本（快照和状态数据在 %s，可手动删除）。" % short_cwd(STATE_DIR))


# ---------- 入口 ----------

COMMANDS = {"setup": "安装 Claude Code 状态钩子和 iTerm2 后台脚本（布局快照 + tab 状态）",
            "uninstall": "移除 setup 安装的内容",
            "hook": "（供 Claude Code 钩子调用）"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in COMMANDS:
        {"setup": setup, "uninstall": uninstall, "hook": hook}[argv[0]]()
        return

    ap = argparse.ArgumentParser(
        prog="restore-ai-tabs",
        description="一键把最近的 Claude Code / Codex 会话恢复成终端 tab",
        epilog="子命令：\n" + "\n".join("  %-10s %s" % kv for kv in COMMANDS.items() if kv[0] != "hook"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-H", "--hours", type=float, default=6, help="查看最近多少小时的会话（默认 6）")
    ap.add_argument("--all", action="store_true", help="不询问，恢复所有没在运行的会话")
    ap.add_argument("--list", action="store_true", help="只列出会话，不打开")
    ap.add_argument("--dry-run", action="store_true", help="打印将要执行的命令，不打开")
    ap.add_argument("--new-window", action="store_true", help="在新窗口打开（默认加到当前窗口）")
    ap.add_argument("--terminal", choices=["auto", "iterm", "wt", "console", "print"], default="auto",
                    help="用哪个终端打开（默认自动识别）")
    ap.add_argument("--layout", action="store_true", help="按上次保存的布局恢复（需要先运行 setup）")
    ap.add_argument("--sessions", action="store_true", help="不管有没有布局快照，都逐个选择会话")
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

    if IS_MAC and not (args.sessions or args.list or args.all):
        snap = previous_snapshot(include_consumed=args.layout)
        if args.layout and not snap:
            sys.exit("没有找到布局快照：先运行 restore-ai-tabs setup，之后的布局才会被记录。")
        if snap and (args.layout or args.dry_run):
            restore_layout(snap, dry_run=args.dry_run)
            return
        if snap and sys.stdin.isatty():
            key = layout_prompt(snap)
            if key in ("\n", "\r"):
                restore_layout(snap)
                return
            mark_consumed(snap)
            if key.lower() != "s":
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
