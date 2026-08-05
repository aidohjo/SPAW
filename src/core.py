#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import time
import logging
import datetime
import subprocess
import threading
from pathlib import Path
from typing import Optional, Any
from openai import OpenAI
from web_search import WebSearchEngine
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ 日志配置 ============
log_dir = Path(__file__).parent          # 日志放在 src/ 目录下
log_file = log_dir / 'agent.log'
running_flags = sys.argv[len(sys.argv)-1] # debugging or normal 

if running_flags and running_flags == "debugging":
    log_level = logging.INFO
else:
    log_level = logging.WARNING

logging.basicConfig(
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



# ============ 核心类：Daemon ============
class Daemon:

    def __init__(self, config_path="config.json"):
        # 1. 加载配置文件
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件 {config_path} 未找到")
        
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # 2. API 配置
        api_config = self.config.get('api', {})
        self.model = api_config.get('model', 'deepseek-v4-pro')
        self.temperature = api_config.get('temperature', 0.7)
        self.max_tokens = api_config.get('max_tokens', 2048)
        
        api_key = api_config.get('api_key') or os.getenv("DEEPSEEK_API_KEY") 
        if not api_key:
            raise ValueError("未设置 DEEPSEEK_API_KEY 环境变量或配置文件中缺少 api_key")
        
        base_url = api_config.get('base_url', 'https://api.deepseek.com')
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        
        # 3. 记忆路径
        memory_config = self.config.get('memory', {})
        self.short_term_dir = Path(memory_config.get('short_term_dir', 'memory/short_term/'))
        self.long_term_path = Path(memory_config.get('long_term_db', 'memory/memory.json'))
        
        self.short_term_dir.mkdir(parents=True, exist_ok=True)
        self.long_term_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 4. 加载长期记忆
        self.memory = self._load_long_term()
        
        # 5. 系统提示与工具
        self.tools = self.config.get('tools', [])
        
        # 6. 安全设置
        safety = self.config.get('safety', {})
        self.timeout = safety.get('timeout', 30)
        self.blacklist = safety.get('blacklist', [])
        
        # 7. （可选）实例化 Memory_manager，以便后续使用
        # self.memory_mgr = Memory_manager(self.short_term_dir.parent)  # 根据需要
        logger.info("Daemon 初始化完成")

        #8. 加载搜索引擎
        self.se = WebSearchEngine()

        #9. 记忆操作锁（可重入，线程安全）
        self._memory_lock = threading.RLock()

    # ============ 记忆管理 ============
    
    def _load_long_term(self):
        """加载长期记忆 JSON 文件"""
        if self.long_term_path.exists():
            try:
                with open(self.long_term_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                logger.warning("长期记忆文件损坏，将重新初始化")
                return {}
        return {}
    
    def save_long_term(self):
        """保存长期记忆到文件（线程安全）"""
        with self._memory_lock:
            with open(self.long_term_path, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)

    def save_long_term_nolock(self):
        """保存长期记忆到文件（调用者必须已持有 _memory_lock）"""
        with open(self.long_term_path, 'w', encoding='utf-8') as f:
            json.dump(self.memory, f, ensure_ascii=False, indent=2)
    
    def get_memory(self, key, default=None):
        return self.memory.get(key, default)
    
    def set_memory(self, key, value):
        with self._memory_lock:
            self.memory[key] = value
            
    def append_to_short_term(self, role, content):
        """追加一条消息到今日短期记忆文件（JSONL）"""
        if role == "tool":   # 不存储工具消息
            return
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = self.short_term_dir / f"{today}.jsonl"
        entry = {
            "role": role,
            "content": content,
            }
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def load_recent_short_term(self, limit: int =50):
        """加载今日最近的 N 条短期记忆"""
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = self.short_term_dir / f"{today}.jsonl"
        messages = []
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        messages.append(json.loads(line.strip()))
                    except:
                        continue
        return messages[-limit:] if messages else []


    def _manage_long_term_memory(
        self,
        action: str,
        key: str,
        value: Optional[str] = None,
        index: Optional[int] = None
    ) -> str:
        """
        管理长期记忆的统一接口，对应工具 manage_long_term_memory 的参数。

        Args:
            action: 操作类型，支持 'get', 'set', 'append', 'remove'
            key: 记忆键名
            value: 用于 set 或 append 的值（字符串）
            index: 用于 remove 的列表索引（从 0 开始）

        Returns:
            操作结果描述字符串，或获取到的值（get 操作返回字符串形式）
        """


        if action == "get":
            result = self.get_memory(key, None)
            if result is None:
                return f"Key '{key}' not found."
            if isinstance(result, (list, dict)):
                return json.dumps(result, ensure_ascii=False)
            return str(result)

        # 所有写操作在同一个锁内完成，防止竞态条件
        with self._memory_lock:
            if action == "set":
                if value is None:
                    return "Error: 'value' is required for action 'set'."
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = value
                self.memory[key] = parsed
                self.save_long_term_nolock()
                return f"Successfully set '{key}' to {json.dumps(parsed, ensure_ascii=False)}"

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

    # ============ 工具执行 ============
    
    def _check_safety(self, command):
        if command in self.blacklist:
            logger.error(f'不允许命令：{command}')
            return True
        else:
            return False
    
    def _execute_tool(self, tool_name, args):
    
        tools_map = {
            "execute_command": self._tool_execute_command,
            "manage_long_term_memory":self._manage_long_term_memory,
            "auto_agent":self._run_agent,
            "search":self._search,
            "deep_search":self.se.search_with_context,
            "search_with_url":self.se.fetch_full_content

        }
        if tool_name in tools_map:
            try:
                return tools_map[tool_name](**args)
            except Exception as e:
                logger.error(f"工具 {tool_name} 执行失败: {e}")
                return f"error:{str(e)}"
        else:
            return f"未知工具: {tool_name}"
    

    
    def _tool_execute_command(self, command, timeout=10):
        if self._check_safety(command):
            return f'危险命令：{command}'
        logger.info(f"执行命令: {command}")
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

    def _search(self, key_word:str, limit:int = 8):
        return self.se.search_text(query = key_word, max_results = limit)

        

    # ============ API 请求 ============
    
    def _request(self, messages, tools=None, tool_choice="auto", stream=False, on_chunk=None, **kwargs):
        """发送 API 请求，支持流式和非流式两种模式。
        
        Args:
            stream: 是否启用流式响应
            on_chunk: 流式模式下的文本块回调 on_chunk(text: str)
        
        Returns:
            非流式返回完整 response；流式返回合成 response（含累积的 content 和 tool_calls）
        """
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        params.update(kwargs)
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        
        max_retries = 3
        retry_delay = 1
        for attempt in range(max_retries):
            try:
                logger.info(f"发送请求 (尝试 {attempt+1}/{max_retries}, stream={stream})")
                response = self.client.chat.completions.create(**params)
                
                if not stream:
                    return response
                
                # ---- 流式处理 ----
                collected_content = ""
                collected_tool_calls = []  # list of dicts: {id, function_name, arguments}
                
                for chunk in response:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    
                    # 处理文本增量
                    if delta.content:
                        collected_content += delta.content
                        if on_chunk:
                            on_chunk(delta.content)
                    
                    # 处理工具调用增量（分片累积）
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            # 确保列表足够长
                            while len(collected_tool_calls) <= idx:
                                collected_tool_calls.append({
                                    "id": "", "function_name": "", "arguments": ""
                                })
                            if tc_delta.id:
                                collected_tool_calls[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    collected_tool_calls[idx]["function_name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    collected_tool_calls[idx]["arguments"] += tc_delta.function.arguments
                
                # 构造合成 response 对象（兼容非流式接口）
                from types import SimpleNamespace
                tool_calls_objs = []
                for tc in collected_tool_calls:
                    if tc["function_name"]:  # 有效的工具调用
                        tool_calls_objs.append(SimpleNamespace(
                            id=tc["id"],
                            function=SimpleNamespace(
                                name=tc["function_name"],
                                arguments=tc["arguments"]
                            )
                        ))
                
                msg = SimpleNamespace(
                    content=collected_content or None,
                    tool_calls=tool_calls_objs if tool_calls_objs else None
                )
                choice = SimpleNamespace(message=msg)
                return SimpleNamespace(choices=[choice])
                
            except Exception as e:
                logger.error(f"请求失败: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                else:
                    raise

    # ============ 系统提示构建 ============

    def build_system_prompt(self) -> str:
        """生成系统提示，引导模型自主执行任务并调用 finish_task 完成"""
        base_prompt = self.config.get("system_prompt", "")
        # 确保 base_prompt 为字符串
        if not isinstance(base_prompt, str):
            base_prompt = str(base_prompt)
        extra = (
            "\n\n你是一个能够自主执行任务的智能助手。\n"
            "当收到复杂指令时，请按以下步骤执行：\n"
            "1. **拆解任务**：将用户目标分解为多个可执行的子任务。\n"
            "2. **使用工具**：根据当前子任务调用合适的工具（如 execute_command, manage_long_term_memory）。\n"
            "3. **检查结果**：分析工具返回的结果，判断子任务是否完成。\n"
            "4. **迭代推进**：若未完成则继续调用工具；若完成则进入下一子任务。\n"
            "5. **最终完成**：当所有子任务完成时，务必调用 finish_task 工具，并在 summary 中给出完整总结。\n"
            "注意：每次可以调用多个工具，避免无效循环。若尝试3次仍失败，请说明原因并调用 finish_task 给出当前进展。\n"
        )
        return base_prompt + extra

     # ============ 自循环执行 ============

    def _run_agent(self, goal: str, max_iterations: int = 150, 
                   exit_condition: str = "auto", context: str = "",
                   level_flag: str = "main",
                   short_term_memory: int = 20,
                   on_event: callable = None) -> str:
        """
        自循环执行任务，自动迭代调用工具，直至满足退出条件。

        Args:
            goal: 用户任务描述（目标）
            max_iterations: 最大循环轮次（硬性约束）
            exit_condition: 退出策略 'auto' | 'max_only' | 'complete_only'
            level_flag: 标识当前agent的关系： 'main'| 'son {father_name}'
            context: 可选额外上下文（字符串），会作为系统提示附加信息

        Returns:
            最终的总结字符串
        """
        # 记录用户输入到短期记忆（子 agent 加前缀以避免污染父 agent 上下文）
        tag = f"[{level_flag}] " if level_flag != "main" else ""
        self.append_to_short_term("user", tag + goal)

        # 构建初始消息列表
        system_content = self.build_system_prompt()
        if context:
            system_content += f"\n\n额外上下文信息：\n{context}"
        def _build_memory_context():
            return f"当前长期记忆内容：\n{json.dumps(self.memory, ensure_ascii=False, indent=2)}"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "system", "content": _build_memory_context()}
        ]
        history = self.load_recent_short_term(limit=short_term_memory)
        messages.extend(history)
        messages.append({"role": "user", "content": goal})

        iteration = 0
        final_reply = "任务未完成，达到最大迭代次数。"

        while iteration < max_iterations:
            iteration += 1
            # 每轮迭代开始时刷新长期记忆上下文（反映上轮可能的修改）
            if iteration > 1:
                messages[1]["content"] = _build_memory_context()
            logger.info(f"----- Agent 迭代 {iteration}/{max_iterations} -----")
            if on_event:
                on_event({"type": "iteration_start", "iteration": iteration, "max_iterations": max_iterations})

            # ---- 流式请求 ----
            chunk_buf = []  # 收集流式文本块
            def _on_chunk(text):
                chunk_buf.append(text)
                if on_event:
                    on_event({"type": "text_chunk", "content": text, "iteration": iteration})
            
            try:
                response = self._request(
                    messages, tools=self.tools, tool_choice="auto",
                    stream=True, on_chunk=_on_chunk
                )
            except Exception as e:
                logger.error(f"API 请求失败: {e}")
                final_reply = f"请求错误: {e}"
                break

            choice = response.choices[0]
            message = choice.message
            
            # 用累积的完整内容构造可序列化的消息
            full_content = "".join(chunk_buf) if chunk_buf else (message.content or "")
            serializable_msg = {"role": "assistant", "content": full_content or None}
            if message.tool_calls:
                serializable_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in message.tool_calls
                ]
            messages.append(serializable_msg)

            # 发出完整助手文本事件（debug 用）
            if full_content and on_event:
                on_event({"type": "assistant_text", "content": full_content, "iteration": iteration})

            # 无工具调用 -> 模型自判完成
            if not message.tool_calls:
                if exit_condition == "max_only":
                    # max_only 模式：忽略模型自判，继续迭代
                    logger.info("模型无工具调用，但 max_only 模式继续迭代")
                    continue
                final_reply = message.content or "任务已完成（无工具调用）"
                if on_event:
                    on_event({"type": "done", "summary": final_reply, "iteration": iteration})
                break


            # 处理工具调用
            tool_calls = message.tool_calls
            finished = False

            # ========== 第一步：检查是否有 finish_task ==========
            finish_call = None
            other_calls = []
            for tc in tool_calls:
                if tc.function.name == "finish_task":
                    finish_call = tc  # 取最后一个 finish_task（不应有多个）
                else:
                    other_calls.append(tc)

            # ========== 第二步：如果存在 finish_task，处理它 ==========
            if finish_call:
                args = json.loads(finish_call.function.arguments)
                summary = args.get("summary", "任务已完成，但未提供总结。")
                if exit_condition == "max_only":
                    # max_only 模式：记录 finish_task 但不退出
                    logger.info(f"Agent 主动完成（max_only 模式忽略），总结：{summary}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": finish_call.id,
                        "content": f"finish_task acknowledged (max_only mode, continuing). Summary: {summary}"
                    })
                    if on_event:
                        on_event({"type": "tool_result", "tool_name": "finish_task",
                                  "result": f"(max_only, continuing) {summary}", "iteration": iteration})
                    # 不 break，继续处理 other_calls
                else:
                    final_reply = summary
                    logger.info(f"Agent 主动完成，总结：{summary}")
                    if on_event:
                        on_event({"type": "done", "summary": summary, "iteration": iteration})
                    finished = True
                    break  # 跳出 while 循环

            # ========== 第三步：没有 finish_task，并行执行所有普通工具 ==========
            if not other_calls:
                # 没有普通工具，继续下一轮（可能模型没有调用工具，直接回复）
                continue

            # 先发出所有工具调用事件（让前端/日志知道开始执行）
            if on_event:
                for tc in other_calls:
                    args = json.loads(tc.function.arguments)
                    on_event({
                        "type": "tool_call",
                        "tool_name": tc.function.name,
                        "args": args,
                        "iteration": iteration
                    })

            # 使用线程池并行执行工具
            with ThreadPoolExecutor(max_workers=min(len(other_calls), 8)) as executor:
                # 提交所有任务，保留 future 与 tool_call 的映射
                future_to_call = {
                    executor.submit(self._execute_tool, tc.function.name, json.loads(tc.function.arguments)): tc
                    for tc in other_calls
                }
                
                # 等待所有任务完成，并按原始顺序收集结果
                # 这里有两种策略：
                # 1. 使用 as_completed 按完成顺序处理（更快输出结果事件）
                # 2. 保持原始顺序，方便后续按序添加消息
                # 我们选择 as_completed 以获得更快的反馈，但结果消息的顺序不影响大模型理解（因为 tool_call_id 是唯一标识）
                results = {}  # tool_call_id -> result
                for future in as_completed(future_to_call):
                    tc = future_to_call[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        # 确保工具异常不会导致整个 Agent 崩溃
                        result = f"工具执行出错: {str(e)}"
                        logger.error(f"工具 {tc.function.name} 执行异常: {e}")
                    
                    results[tc.id] = result
                    # 发出单个工具结果事件（可选的，顺序可能乱，但状态是真实的）
                    if on_event:
                        on_event({
                            "type": "tool_result",
                            "tool_name": tc.function.name,
                            "result": str(result)[:500],
                            "iteration": iteration
                        })
                
                # 按原始顺序将工具结果追加到 messages 中（保证对话历史的顺序一致性）
                for tc in other_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": results.get(tc.id, "未获取到结果")
                    })

            # 没有 finish_task，继续下一轮循环
            continue        
        # while 正常结束（达到 max_iterations）
        else:
            if iteration >= max_iterations:
                logger.warning(f"达到最大迭代次数 {max_iterations}，强制终止。")
                if exit_condition == "complete_only":
                    final_reply = f"错误：任务未能在 {max_iterations} 次迭代内完成。"
            else:
                # auto 或 max_only 模式下，返回最后一条助手消息（若有）
                if messages and messages[-1].get("role") == "assistant":
                    last_content = messages[-1].get("content", "")
                    if last_content:
                        final_reply = last_content

        tag = f"[{level_flag}] " if level_flag != "main" else ""
        self.append_to_short_term("assistant", tag + final_reply)
        return final_reply
 

    def close(self):
        quit(0)



if __name__ == "__main__":
    try:
        daemon = Daemon("config.json")
        print("✅ Daemon 启动成功，输入 'exit' 退出。")
        while True:
            user_input = input(">>> ")
            if user_input.lower() in ("exit", "quit"):
                break
            try:
                # 选择使用 _run_agent（推荐）或 chat
                response = daemon._run_agent(
                    goal=user_input,
                    max_iterations=2000,
                    exit_condition="auto"
                )
                print(f"🤖 {response}\n")
            except Exception as e:
                print(f"⚠️ 执行失败: {e}\n")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
    finally:
        # 如果 daemon 实例存在且有关闭方法
        if 'daemon' in locals():
            daemon.close()   
   
