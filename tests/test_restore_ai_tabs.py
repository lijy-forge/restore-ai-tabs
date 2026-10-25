import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import restore_ai_tabs as rat  # noqa: E402

CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
CODEX_ID = "019a0000-aaaa-7bbb-8ccc-dddddddddddd"


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def make_home(home, project):
    write_jsonl(os.path.join(home, ".claude", "projects", "-proj", CLAUDE_ID + ".jsonl"), [
        {"type": "mode", "sessionId": CLAUDE_ID},
        {"type": "user", "cwd": project, "isMeta": True, "message": {"content": "<command-name>/init</command-name>"}},
        {"type": "user", "cwd": project, "message": {"role": "user", "content": "修复登录接口偶尔返回 500 的问题"}},
    ])
    write_jsonl(os.path.join(home, ".codex", "sessions", "2026", "10", "23", "rollout-x-%s.jsonl" % CODEX_ID), [
        {"type": "session_meta", "payload": {"id": CODEX_ID, "cwd": project}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "<environment_context>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "Deploy to Cloudflare"}]}},
    ])
    old = os.path.join(home, ".claude", "projects", "-proj", "old-session.jsonl")
    write_jsonl(old, [{"type": "user", "cwd": project, "message": {"content": "很久以前的会话"}}])
    t = time.time() - 30 * 86400
    os.utime(old, (t, t))


class SessionParsingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.project = os.path.join(self.home, "my project", "中文目录")
        make_home(self.home, self.project)
        self._old_home, rat.HOME = rat.HOME, self.home

    def tearDown(self):
        rat.HOME = self._old_home
        self.tmp.cleanup()

    def test_finds_recent_claude_and_codex_sessions(self):
        items = rat.find_sessions(hours=6)
        self.assertEqual(sorted(it["tool"] for it in items), ["claude", "codex"])
        by_tool = {it["tool"]: it for it in items}
        self.assertEqual(by_tool["claude"]["id"], CLAUDE_ID)
        self.assertEqual(by_tool["claude"]["cwd"], self.project)
        self.assertEqual(by_tool["codex"]["id"], CODEX_ID)

    def test_title_skips_system_messages(self):
        by_tool = {it["tool"]: it for it in rat.find_sessions(hours=6)}
        self.assertEqual(by_tool["claude"]["title"], "修复登录接口偶尔返回 500 的问题")
        self.assertEqual(by_tool["codex"]["title"], "Deploy to Cloudflare")

    def test_old_sessions_need_larger_window(self):
        self.assertEqual(len(rat.find_sessions(hours=6)), 2)
        self.assertEqual(len(rat.find_sessions(hours=24 * 60)), 3)

    def test_resume_commands(self):
        by_tool = {it["tool"]: it for it in rat.find_sessions(hours=6)}
        self.assertEqual(rat.resume_args(by_tool["claude"]), ["claude", "--resume", CLAUDE_ID])
        self.assertEqual(rat.resume_args(by_tool["codex"]), ["codex", "resume", CODEX_ID])


class HelpersTest(unittest.TestCase):
    def test_parse_choice(self):
        self.assertEqual(rat.parse_choice("1 3", 5), [0, 2])
        self.assertEqual(rat.parse_choice("2-4", 5), [1, 2, 3])
        self.assertEqual(rat.parse_choice("a", 3), [0, 1, 2])
        self.assertEqual(rat.parse_choice("9 x 1", 3), [0])

    def test_fit_counts_cjk_as_two_columns(self):
        self.assertEqual(rat.width(rat.fit("中文目录abc", 6)), 6)
        self.assertEqual(rat.fit("abc", 5), "abc  ")
        self.assertTrue(rat.fit("很长很长的标题", 7).endswith("…"))

    def test_is_agent_cmdline(self):
        self.assertTrue(rat.is_agent_cmdline("claude --resume abc"))
        self.assertTrue(rat.is_agent_cmdline("node /usr/lib/node_modules/.bin/codex resume x"))
        self.assertTrue(rat.is_agent_cmdline(r'"C:\Program Files\nodejs\node.exe" C:\npm\node_modules\@anthropic-ai\claude-code\cli.js'))
        self.assertFalse(rat.is_agent_cmdline("vim notes-about-claude.md"))


class TerminalCommandTest(unittest.TestCase):
    item = dict(tool="claude", id=CLAUDE_ID, cwd='/Users/me/my "quoted" dir', running=False)

    def test_iterm_script_escapes_quotes(self):
        script = rat.iterm_script([self.item], new_window=False)
        self.assertIn('set name to "my \\"quoted\\" dir"', script)
        self.assertIn("claude --resume " + CLAUDE_ID, script)
        self.assertIn("set w to current window", script)

    def test_wt_args_chain_tabs(self):
        items = [dict(tool="claude", id="a", cwd="C:\\work\\my app\\"),
                 dict(tool="codex", id="b", cwd="D:\\中文项目")]
        args = rat.wt_args(items, new_window=False)
        self.assertEqual(args[:3], ["wt", "-w", "0"])
        self.assertEqual(args.count("new-tab"), 2)
        self.assertEqual(args.count(";"), 1)
        self.assertIn("C:\\work\\my app", args)
        self.assertEqual(args[-5:], ["cmd", "/k", "codex", "resume", "b"])
        self.assertEqual(rat.wt_args(items, new_window=True)[2], "new")


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as home:
            make_home(home, os.path.join(home, "proj"))
            env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8")
            return subprocess.run([sys.executable, os.path.join(ROOT, "restore_ai_tabs.py")] + list(args),
                                  capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)

    def test_list(self):
        out = self.run_cli("--list")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("修复登录接口", out.stdout)
        self.assertIn("Deploy to Cloudflare", out.stdout)

    def test_all_dry_run_prints_resume_commands(self):
        out = self.run_cli("--all", "--dry-run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("claude --resume " + CLAUDE_ID, out.stdout)
        self.assertIn("codex resume " + CODEX_ID, out.stdout)

    def test_print_hook(self):
        out = self.run_cli("--print-hook", "powershell")
        self.assertIn("restore-ai-tabs --check", out.stdout)


if __name__ == "__main__":
    unittest.main()


SNAP = {"windows": [{"tabs": [
    {"root": {"split": "vertical", "children": [
        {"pane": {"cwd": "/work/api", "agent": {"tool": "claude", "id": "aaa", "cwd": "/work/api"}}},
        {"split": "horizontal", "children": [
            {"pane": {"cwd": "/work/web", "agent": {"tool": "codex", "id": "bbb", "cwd": "/work/web"}}},
            {"pane": {"cwd": "/tmp", "agent": None}}]}]}},
    {"root": {"pane": {"cwd": "/work/docs", "agent": None}}}]}]}


class LayoutTest(unittest.TestCase):
    def test_stats(self):
        self.assertEqual(rat.snapshot_stats(SNAP), (1, 2, 4, 2))

    def test_layout_script_rebuilds_splits_and_resumes(self):
        script = rat.layout_script(SNAP, running_ids=set())
        self.assertEqual(script.count("create window"), 1)
        self.assertEqual(script.count("create tab"), 1)
        self.assertIn("tell s1 to set s2 to (split vertically", script)
        self.assertIn("tell s2 to set s3 to (split horizontally", script)
        self.assertIn('write text "cd /work/api && claude --resume aaa"', script)
        self.assertIn('write text "cd /work/web && codex resume bbb"', script)
        self.assertIn('write text "cd /tmp"', script)

    def test_running_sessions_only_cd(self):
        script = rat.layout_script(SNAP, running_ids={"aaa"})
        self.assertIn('write text "cd /work/api"', script)
        self.assertNotIn("claude --resume aaa", script)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(rat, k) for k in
                      ("STATE_DIR", "SNAPSHOT_DIR", "STATUS_DIR", "CLAUDE_SETTINGS", "terminal_launch_id")}
        rat.STATE_DIR = self.tmp.name
        rat.SNAPSHOT_DIR = os.path.join(self.tmp.name, "snapshots")
        rat.STATUS_DIR = os.path.join(self.tmp.name, "status")
        rat.CLAUDE_SETTINGS = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(rat, k, v)
        self.tmp.cleanup()

    def send_hook(self, event, **extra):
        payload = dict(session_id="s1", hook_event_name=event, **extra)
        old = sys.stdin
        sys.stdin = __import__("io").StringIO(json.dumps(payload))
        try:
            rat.hook()
        finally:
            sys.stdin = old
        return rat.read_statuses().get("s1")

    def test_hook_state_machine(self):
        self.assertEqual(self.send_hook("UserPromptSubmit"), "running")
        self.assertEqual(self.send_hook("Notification", message="Claude needs your permission to use Bash"), "waiting")
        self.assertEqual(self.send_hook("PostToolUse"), "running")
        self.assertEqual(self.send_hook("Stop"), "done")
        self.assertEqual(self.send_hook("Notification", message="Claude is waiting for your input"), "done")
        self.assertIsNone(self.send_hook("SessionEnd"))

    def test_hook_never_raises_on_bad_input(self):
        old = sys.stdin
        sys.stdin = __import__("io").StringIO("not json")
        try:
            rat.hook()
        finally:
            sys.stdin = old

    def test_install_and_remove_hooks_keep_user_hooks(self):
        mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}, "model": "x"}
        rat.write_json(rat.CLAUDE_SETTINGS, mine)
        rat.install_claude_hooks()
        rat.install_claude_hooks()  # 重复安装不应重复添加
        s = rat.read_json(rat.CLAUDE_SETTINGS)
        self.assertEqual(len(s["hooks"]["Stop"]), 2)
        self.assertEqual(set(s["hooks"]), set(rat.HOOK_EVENTS))
        rat.remove_claude_hooks()
        self.assertEqual(rat.read_json(rat.CLAUDE_SETTINGS), mine)

    def test_previous_snapshot_skips_current_launch_and_consumed(self):
        rat.save_snapshot("old-launch", SNAP)
        rat.terminal_launch_id = lambda: "old-launch"
        self.assertIsNone(rat.previous_snapshot())
        rat.terminal_launch_id = lambda: "new-launch"
        snap = rat.previous_snapshot()
        self.assertEqual(snap["launch"], "old-launch")
        rat.mark_consumed(snap)
        self.assertIsNone(rat.previous_snapshot())
        self.assertIsNotNone(rat.previous_snapshot(include_consumed=True))

    def test_clear_done_only_touches_seen_done(self):
        self.send_hook("Stop")
        rat.clear_done(["s1"], since=time.time() + 60)  # 完成在开始看之前：离开时不清
        self.assertEqual(rat.read_statuses().get("s1"), "done")
        rat.clear_done(["s1"])
        self.assertIsNone(rat.read_statuses().get("s1"))
        self.send_hook("UserPromptSubmit")
        rat.clear_done(["s1"])
        self.assertEqual(rat.read_statuses().get("s1"), "running")


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.project = os.path.join(self.home, "proj")
        make_home(self.home, self.project)
        self.saved = (rat.HOME, rat.INDEX_FILE)
        rat.HOME = self.home
        rat.INDEX_FILE = os.path.join(self.home, "cache", "search-index.json")
        self.claude = os.path.join(self.home, ".claude", "projects", "-proj", CLAUDE_ID + ".jsonl")

    def tearDown(self):
        rat.HOME, rat.INDEX_FILE = self.saved
        self.tmp.cleanup()

    def append(self, raw):
        with open(self.claude, "a", encoding="utf-8") as fh:
            fh.write(raw)

    def test_searches_user_and_assistant_text_only(self):
        self.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "原因是 Redis 限流的阈值太低"},
            {"type": "tool_use", "input": {"cmd": "grep 秘密工具输出"}}]}}, ensure_ascii=False) + "\n")
        hits = rat.search_sessions("redis 限流")
        self.assertEqual([h["id"] for h in hits], [CLAUDE_ID])
        self.assertIn("限流", hits[0]["title"])
        self.assertEqual(rat.search_sessions("秘密工具输出"), [])
        self.assertEqual([h["id"] for h in rat.search_sessions("cloudflare")], [CODEX_ID])
        self.assertEqual(len(rat.search_sessions("很久以前")), 1)  # 搜索默认不限时间
        self.assertEqual(rat.search_sessions("很久以前", hours=6), [])

    def test_incremental_update_and_partial_line(self):
        rat.load_index()
        self.append('{"type": "user", "message": {"content": "新加的问题 alpha"}}\n{"type": "user", "mess')
        self.assertEqual(len(rat.search_sessions("alpha")), 1)
        self.append('age": {"content": "后半行 beta"}}\n')
        self.assertEqual(len(rat.search_sessions("beta")), 1)
        entry = rat.load_index()[self.claude]
        self.assertEqual(entry["offset"], os.path.getsize(self.claude))
        self.assertEqual(entry["texts"].count("新加的问题 alpha"), 1)

    def test_deleted_sessions_leave_index(self):
        rat.load_index()
        os.remove(self.claude)
        self.assertNotIn(self.claude, rat.load_index())
        self.assertEqual(rat.search_sessions("登录接口"), [])

    def test_cli_search(self):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, os.path.join(ROOT, "restore_ai_tabs.py"), "search", "登录接口", "--list"],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("登录接口", out.stdout)
        self.assertNotIn("Cloudflare", out.stdout)

