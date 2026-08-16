# -*- coding: utf-8 -*-
"""记忆管理 Mixin — 短期/长期记忆的读写操作。"""

import json
import datetime
import threading
from pathlib import Path
from typing import Optional


class MemoryMixin:
    """提供记忆管理方法的 Mixin 类。

    期望宿主类（Daemon）提供以下属性：
        - self.memory: dict          长期记忆数据
        - self.long_term_path: Path  长期记忆文件路径
        - self.short_term_dir: Path  短期记忆目录
        - self._memory_lock: RLock   线程锁
    """

    # ── 长期记忆 ──

    def _load_long_term(self):
        """加载长期记忆 JSON 文件"""
        if self.long_term_path.exists():
            try:
                with open(self.long_term_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("长期记忆文件损坏，将重新初始化")
                return {}
        return {}

    def save_long_term(self):
        """保存长期记忆到文件（线程安全）"""
        with self._memory_lock:
            self.save_long_term_nolock()

    def save_long_term_nolock(self):
        """保存长期记忆到文件（调用者必须已持有 _memory_lock）"""
        with open(self.long_term_path, 'w', encoding='utf-8') as f:
            json.dump(self.memory, f, ensure_ascii=False, indent=2)

    def get_memory(self, key, default=None):
        return self.memory.get(key, default)

    def set_memory(self, key, value):
        with self._memory_lock:
            self.memory[key] = value

    # ── 短期记忆 ──

    def append_to_short_term(self, role, content):
        """追加一条消息到今日短期记忆文件（JSONL）"""
        if role == "tool":   # 不存储工具消息
            return
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = self.short_term_dir / f"{today}.jsonl"
        entry = {"role": role, "content": content}
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load_recent_short_term(self, limit: int = 50):
        """加载今日最近的 N 条短期记忆"""
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = self.short_term_dir / f"{today}.jsonl"
        messages = []
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        messages.append(json.loads(line.strip()))
                    except Exception:
                        continue
        return messages[-limit:] if messages else []

    # ── 统一记忆接口 ──

    def _manage_long_term_memory(
        self,
        action: str,
        key: str,
        value: Optional[str] = None,
        index: Optional[int] = None
    ) -> str:
        """管理长期记忆的统一接口，对应工具 manage_long_term_memory 的参数。"""
        import json as _json

        if action == "get":
            result = self.get_memory(key, None)
            if result is None:
                return f"Key '{key}' not found."
            if isinstance(result, (list, dict)):
                return _json.dumps(result, ensure_ascii=False)
            return str(result)

        # 所有写操作在同一个锁内完成，防止竞态条件
        with self._memory_lock:
            if action == "set":
                if value is None:
                    return "Error: 'value' is required for action 'set'."
                try:
                    parsed = _json.loads(value)
                except _json.JSONDecodeError:
                    parsed = value
                self.memory[key] = parsed
                self.save_long_term_nolock()
                return f"Successfully set '{key}' to {_json.dumps(parsed, ensure_ascii=False)}"

            elif action == "append":
                if value is None:
                    return "Error: 'value' is required for action 'append'."
                current = self.memory.get(key)
                if current is None:
                    current = []
                elif not isinstance(current, list):
                    return f"Error: key '{key}' exists but is not a list (type: {type(current).__name__}). Cannot append."
                current.append(value)
                self.memory[key] = current
                self.save_long_term_nolock()
                return f"Successfully appended '{value}' to '{key}'. Now length: {len(current)}"

            elif action == "remove":
                if index is None:
                    return "Error: 'index' is required for action 'remove'."
                current = self.memory.get(key)
                if current is None:
                    return f"Error: key '{key}' not found."
                if not isinstance(current, list):
                    return f"Error: key '{key}' is not a list (type: {type(current).__name__}). Cannot remove by index."
                if not (0 <= index < len(current)):
                    return f"Error: index {index} out of range. Valid indices: 0..{len(current)-1}"
                removed = current.pop(index)
                self.memory[key] = current
                self.save_long_term_nolock()
                return f"Successfully removed item at index {index} (value: '{removed}') from '{key}'. Remaining length: {len(current)}"

            else:
                return f"Error: unknown action '{action}'. Supported: get, set, append, remove."
