#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
web_search.py — 网络搜索引擎接口
与 core.py 配合使用，为 AI Daemon 提供网络搜索能力。
基于 DuckDuckGo Lite（无 JS、轻量 HTML），使用 requests 获取搜索结果，
正则表达式清理标签并提取纯文本摘要。

可独立使用，也可作为 Daemon 的工具函数导入。
"""

import re
import logging
import time
from typing import List, Optional
from urllib.parse import quote_plus

import requests

# ============ 日志配置 ============
logger = logging.getLogger(__name__)


# ============ 数据结构 ============

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


# ============ 标签清理工具 ============

# 预编译正则，避免每次调用重新编译
_RE_SCRIPT_STYLE = re.compile(
    r'<(script|style|noscript|iframe|svg|canvas|video|audio|'
    r'source|embed|object|applet|form|select|option|textarea|'
    r'input|button|label|fieldset|legend|datalist|output|'
    r'map|area|nav|footer|header|aside|template|slot|'
    r'head|meta|link|title|base)[^>]*?>.*?</\1\s*>',
    re.DOTALL | re.IGNORECASE,
)

_RE_SELF_CLOSING_TAGS = re.compile(
    r'<(?:br|hr|img|input|meta|link|base|area|col|embed|'
    r'source|track|wbr|param)\s*[^>]*?>',
    re.IGNORECASE,
)

_RE_ALL_TAGS = re.compile(r'<[^>]*?>')

_RE_WHITESPACE = re.compile(r'[ \t]+')
_RE_BLANK_LINES = re.compile(r'\n{3,}')
_RE_ENTITIES = re.compile(r'&(?:amp|lt|gt|quot|nbsp|#\d+|#x[0-9a-fA-F]+);')


def clean_html(raw_html: str) -> str:
    """清理 HTML 标签，返回纯文本。

    处理顺序：
    1. 移除 <script>/<style> 等整块标签（含内容）
    2. 移除自闭合标签（<br>, <img> 等）
    3. 移除所有剩余 HTML 标签
    4. 解码常见 HTML 实体
    5. 压缩多余空白
    """
    if not raw_html:
        return ""

    text = raw_html

    # 1. 移除整块标签（script, style, noscript 等）
    text = _RE_SCRIPT_STYLE.sub(" ", text)

    # 2. 自闭合标签替换为空格（避免单词粘连）
    text = _RE_SELF_CLOSING_TAGS.sub(" ", text)

    # 3. 移除所有剩余标签
    text = _RE_ALL_TAGS.sub(" ", text)

    # 4. 解码 HTML 实体
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&nbsp;", " ")
    text = text.replace("&#x27;", "'")

    # 5. 压缩空白
    text = _RE_WHITESPACE.sub(" ", text)
    text = _RE_BLANK_LINES.sub("\n\n", text)

    return text.strip()


# ============ 搜索引擎类 ============

class WebSearchEngine:
    """基于 DuckDuckGo Lite 的网络搜索引擎。

    用法::

        wse = WebSearchEngine()
        results = wse.search("Python 教程")
        for r in results:
            print(r)
        # 或直接获取文本摘要
        print(wse.search_text("Python 教程"))

    特性:
    - 无 JS 依赖，纯 HTTP + HTML 解析
    - 自动清理所有 HTML 标签
    - 提取标题、摘要、URL
    - 可配置超时、重试、结果数量
    """

    _BASE_URL = "https://lite.duckduckgo.com/lite/"
    _DEFAULT_TIMEOUT = 12
    _DEFAULT_MAX_RETRIES = 2
    _DEFAULT_RESULTS = 10

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        max_results: int = _DEFAULT_RESULTS,
        user_agent: Optional[str] = None,
    ):
        """
        Args:
            timeout: HTTP 请求超时秒数
            max_retries: 请求失败重试次数
            max_results: 单次搜索返回的最大结果数
            user_agent: 自定义 User-Agent，默认使用常见浏览器标识
        """
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_results = max_results
        self._user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self._user_agent})

        logger.info("WebSearchEngine 初始化 — timeout=%ds, retries=%d, max_results=%d",
                     timeout, max_retries, max_results)

    # ========== 公开 API ==========

    def search(self, query: str, max_results: Optional[int] = None) -> List[WebSearchResult]:
        """执行网络搜索，返回结构化结果列表。

        Args:
            query: 搜索关键词
            max_results: 覆盖默认最大结果数

        Returns:
            WebSearchResult 列表（可能为空）
        """
        limit = max_results if max_results is not None else self._max_results
        html = self._fetch(query)

        if not html:
            logger.warning("搜索 '%s' 未获取到页面内容", query)
            return []

        results = self._parse_results(html, limit)
        logger.info("搜索 '%s' → %d 条结果", query, len(results))
        return results

    def search_text(self, query: str, max_results: Optional[int] = None) -> str:
        """执行搜索，返回人类可读的纯文本摘要。

        适合直接作为工具调用的返回值。

        Args:
            query: 搜索关键词
            max_results: 覆盖默认最大结果数

        Returns:
            格式化的纯文本搜索结果，或提示信息
        """
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
        """执行搜索，返回 JSON 格式结果（方便程序处理）。"""
        import json
        results = self.search(query, max_results)
        data = {
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ========== 内部方法 ==========

    def _fetch(self, query: str) -> Optional[str]:
        """发起 HTTP 请求，获取搜索结果 HTML。"""
        params = {"q": query}

        for attempt in range(1, self._max_retries + 2):
            try:
                resp = self._session.get(
                    self._BASE_URL,
                    params=params,
                    timeout=self._timeout,
                )
                resp.raise_for_status()

                # DuckDuckGo 有时会返回重定向或验证页面
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    logger.debug("非 HTML 响应: Content-Type=%s", content_type)

                return resp.text

            except requests.Timeout:
                logger.warning("请求超时 (尝试 %d/%d): %s", attempt,
                               self._max_retries + 1, query)
            except requests.HTTPError as e:
                logger.warning("HTTP 错误 (尝试 %d/%d): %s", attempt,
                               self._max_retries + 1, e)
            except requests.RequestException as e:
                logger.warning("请求异常 (尝试 %d/%d): %s", attempt,
                               self._max_retries + 1, e)

            if attempt <= self._max_retries:
                time.sleep(1.0 * attempt)  # 递增退避

        logger.error("搜索 '%s' 失败，已重试 %d 次", query, self._max_retries + 1)
        return None

    def _parse_results(self, html: str, limit: int) -> List[WebSearchResult]:
        """从 DuckDuckGo Lite HTML 中解析搜索结果。

        DDG Lite 的结果结构（简化）::

            <tr>
              <td>1.&nbsp;</td>
              <td><a class='result-link' href="...">标题</a></td>
            </tr>
            <tr>
              <td>&nbsp;&nbsp;&nbsp;</td>
              <td class='result-snippet'>摘要...</td>
            </tr>
            <tr>
              <td>&nbsp;&nbsp;&nbsp;</td>
              <td><span class='link-text'>显示URL</span></td>
            </tr>
        """
        results: List[WebSearchResult] = []

        # 匹配每个结果块：result-link → result-snippet → link-text
        # 使用更健壮的方式：先找到所有 result-link
        # 两步匹配：先找 class=result-link 的 <a> 标签，再从中提取 href 和文本
        # （因为 href 和 class 在标签中的顺序不固定）
        _tag_pattern = re.compile(
            r'<a\s+[^>]*class\s*=\s*[\'"]result-link[\'"][^>]*>.*?</a>',
            re.DOTALL | re.IGNORECASE,
        )
        _href_pattern = re.compile(
            r'href\s*=\s*[\'"]([^\'"]*?)[\'"]',
            re.IGNORECASE,
        )
        _title_pattern = re.compile(
            r'<a[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        snippet_pattern = re.compile(
            r'<td\s+[^>]*?class\s*=\s*[\'"]result-snippet[\'"][^>]*?>'
            r'(.*?)</td>',
            re.DOTALL | re.IGNORECASE,
        )

        link_text_pattern = re.compile(
            r'<span\s+[^>]*?class\s*=\s*[\'"]link-text[\'"][^>]*?>'
            r'(.*?)</span>',
            re.DOTALL | re.IGNORECASE,
        )

        # 两步提取
        raw_tags = _tag_pattern.findall(html)
        links = []
        for tag in raw_tags:
            href_m = _href_pattern.search(tag)
            title_m = _title_pattern.search(tag)
            if href_m and title_m:
                links.append((href_m.group(1), title_m.group(1).strip()))
        snippets = snippet_pattern.findall(html)
        link_texts = link_text_pattern.findall(html)

        for i, (raw_url, raw_title) in enumerate(links):
            if len(results) >= limit:
                break

            url = self._clean_url(raw_url)
            title = clean_html(raw_title).strip()

            snippet = ""
            if i < len(snippets):
                snippet = clean_html(snippets[i]).strip()

            display_url = ""
            if i < len(link_texts):
                display_url = clean_html(link_texts[i]).strip()

            # 跳过空标题
            if not title:
                continue

            results.append(WebSearchResult(
                title=title,
                url=url,
                snippet=snippet,
                display_url=display_url,
            ))

        return results

    @staticmethod
    def _clean_url(raw_url: str) -> str:
        """清理 DuckDuckGo 的跳转 URL，提取真实目标 URL。

        DDG Lite 的 href 形如::

            //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&rut=...

        需要提取 uddg 参数并 URL 解码。
        """
        from urllib.parse import unquote

        # 补齐协议
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url

        # 尝试提取 uddg 参数
        match = re.search(r'uddg=([^&]+)', raw_url)
        if match:
            return unquote(match.group(1))

        # 如果没找到 uddg，返回原始 URL（去掉协议缺失）
        return raw_url


# ============ 工具函数（可直接在 Daemon 中注册为 tool） ============


def tool_web_search(query: str, max_results: int = 10) -> str:
    """供 Daemon 直接调用的网络搜索工具函数。

    返回人类可读的纯文本搜索结果。
    """
    wse = WebSearchEngine(max_results=max_results)
    return wse.search_text(query, max_results=max_results)


# ============ 入口测试 ============

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    engine = WebSearchEngine(max_results=5)

    if len(sys.argv) < 2:
        # 默认测试搜索
        test_query = "Python asyncio tutorial"
        print(f"🔍 测试搜索: {test_query}")
        print(engine.search_text(test_query))
        print("\n--- JSON 格式 ---")
        print(engine.search_json(test_query))
    else:
        query = " ".join(sys.argv[1:])
        print(engine.search_text(query))
