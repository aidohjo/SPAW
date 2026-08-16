# -*- coding: utf-8 -*-
"""工具执行 Mixin — 命令执行、搜索等工具的实现。"""

import json
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class ToolsMixin:
    """提供工具执行方法的 Mixin 类。

    期望宿主类（Daemon）提供以下属性：
        - self.blacklist: list      命令黑名单
        - self.se: WebSearchEngine  搜索引擎实例
        - self.tools: list          工具定义列表
        - self._execute_tool()      工具路由方法（由本 Mixin 提供）
        - self._run_agent()         Agent 自循环（在 core.py 中定义）
    """

    def _check_safety(self, command):
        """检查命令是否在黑名单中。返回 True 表示危险。"""
        if command in self.blacklist:
            logger.error('不允许命令：%s', command)
            return True
        return False

    def _execute_tool(self, tool_name, args):
        """工具路由：根据 tool_name 分发到具体执行方法。"""
        tools_map = {
            "execute_command": self._tool_execute_command,
            "manage_long_term_memory": self._manage_long_term_memory,
            "auto_agent": self._run_agent,
            "search": self._search,
            "deep_search": self.se.search_with_context,
            "search_with_url": self.se.fetch_full_content,
        }
        if tool_name in tools_map:
            try:
                return tools_map[tool_name](**args)
            except Exception as e:
                logger.error("工具 %s 执行失败: %s", tool_name, e)
                return f"error:{str(e)}"
        else:
            return f"未知工具: {tool_name}"

    def _tool_execute_command(self, command, timeout=10):
        """执行 Shell 命令（带安全检查和超时）。"""
        if self._check_safety(command):
            return f'危险命令：{command}'
        logger.info("执行命令: %s", command)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return f'out:{result.stdout} err:{result.stderr}'
        except subprocess.TimeoutExpired:
            return f"命令超时 ({timeout}秒)"
        except Exception as e:
            return f"error:{str(e)}"

    def _search(self, key_word: str, limit: int = 8):
        """执行网络搜索，返回文本结果。"""
        return self.se.search_text(query=key_word, max_results=limit)
