"""restore-ai-tabs 的 iTerm2 后台脚本（需要在 iTerm2 里打开 Python API）。

由 `restore-ai-tabs setup` 安装到 iTerm2 的 AutoLaunch 目录，随 iTerm2 启动，负责：
  1. 布局一变化就保存快照：窗口 / tab / 分屏结构，每个窗格的目录和里面跑的 AI 会话
  2. 把 Claude Code 钩子上报的状态画到 tab 上：🔵 运行中 / 🟡 等你确认 / ✅ 已完成
"""
import asyncio
import os

import iterm2

import restore_ai_tabs as rat

POLL_SECONDS = 2

STATUS_STYLE = {
    "waiting": ("🟡", (255, 176, 32)),
    "running": ("🔵", (10, 132, 255)),
    "done": ("✅", (142, 142, 147)),
}
PRIORITY = ["waiting", "running", "done"]


async def pane_info(session):
    tty = await session.async_get_variable("tty")
    cwd = await session.async_get_variable("path")
    return {"id": session.session_id, "tty": tty or "", "cwd": cwd or ""}


async def build_node(node, agents, panes):
    """把 iTerm 的分屏树转成可序列化的 dict，同时收集每个窗格（供刷状态用）。"""
    if isinstance(node, iterm2.Splitter):
        children = [await build_node(c, agents, panes) for c in node.children]
        if len(children) == 1:
            return children[0]
        return {"split": "vertical" if node.vertical else "horizontal", "children": children}
    info = await pane_info(node)
    agent = agents.get(info["tty"])
    if agent and not agent["cwd"]:
        agent = dict(agent, cwd=info["cwd"])
    panes.append((node, agent))
    return {"pane": {"cwd": info["cwd"], "agent": agent,
                     "cols": node.grid_size.width, "rows": node.grid_size.height}}


async def take_snapshot(app):
    agents = rat.agents_by_tty()
    windows, tabs = [], []
    for w in app.windows:
        wtabs = []
        for t in w.tabs:
            panes = []
            root = await build_node(t.root, agents, panes)
            wtabs.append({"root": root})
            tabs.append((t, panes))
        if wtabs:
            windows.append({"tabs": wtabs})
    return {"windows": windows}, tabs


def tab_status(panes, statuses):
    found = [statuses.get(a["id"]) for _, a in panes if a and a["tool"] == "claude"]
    for state in PRIORITY:
        if state in found:
            return state
    return None


async def paint(tab, panes, state, cwd):
    if state:
        emoji, (r, g, b) = STATUS_STYLE[state]
        await tab.async_set_title("%s %s" % (emoji, os.path.basename(cwd.rstrip("/")) or cwd))
    else:
        await tab.async_set_title("")
    for session, agent in panes:
        if not agent:
            continue
        profile = iterm2.LocalWriteOnlyProfile()
        if state:
            profile.set_tab_color(iterm2.Color(r, g, b))
        profile.set_use_tab_color(bool(state))
        await session.async_set_profile_properties(profile)


async def main(connection):
    launch = rat.terminal_launch_id() or "unknown"
    painted = {}  # tab_id -> 上次画上去的状态
    last_saved = None

    async with iterm2.LayoutChangeMonitor(connection) as monitor:
        while True:
            try:
                app = await iterm2.async_get_app(connection)
                snapshot, tabs = await take_snapshot(app)
                if snapshot["windows"] and snapshot != last_saved:
                    rat.save_snapshot(launch, snapshot)
                    last_saved = snapshot

                statuses = rat.read_statuses()
                for tab, panes in tabs:
                    state = tab_status(panes, statuses)
                    # 只改有状态的 tab，或者之前被我们改过的 tab；不动用户自己命名的 tab
                    if painted.get(tab.tab_id) != state and (state or tab.tab_id in painted):
                        agent_cwd = next((a["cwd"] for _, a in panes if a), "")
                        await paint(tab, panes, state, agent_cwd)
                        painted[tab.tab_id] = state
            except Exception as e:  # 后台脚本不能因为一次异常就退出
                print("restore-ai-tabs:", repr(e))
            try:
                await asyncio.wait_for(monitor.async_get(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


def run():
    iterm2.run_forever(main)


if __name__ == "__main__":
    run()
