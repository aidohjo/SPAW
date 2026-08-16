#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
web_search.py — 网络搜索引擎接口（基于 duckduckgo-search 库）
与 core.py 配合使用，为 AI Daemon 提供网络搜索能力。
使用 duckduckgo-search 库替代原 HTML 解析方案，稳定性更高。
"""

import logging
import trafilatura
import requests
from typing import List, Optional, Dict
import logging
from urllib.parse import urlparse

from ddgs import DDGS

# ============ 日志配置 ============
logger = logging.getLogger(__name__)

# ============ 数据结构（保持不变） ============

class WebSearchResult:
    """单条网络搜索结果"""

    __slots__ = ("title", "url", "snippet", "display_url")

    def __init__(self, title: str = "", url: str = "",
                 snippet: str = "", display_url: str = ""):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.display_url = display_url

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "display_url": self.display_url,
        }

    def __str__(self) -> str:
        parts = []
        if self.title:
            parts.append(f"📌 {self.title}")
        if self.snippet:
            parts.append(f"   {self.snippet}")
        if self.url:
            parts.append(f"   🔗 {self.url}")
        return "\n".join(parts)

    def __repr__(self) -> str:
        return f"WebSearchResult(title={self.title!r}, url={self.url!r})"


# ============ 搜索引擎类（重构版） ============

class WebSearchEngine:
    """
    基于 duckduckgo-search 库的网络搜索引擎。

    用法与之前完全一致：
        wse = WebSearchEngine(proxies={"http": "http://127.0.0.1:7890", ...})
        results = wse.search("Python 教程")
        print(wse.search_text("Python 教程"))
    """

    _DEFAULT_TIMEOUT = 30          # duckduckgo-search 默认超时
    _DEFAULT_MAX_RESULTS = 10

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        max_results: int = _DEFAULT_MAX_RESULTS,
 
    ):
        """
        Args:
            timeout: HTTP 请求超时秒数（传递给 DDGS）
            max_results: 单次搜索返回的最大结果数
            proxies: 代理配置，格式如 {"http": "http://127.0.0.1:7890", "https": "..."}
        """
        self._timeout = timeout
        self._max_results = max_results
    
        self._ddgs = DDGS(
            timeout=self._timeout,
        )
        import os
        logger.info(
            "WebSearchEngine 初始化 — timeout=%ds, max_results=%d, proxies=%s",
            timeout, max_results, os.environ["http_proxy"] or None
        )

    # ========== 公开 API ==========

    def search(self, query: str, max_results: Optional[int] = None) -> List[WebSearchResult]:
        """执行网络搜索，返回结构化结果列表。"""
        limit = max_results if max_results is not None else self._max_results

        try:
            # duckduckgo_search 的 text() 方法返回生成器，我们转为列表
            raw_results = list(self._ddgs.text(query, max_results=limit))
        except Exception as e:
            logger.error("搜索 '%s' 失败: %s", query, e)
            return []

        results: List[WebSearchResult] = []
        for item in raw_results:
            # 字段映射：title, href, body
            title = item.get("title", "")
            url = item.get("href", "")
            snippet = item.get("body", "")
            # display_url 可由 url 精简，也可留空
            display_url = url  # 或自行处理

            if not title:  # 跳过空标题
                continue

            results.append(WebSearchResult(
                title=title,
                url=url,
                snippet=snippet,
                display_url=display_url,
            ))

        logger.info("搜索 '%s' → %d 条结果", query, len(results))
        return results

    def search_text(self, query: str, max_results: Optional[int] = None) -> str:
        """执行搜索，返回人类可读的纯文本摘要。"""
        results = self.search(query, max_results)

        if not results:
            return f"（未找到与 '{query}' 相关的搜索结果，请检查网络或更换关键词）"

        lines = [f"🔍 搜索: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            if r.snippet:
                lines.append(f"   {r.snippet}")
            if r.url:
                lines.append(f"   🔗 {r.url}")
            lines.append("")

        return "\n".join(lines).strip()

    def search_json(self, query: str, max_results: Optional[int] = None) -> str:
        """执行搜索，返回 JSON 格式结果。"""
        import json
        results = self.search(query, max_results)
        data = {
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def fetch_full_content(self, url: str, timeout: int = 10) -> Optional[str]:
        """
        抓取指定 URL 的网页并提取正文全文。
        
        Args:
            url: 目标网址
            timeout: 请求超时时间
            
        Returns:
            提取的纯文本内容，如果失败或解析不到则返回 None
        """
        try:
            # 1. 发送请求（建议复用 self._ddgs 的代理配置）
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            # 如果你之前传入了 proxies，这里可以获取并传入
            # 简单起见，这里直接用 requests.get
            response = requests.get(url, timeout=timeout, headers=headers)
            response.encoding = response.apparent_encoding or 'utf-8'
            
            if response.status_code != 200:
                logger.warning("抓取失败，状态码 %d: %s", response.status_code, url)
                return None

            # 2. 使用 trafilatura 提取正文
            # extract 返回纯文本，include_comments=False 排除杂乱评论
            extracted_text = trafilatura.extract(
                response.text,
                include_comments=False,
                include_links=False,
                include_tables=True,   # 技术博客经常有表格
                include_formatting=True
            )
            
            if not extracted_text:
                # 如果 trafilatura 提取为空，尝试降级方案：直接取前 2000 字符
                # 但通常 trafilatura 效果很好
                logger.warning("trafilatura 未能提取正文: %s", url)
                return None
                
            # 简单清洗：去除过度的换行
            cleaned = '\n'.join([line.strip() for line in extracted_text.splitlines() if line.strip()])
            return cleaned
            
        except requests.exceptions.Timeout:
            logger.error("抓取超时: %s", url)
            return None
        except Exception as e:
            logger.error("抓取 '%s' 异常: %s", url, e)
            return None

    def deep_search(
        self, 
        query: str, 
        max_results: int = 5, 
        fetch_all: bool = True
    ) -> List[Dict]:
        """
        增强搜索：不仅返回标题/摘要，还抓取并附上前 N 个结果的全文内容。
        
        Args:
            query: 搜索关键词
            max_results: 最多处理几个链接（建议 3~5 个，抓取全文耗时较长）
            fetch_all: 是否全部抓取，否则只抓取第一个
            
        Returns:
            包含 'title', 'url', 'snippet', 'full_content' 的字典列表
        """
        results = self.search(query, max_results=max_results)
        if not results:
            return []

        deep_results = []
        # 只处理前 max_results 个，避免耗时过长
        for r in results[:max_results]:
            item = {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "full_content": None
            }
            if fetch_all:
                logger.info("正在抓取全文: %s", r.url)
                content = self.fetch_full_content(r.url)
                # 截断过长内容，防止 LLM Token 溢出（比如限制 8000 字符）
                if content and len(content) > 8000:
                    content = content[:8000] + "...(已截断)"
                item["full_content"] = content
            
            deep_results.append(item)
        
        logger.info("深搜完成，共处理 %d 个页面", len(deep_results))
        return deep_results

    def search_with_context(self, query: str, max_results: int = 3) -> str:
        """
        生成适合 LLM 调用的上下文文本：摘要 + 全文。
        """
        items = self.deep_search(query, max_results=max_results, fetch_all=True)
        if not items:
            return f"未找到与 '{query}' 相关的结果。"
        
        output_lines = [f"🔍 搜索: {query}\n"]
        for idx, item in enumerate(items, 1):
            output_lines.append(f"--- 结果 {idx} ---")
            output_lines.append(f"标题: {item['title']}")
            output_lines.append(f"链接: {item['url']}")
            if item['snippet']:
                output_lines.append(f"摘要: {item['snippet']}")
            if item['full_content']:
                output_lines.append(f"全文内容 (截取):\n{item['full_content']}")
            else:
                output_lines.append("(未能抓取到全文)")
            output_lines.append("")
        
        return "\n".join(output_lines)


# ============ 工具函数 ============

def tool_web_search(query: str, max_results: int = 10) -> str:
    """供 Daemon 直接调用的网络搜索工具函数。"""
    wse = WebSearchEngine(max_results=max_results)
    return wse.search_text(query, max_results=max_results)


# ============ 简易测试（可选） ============
if __name__ == "__main__":
    # 设置代理（如果需要）

    engine = WebSearchEngine(max_results=5)
    Q = input("Q: ")
    print(engine.fetch_full_content(Q))
