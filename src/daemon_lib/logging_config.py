# -*- coding: utf-8 -*-
"""日志配置模块 — 为 Daemon 提供统一的日志初始化。"""

import sys
import logging
from pathlib import Path


def setup_logging(log_dir: Path = None, debug: bool = False) -> logging.Logger:
    """配置并返回模块级 logger。

    Args:
        log_dir: 日志文件所在目录，默认为调用者所在目录
        debug: True 则日志级别为 INFO，否则为 WARNING

    Returns:
        配置完成的 logger 实例
    """
    if log_dir is None:
        log_dir = Path.cwd()

    log_file = log_dir / 'agent.log'
    log_level = logging.INFO if debug else logging.WARNING

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)
