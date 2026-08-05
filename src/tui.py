#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tui.py — Wired 暗色终端界面 for SPAW
=====================================
基于 Textual 框架。Serial Experiments Lain 美学。

  • 双排状态栏 — 时钟、模型、Protocol 7、Schumann 共振、迭代计数
  • 左侧对话面板 — 用户与 AI 实时对话
  • 右侧 Debug 面板 — Agent 迭代过程、工具调用
  • 终端风格输入栏 — 绿色 > 提示符，聚焦辉光

用法:
    python tui.py              # 正常模式
    python tui.py --debug      # 调试模式
"""

import sys
import os
import json
import time
import datetime
import threading
from pathlib import Path
from typing import Optional

SRC_DIR = Path(__file__).parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Input, RichLog, Static, Label, Button, LoadingIndicator
)
from textual.reactive import reactive, var
from textual.binding import Binding
from textual.message import Message
from textual import events
from textual.css.query import NoMatches

from core import Daemon

# ═══════════════════════════════════════════════════════
#  Wired Dark — Serial Experiments Lain 主题
# ═══════════════════════════════════════════════════════
WIRED_CSS = """
/*
 * Wired Dark — Compact
 * Serial Experiments Lain 美学
 * 深黑 · 电光绿 · 紫罗兰 · 赛博朋克
 */

/* ── 全局基调 ── */
Screen {
    background: #0a0a12;
    color: #c8c8d4;
}

/* ── 紧凑双排状态栏 (height:4) ── */
#status-bar {
    height: auto;
    background: #0a0a12;
    border: none;
    border-bottom: solid #1a1a30;
    padding: 0 2;
}

#status-row-1 {
    height: 1;
    padding: 0 1;
    align: center middle;
    overflow: hidden;
}
#status-row-2 {
    height: 1;
    padding: 0 1;
    align: center middle;
    overflow: hidden;
}

.col-sep {
    color: #2a2a40;
    width: 3;
    content-align: center middle;
}

#app-title {
    color: #00ff66;
    text-style: bold;
    width: 28;
}
#clock {
    color: #06b6d4;
    width: 8;
    content-align: center middle;
}
#model-label {
    color: #8888a0;
    width: 20;
    content-align: center middle;
}
#mode-label {
    color: #c084fc;
    text-style: bold;
    width: 10;
    content-align: center middle;
}
#status-indicator {
    color: #00ff66;
    text-style: bold;
    width: 1fr;
    content-align: right middle;
}
#status-indicator.running {
    color: #fbbf24;
}
#status-indicator.error {
    color: #ef4444;
}

#protocol-label {
    color: #444460;
    width: 28;
}
#memory-label {
    color: #444460;
    width: 12;
}
#iter-counter {
    color: #fbbf24;
    width: 1fr;
    content-align: right middle;
}

/* ── 对话面板 ── */
#conversation-panel {
    border: none;
    border-right: solid #1a1a30;
    background: #0a0a12;
}

#conv-header {
    height: 2;
    background: #0a0a12;
    color: #00ff66;
    padding: 0 2;
    text-style: bold;
    content-align: left middle;
    border-bottom: solid #1a1a30;
}

#chat-log {
    background: #0a0a12;
    color: #c8c8d4;
    border: none;
    scrollbar-size: 0 0;
}

/* ── Debug 面板 ── */
#debug-panel {
    border: none;
    border-left: solid #1a1a30;
    background: #0a0a12;
    width: 38%;
    min-width: 36;
}

#debug-header {
    height: 2;
    background: #0a0a12;
    color: #c084fc;
    padding: 0 2;
    text-style: bold;
    content-align: left middle;
    border-bottom: solid #1a1a30;
}

#debug-log {
    background: #0a0a12;
    color: #8888a0;
    border: none;
    scrollbar-size: 0 0;
}

/* ── 输入栏 ── */
#input-area {
    height: 2;
    background: #0a0a12;
    border-top: solid #1a1a30;
    padding: 0 1;
}

#input-prompt {
    color: #00ff66;
    text-style: bold;
    width: 2;
    content-align: center middle;
}

#user-input {
    background: #0a0a12;
    color: #d8d8e8;
    border: none;
    width: 1fr;
}

#user-input:focus {
    background: #0c0c16;
    border: none;
}

#send-btn {
    min-width: 8;
    background: #0a0a12;
    color: #8888a0;
    border: none;
}
#send-btn:hover {
    color: #00ff66;
    background: #0a0a12;
}

/* ── 全局滚动条隐藏 ── */
Scrollbar {
    scrollbar-size: 0 0;
}
"""

# ═══════════════════════════════════════════════════════
#  双排状态栏
# ═══════════════════════════════════════════════════════
class StatusBar(Static):
    """双排信息状态栏 — Wired 主题"""

    agent_status: reactive[str] = reactive("idle")
    iteration: reactive[int] = reactive(0)
    max_iterations: reactive[int] = reactive(0)
    model_name: reactive[str] = reactive("")
    mode: reactive[str] = reactive("Normal")

    def compose(self) -> ComposeResult:
        with Horizontal(id="status-row-1"):
            yield Label("⚡ Lain · Wired v2.1", id="app-title")
            yield Label("│", classes="col-sep")
            yield Label("00:00", id="clock")
            yield Label("│", classes="col-sep")
            yield Label("gpt-4", id="model-label")
            yield Label("│", classes="col-sep")
            yield Label("NORMAL", id="mode-label")
            yield Label("│", classes="col-sep")
            yield Label("● READY", id="status-indicator")
        with Horizontal(id="status-row-2"):
            yield Label("Protocol 7 · 7.83 Hz", id="protocol-label")
            yield Label("│", classes="col-sep")
            yield Label("mem: OK", id="memory-label")
            yield Label("│", classes="col-sep")
            yield Label("iter: 0/0", id="iter-counter")

    def on_mount(self) -> None:
        """每秒更新时钟"""
        self._update_clock()
        self.set_interval(1.0, self._update_clock)

    def _update_clock(self) -> None:
        now = datetime.datetime.now().strftime("%H:%M")
        try:
            self.query_one("#clock", Label).update(now)
        except NoMatches:
            pass

    def watch_model_name(self, old: str, new: str):
        try:
            self.query_one("#model-label", Label).update(new)
        except NoMatches:
            pass

    def watch_mode(self, old: str, new: str):
        try:
            self.query_one("#mode-label", Label).update(new.upper())
        except NoMatches:
            pass

    def watch_agent_status(self, old: str, new: str):
        try:
            ind = self.query_one("#status-indicator", Label)
            ind.remove_class("running", "error")
            if new == "running":
                ind.update("◉ RUNNING")
                ind.add_class("running")
            elif new == "error":
                ind.update("✖ ERROR")
                ind.add_class("error")
            else:
                ind.update("● READY")
        except NoMatches:
            pass

    def watch_iteration(self, old: int, new: int):
        try:
            counter = self.query_one("#iter-counter", Label)
            if self.max_iterations > 0:
                counter.update(f"iter: {new}/{self.max_iterations}")
            elif new > 0:
                counter.update(f"iter: {new}")
            else:
                counter.update("iter: 0/0")
        except NoMatches:
            pass

    def update_memory(self, status: str):
        try:
            self.query_one("#memory-label", Label).update(f"mem: {status}")
        except NoMatches:
            pass


# ═══════════════════════════════════════════════════════
#  主应用
# ═══════════════════════════════════════════════════════
class AssistantTUI(App):
    """Wired 暗色 SPAW TUI 应用"""

    CSS = WIRED_CSS
    TITLE = "SPAW · Lain"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+d", "toggle_debug", "Toggle Debug", show=True),
        Binding("ctrl+l", "clear_chat", "Clear Chat", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
    ]

    show_debug: reactive[bool] = reactive(True)
    is_running: reactive[bool] = reactive(False)

    def __init__(self, debug_mode: bool = False):
        super().__init__()
        self._debug_mode = debug_mode
        self._daemon: Optional[Daemon] = None
        self._agent_thread: Optional[threading.Thread] = None
        self._current_assistant_text = ""
        self._is_first_chunk = True
        self._streaming_active = False
        self._stream_prefix = ""
        self._stream_line_start = 0
        self._stream_buffer = ""

    # ── 生命周期 ──

    def on_mount(self) -> None:
        config_path = SRC_DIR / "config.json"
        try:
            self._daemon = Daemon(str(config_path))
            self._log_debug("●  Daemon initialized", "success")
            self._log_debug(f"   model: {self._daemon.model}", "info")
            self._log_debug(f"   memory: {self._daemon.long_term_path}", "info")

            status_bar = self.query_one(StatusBar)
            status_bar.model_name = self._daemon.model
            status_bar.update_memory("OK")
            if self._debug_mode:
                status_bar.mode = "Debug"
        except Exception as e:
            self._log_debug(f"✖  Daemon init failed: {e}", "error")
            self._log_chat("system", f"✖  Init failed: {e}")
            return

        self._load_history()
        self.set_focus(self.query_one("#user-input", Input))

        self._log_chat("system", "⚡ Welcome to the Wired.")
        self._log_chat("system", "   Ctrl+Q quit  |  Ctrl+D toggle debug  |  Ctrl+L clear")
        self._log_debug("●  TUI ready — waiting for input...", "info")

    # ── UI 组合 ──

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")

        with Horizontal():
            with Vertical(id="conversation-panel"):
                yield Label("󰍁 CONVERSATION", id="conv-header")
                yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)

            with Vertical(id="debug-panel"):
                yield Label("󰨙 DEBUG", id="debug-header")
                yield RichLog(id="debug-log", highlight=True, markup=True, wrap=True)

        with Horizontal(id="input-area"):
            yield Label(">", id="input-prompt")
            yield Input(placeholder="type your message... (Enter to send)", id="user-input")
            yield Button("󱗂 Send", id="send-btn", variant="primary")

    # ── 事件 ──

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "user-input":
            self._send_message(event.value)
            event.input.clear()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            inp = self.query_one("#user-input", Input)
            if inp.value.strip():
                self._send_message(inp.value)
                inp.clear()
                self.set_focus(inp)

    def action_toggle_debug(self) -> None:
        try:
            dp = self.query_one("#debug-panel", Vertical)
            if self.show_debug:
                dp.styles.display = "none"
                self.show_debug = False
            else:
                dp.styles.display = "block"
                self.show_debug = True
        except NoMatches:
            pass

    def action_clear_chat(self) -> None:
        self.query_one("#chat-log", RichLog).clear()
        self._log_chat("system", "  cleared.")

    def action_focus_input(self) -> None:
        self.set_focus(self.query_one("#user-input", Input))

    # ── Agent 线程桥接 ──

    def _on_agent_event(self, event: dict) -> None:
        self.call_from_thread(self._process_event, event)

    def _run_agent_in_thread(self, goal: str) -> None:
        try:
            result = self._daemon._run_agent(
                goal=goal,
                max_iterations=2000,
                exit_condition="auto",
                short_term_memory= 20,
                level_flag= "main",
                on_event=self._on_agent_event
            )
            self.call_from_thread(self._on_agent_done, result)
        except Exception as e:
            self.call_from_thread(self._on_agent_error, str(e))

    # ── 发送消息 ──

    def _send_message(self, text: str) -> None:
        if self.is_running:
            self._log_chat("system", "⚠  Agent is running, please wait...")
            return
        text = text.strip()
        if not text:
            return

        self._streaming_active = False
        self._stream_buffer = ""
        self._is_first_chunk = True

        self._log_chat("user", text)

        self.is_running = True
        sbar = self.query_one(StatusBar)
        sbar.agent_status = "running"
        sbar.iteration = 0

        debug_log = self.query_one("#debug-log", RichLog)
        debug_log.write("\n" + "─" * 50)

        self._log_debug(f"📨 user: {text[:80]}{'...' if len(text)>80 else ''}", "user")

        self._agent_thread = threading.Thread(
            target=self._run_agent_in_thread,
            args=(text,),
            daemon=True
        )
        self._agent_thread.start()

    # ── 事件处理 ──

    def _process_event(self, event: dict) -> None:
        etype = event.get("type", "")

        if etype == "text_chunk":
            chunk = event.get("content", "")
            if chunk:
                if not self._streaming_active:
                    self._streaming_active = True
                if '\n' in chunk:
                    self._stream_buffer += chunk[:-1]
                    self._log_chat("assistant", self._stream_buffer)
        
                    self._stream_buffer = ""
                    self._streaming_active = False
                else:
                    self._stream_buffer += chunk

        elif etype == "iteration_start":
            iteration = event.get("iteration", 0)
            max_iter = event.get("max_iterations", 0)
            sbar = self.query_one(StatusBar)
            sbar.iteration = iteration
            sbar.max_iterations = max_iter
            self._log_debug(
                f"[dim]─── Iter {iteration}/{max_iter} ───[/dim]",
                "iteration"
            )

        elif etype == "assistant_text":
            content_text = event.get("content", "")
            iteration = event.get("iteration", 0)
            # flush 缓冲区到对话面板
            if self._stream_buffer:
                self._log_chat("assistant", self._stream_buffer)
                self._stream_buffer = ""
                self._streaming_active = False
            if content_text:
                self._log_debug(
                    f"[bold #c084fc]💭 [{iteration}] thought:[/bold #c084fc]\n"
                    f"[#c8c8d4]{content_text[:500]}[/#c8c8d4]",
                    "thought"
                )

        elif etype == "tool_call":
            if self._stream_buffer:
                self._log_chat("assistant", self._stream_buffer)
                self._stream_buffer = ""
            self._streaming_active = False
            self._is_first_chunk = True
            self._current_assistant_text = ""

            tool_name = event.get("tool_name", "")
            args = event.get("args", {})
            iteration = event.get("iteration", 0)

            args_str = json.dumps(args, ensure_ascii=False, indent=2)
            if len(args_str) > 300:
                args_str = args_str[:300] + "..."

            icons = {
                "execute_command": "⚙",
                "manage_long_term_memory": "🧠",
                "auto_agent": "🤖",
                "search": "🔍",
                "finish_task": "✓",
            }
            icon = icons.get(tool_name, "◆")

            self._log_debug(
                f"[bold #fbbf24]{icon} [{iteration}] {tool_name}[/bold #fbbf24]\n"
                f"[dim]{args_str}[/dim]",
                "tool"
            )

        elif etype == "tool_result":
            tool_name = event.get("tool_name", "")
            result = event.get("result", "")
            display = result[:400] + ("..." if len(result) > 400 else "")
            self._log_debug(f"[#00ff6688]   ↳ {display}[/#00ff6688]", "result")

        elif etype == "done":
            if self._stream_buffer:
                self._log_chat("assistant", self._stream_buffer)
                self._stream_buffer = ""
            self._streaming_active = False
            self._is_first_chunk = True
            self._current_assistant_text = ""

            summary = event.get("summary", "")
            self._log_debug(
                f"\n[bold #00ff66]✓  done![/bold #00ff66]\n[#00ff66aa]{summary[:300]}[/#00ff66aa]",
                "done"
            )

    def _on_agent_done(self, result: str) -> None:
        self.is_running = False
        sbar = self.query_one(StatusBar)
        sbar.agent_status = "idle"
        self._stream_buffer = ""
        self._log_debug(f"[bold #00ff66]●  Agent finished[/bold #00ff66]", "success")
        self._log_debug("─" * 50 + "\n", "info")
        self.set_focus(self.query_one("#user-input", Input))

    def _on_agent_error(self, error_msg: str) -> None:
        self.is_running = False
        sbar = self.query_one(StatusBar)
        sbar.agent_status = "error"
        self._log_chat("system", f"✖  Error: {error_msg}")
        self._log_debug(f"[bold #ef4444]✖  Error: {error_msg}[/bold #ef4444]", "error")
        self.set_focus(self.query_one("#user-input", Input))

    # ── 日志辅助 ──

    def _log_chat(self, role: str, text: str, is_stream: bool = False) -> None:
        try:
            chat_log = self.query_one("#chat-log", RichLog)
        except NoMatches:
            return

        if role == "user":
            prefix = "[bold #00ff66]> [/bold #00ff66]"
            chat_log.write(prefix + text)
        elif role == "assistant":
            if is_stream:
                if self._is_first_chunk:
                    prefix = "[bold #c084fc]◆ [/bold #c084fc]"
                    self._stream_prefix = prefix
                    self._stream_line_start = len(chat_log.lines)
                    chat_log.write(prefix + text)
                    self._is_first_chunk = False
                else:
                    try:
                        if self._stream_line_start is None:
                            self._stream_prefix = "[bold #c084fc]◆ [/bold #c084fc]"
                            self._stream_buffer = text
                            chat_log.write(self._stream_prefix + text)
                            self._stream_line_start = len(chat_log.lines) - 1
                        else:
                            while len(chat_log.lines) > self._stream_line_start:
                                chat_log.lines.pop()
                            self._stream_buffer += text
                            chat_log.write(self._stream_prefix + self._stream_buffer)
                    except Exception:
                        chat_log.write(text)
                        self._stream_line_start = None
                        self._stream_buffer = ""
                        self._stream_prefix = ""
            else:
                prefix = "[bold #c084fc]◆ [/bold #c084fc]" if self._is_first_chunk else ""
                if self._is_first_chunk:
                    self._is_first_chunk = False
                chat_log.write(prefix + text)
        elif role == "system":
            chat_log.write(f"[dim italic]{text}[/dim italic]")

    def _log_debug(self, text: str, category: str = "info") -> None:
        try:
            debug_log = self.query_one("#debug-log", RichLog)
        except NoMatches:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        debug_log.write(f"[dim]{ts}[/dim] {text}")

    def _load_history(self) -> None:
        if not self._daemon:
            return
        try:
            history = self._daemon.load_recent_short_term(limit=20)
            if history:
                chat_log = self.query_one("#chat-log", RichLog)
                chat_log.write("[dim italic]── history ──[/dim italic]")
                for entry in history:
                    role = entry.get("role", "")
                    content = entry.get("content", "")
                    if role == "user":
                        self._log_chat("user", content)
                    elif role == "assistant":
                        self._log_chat("assistant", content[:500])
                chat_log.write("[dim italic]── end of history ──[/dim italic]\n")
        except Exception as e:
            self._log_debug(f"load history failed: {e}", "warn")


# ═══════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    debug_mode = "--debug" in sys.argv or "-d" in sys.argv
    app = AssistantTUI(debug_mode=debug_mode)
    app.run()
