"""JsonVideoInfoRepository：视频元信息存储"""

import os
from typing import Dict, List, Optional

import logging

from data.json._utils import json_lock, atomic_write, load_json
from data.types import VideoInfo

logger = logging.getLogger(__name__)

_FILENAME = "video_info.json"


class JsonVideoInfoRepository:
    """JSON 文件存储的视频元信息仓库"""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _file_path(self, video_hash: str) -> str:
        return os.path.join(self.base_dir, video_hash, _FILENAME)

    def save(self, info: VideoInfo) -> None:
        """保存视频信息（info 必须包含 video_hash）"""
        video_hash = info.get('video_hash')
        if not video_hash:
            raise ValueError("video_info 缺少 video_hash 字段")
        path = self._file_path(video_hash)
        with json_lock:
            atomic_write(path, info)
        logger.info(f"视频信息已保存: {path}")

    def load(self, video_hash: str) -> Optional[VideoInfo]:
        path = self._file_path(video_hash)
        return load_json(path)

    def delete(self, video_hash: str) -> None:
        path = self._file_path(video_hash)
        with json_lock:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"视频信息已删除: {path}")

    def list_all(self) -> List[VideoInfo]:
        """列举缓存目录中所有视频信息"""
        result = []
        if not os.path.exists(self.base_dir):
            return result
        for name in os.listdir(self.base_dir):
            cache_dir = os.path.join(self.base_dir, name)
            if not os.path.isdir(cache_dir):
                continue
            info = self.load(name)
            if info:
                result.append(info)
        return result
