"""JsonResultRepository：识别结果存储"""

import logging
import os
from typing import List, Optional

from data.json._utils import atomic_write, json_lock, load_json, safe_serialize
from data.types import ResultRecord

logger = logging.getLogger(__name__)

_FILENAME = "results.json"


class JsonResultRepository:
    """JSON 文件存储的识别结果仓库"""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _file_path(self, video_hash: str) -> str:
        return os.path.join(self.base_dir, video_hash, _FILENAME)

    def save(self, video_hash: str, results: List[ResultRecord]) -> None:
        path = self._file_path(video_hash)
        serialized = safe_serialize(results)
        with json_lock:
            atomic_write(path, serialized)
        logger.info(f"结果已保存: {path} ({len(serialized)} 条)")

    def load(self, video_hash: str) -> Optional[List[ResultRecord]]:
        path = self._file_path(video_hash)
        data = load_json(path)
        if data is None:
            return None
        if not isinstance(data, list):
            logger.error(f"结果数据格式异常（期望 list，实际 {type(data).__name__}）: {path}")
            return None
        logger.info(f"结果已加载: {path} ({len(data)} 条)")
        return data

    def delete(self, video_hash: str) -> None:
        path = self._file_path(video_hash)
        with json_lock:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"结果已删除: {path}")

    def exists(self, video_hash: str) -> bool:
        return os.path.exists(self._file_path(video_hash))
