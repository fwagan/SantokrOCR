"""JsonEventRepository：事件存储"""

import os
from typing import List, Optional

import logging

from data.json._utils import json_lock, atomic_write, load_json, safe_serialize
from data.types import EventRecord

logger = logging.getLogger(__name__)

_FILENAME = "events.json"


class JsonEventRepository:
    """JSON 文件存储的事件仓库"""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _file_path(self, video_hash: str) -> str:
        return os.path.join(self.base_dir, video_hash, _FILENAME)

    def save(self, video_hash: str, events: List[EventRecord]) -> None:
        path = self._file_path(video_hash)
        serialized = safe_serialize(events)
        with json_lock:
            atomic_write(path, serialized)
        logger.info(f"事件已保存: {path} ({len(serialized)} 条)")

    def load(self, video_hash: str) -> Optional[List[EventRecord]]:
        path = self._file_path(video_hash)
        data = load_json(path)
        if data is None:
            return None
        if not isinstance(data, list):
            logger.error(f"事件数据格式异常（期望 list，实际 {type(data).__name__}）: {path}")
            return None
        logger.info(f"事件已加载: {path} ({len(data)} 条)")
        return data

    def delete(self, video_hash: str) -> None:
        path = self._file_path(video_hash)
        with json_lock:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"事件已删除: {path}")

    def exists(self, video_hash: str) -> bool:
        return os.path.exists(self._file_path(video_hash))
