"""
CacheFacade：统一缓存接入层

封装所有 JSON Repository，对外暴露与旧 CacheManager 兼容的方法签名。
"""

import hashlib
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import logging

from data.types import EventRecord, ResultRecord, RoiConfig, VideoInfo

from data.json.event_repo import JsonEventRepository
from data.json.result_repo import JsonResultRepository
from data.json.roi_repo import JsonRoiRepository
from data.json.video_info_repo import JsonVideoInfoRepository

logger = logging.getLogger(__name__)


def _default_base_dir() -> str:
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
    return os.path.join(app_data, 'SantokrOCR', 'VideoProcessCache')


class CacheFacade:
    """缓存接入层，封装所有 JSON Repository"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or _default_base_dir()
        self.result = JsonResultRepository(self.base_dir)
        self.event = JsonEventRepository(self.base_dir)
        self.roi = JsonRoiRepository(self.base_dir)
        self.video_info = JsonVideoInfoRepository(self.base_dir)

    # ============================================================
    # 视频哈希
    # ============================================================

    @staticmethod
    def compute_video_hash(video_path: str) -> str:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        stat = os.stat(video_path)
        file_size = stat.st_size
        mtime = stat.st_mtime

        md5 = hashlib.md5()
        md5.update(str(file_size).encode('utf-8'))
        md5.update(str(mtime).encode('utf-8'))

        try:
            with open(video_path, 'rb') as f:
                data = f.read(1024 * 1024)
                md5.update(data)
                if file_size > 2 * 1024 * 1024:
                    f.seek(-1024 * 1024, os.SEEK_END)
                    data = f.read(1024 * 1024)
                    md5.update(data)
        except Exception as e:
            logger.warning(f"读取视频文件失败，使用简化 hash: {e}")

        return md5.hexdigest()

    # ============================================================
    # 视频元信息
    # ============================================================

    def save_video_info(self, video_path: str, video_hash: str) -> str:
        stat = os.stat(video_path)
        info = {
            'video_path': video_path,
            'video_hash': video_hash,
            'file_size': stat.st_size,
            'modified_time': stat.st_mtime,
            'created_time': stat.st_ctime,
            'cache_time': time.time(),
            'cache_date': datetime.now().isoformat(),
        }
        self.video_info.save(info)
        info_path = os.path.join(self.base_dir, video_hash, 'video_info.json')
        return info_path

    def load_video_info(self, video_hash: str) -> Optional[VideoInfo]:
        return self.video_info.load(video_hash)

    # ============================================================
    # ROI
    # ============================================================

    def save_rois(self, video_hash: str, config: RoiConfig) -> str:
        self.roi.save(video_hash, config)
        return os.path.join(self.base_dir, video_hash, 'rois.json')

    def load_rois(self, video_hash: str) -> Optional[RoiConfig]:
        return self.roi.load(video_hash)

    # ============================================================
    # 识别结果
    # ============================================================

    def save_results(self, video_hash: str, results: List[ResultRecord]) -> str:
        self.result.save(video_hash, results)
        return os.path.join(self.base_dir, video_hash, 'results.json')

    def load_results(self, video_hash: str) -> Optional[List[ResultRecord]]:
        return self.result.load(video_hash)

    # ============================================================
    # 事件
    # ============================================================

    def save_events(self, video_hash: str, events: List[EventRecord]) -> str:
        self.event.save(video_hash, events)
        return os.path.join(self.base_dir, video_hash, 'events.json')

    def load_events(self, video_hash: str) -> Optional[List[EventRecord]]:
        return self.event.load(video_hash)

    # ============================================================
    # 缓存验证 & 管理
    # ============================================================

    def check_cache_valid(self, video_path: str, video_hash: str) -> bool:
        info = self.load_video_info(video_hash)
        if not info:
            return False
        if not os.path.exists(video_path):
            logger.warning(f"视频文件不存在: {video_path}")
            return False

        stat = os.stat(video_path)
        cached_size = info.get('file_size')
        cached_mtime = info.get('modified_time')

        if cached_size != stat.st_size or abs(cached_mtime - stat.st_mtime) > 1.0:
            logger.info(f"缓存过期: 文件已修改")
            return False

        current_hash = self.compute_video_hash(video_path)
        if current_hash != video_hash:
            logger.info(f"缓存过期: hash 不匹配")
            return False

        return True

    def clear_cache(self, video_hash: str = None):
        import shutil
        if video_hash:
            cache_dir = os.path.join(self.base_dir, video_hash)
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
                logger.info(f"已清除缓存: {cache_dir}")
        else:
            if os.path.exists(self.base_dir):
                shutil.rmtree(self.base_dir)
                os.makedirs(self.base_dir, exist_ok=True)
                logger.info(f"已清除所有缓存")

    def get_cache_size(self) -> int:
        total = 0
        if not os.path.exists(self.base_dir):
            return 0
        for dirpath, dirnames, filenames in os.walk(self.base_dir):
            for filename in filenames:
                total += os.path.getsize(os.path.join(dirpath, filename))
        return total

    def list_cached_videos(self) -> List[Dict]:
        result = []
        if not os.path.exists(self.base_dir):
            return result
        for name in os.listdir(self.base_dir):
            cache_dir = os.path.join(self.base_dir, name)
            if not os.path.isdir(cache_dir):
                continue
            info = self.load_video_info(name)
            if info:
                result.append({
                    'video_hash': name,
                    'video_path': info.get('video_path', '未知'),
                    'cache_date': info.get('cache_date', '未知'),
                    'has_rois': self.roi.exists(name),
                    'has_results': self.result.exists(name),
                })
        return result
