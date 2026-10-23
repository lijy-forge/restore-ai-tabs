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
