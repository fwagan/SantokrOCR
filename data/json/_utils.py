"""
JSON Repository 通用工具

提供线程安全的原子读写，所有 Repository 共享相同锁，
保证同进程内 JSON 文件 IO 不会并发交错。
"""

import json
import os
import threading
from typing import Any, Optional

import logging

logger = logging.getLogger(__name__)

# 全局锁：所有 JSON Repository 共享同一把锁
json_lock = threading.Lock()


def atomic_write(path: str, data: Any) -> None:
    """原子写入 JSON 文件：先写临时文件再 rename"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_json(path: str) -> Optional[Any]:
    """安全加载 JSON 文件，不存在或失败返回 None"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"JSON 加载失败: {path}, 错误: {e}")
        return None


def safe_serialize(results: list) -> list:
    """确保结果数据可 JSON 序列化"""
    serialized = []
    for item in results:
        row = {}
        for key, value in item.items():
            if isinstance(value, (int, float, str, bool, type(None))):
                row[key] = value
            else:
                row[key] = str(value)
        serialized.append(row)
    return serialized
