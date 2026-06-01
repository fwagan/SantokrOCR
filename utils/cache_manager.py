"""
缓存管理模块

功能：
1. 计算视频文件hash（基于文件内容和修改时间）
2. 管理缓存数据
3. 保存和加载ROI配置、识别结果
4. 支持缓存失效检查（文件修改时间变化）

缓存结构：
{base_dir}/
  {video_hash}/
    video_info.json    # 视频基本信息（路径、大小、修改时间）
    rois.json          # ROI配置列表
    results.json       # 识别结果列表
    events.json        # 事件列表
"""

import os
import time
import json
import logging
from typing import Dict, List, Optional

from data.facade import CacheFacade
from data.json._utils import json_lock, atomic_write, load_json

logger = logging.getLogger(__name__)


class CacheManager:
    """缓存管理器（内部委托给 CacheFacade）"""

    def __init__(self, base_dir: str = None):
        self._facade = CacheFacade(base_dir)
        self.base_dir = self._facade.base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"缓存目录: {self.base_dir}")

    # ── 视频哈希 ──

    def compute_video_hash(self, video_path: str) -> str:
        return self._facade.compute_video_hash(video_path)

    def get_cache_dir(self, video_hash: str) -> str:
        cache_dir = os.path.join(self.base_dir, video_hash)
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    # ── 视频信息 ──

    def save_video_info(self, video_path: str, video_hash: str) -> str:
        return self._facade.save_video_info(video_path, video_hash)

    def load_video_info(self, video_hash: str) -> Optional[Dict]:
        return self._facade.load_video_info(video_hash)

    # ── ROI ──

    def save_rois(self, video_hash: str, rois, rotation_angle: float = None,
                  start_frame: int = None) -> str:
        return self._facade.save_rois(video_hash, rois, rotation_angle, start_frame)

    def load_rois(self, video_hash: str):
        return self._facade.load_rois(video_hash)

    # ── 识别结果 ──

    def save_results(self, video_hash: str, results: List[Dict]) -> str:
        return self._facade.save_results(video_hash, results)

    def load_results(self, video_hash: str) -> Optional[List[Dict]]:
        return self._facade.load_results(video_hash)

    # ── 事件 ──

    def save_events(self, video_hash: str, events: List[Dict]) -> str:
        return self._facade.save_events(video_hash, events)

    def load_events(self, video_hash: str) -> Optional[List[Dict]]:
        return self._facade.load_events(video_hash)

    # ── 摄像头 ROI 持久缓存 ──

    CAMERA_ROI_CACHE_FILE = "camera_roi_cache.json"

    def save_camera_rois(self, camera_index: int, rois: dict) -> None:
        cache_file = os.path.join(self.base_dir, self.CAMERA_ROI_CACHE_FILE)
        with json_lock:
            cache = load_json(cache_file) or {}
            serializable = {}
            for name, roi in rois.items():
                if isinstance(roi, (tuple, list)) and len(roi) == 4:
                    serializable[name] = [int(v) for v in roi]
                else:
                    serializable[name] = str(roi)
            cache[str(camera_index)] = {
                'rois': serializable,
                'save_time': time.time(),
            }
            atomic_write(cache_file, cache)
        logger.info(f"摄像头ROI已缓存: camera {camera_index} ({len(serializable)}个ROI)")

    def load_camera_rois(self, camera_index: int) -> Optional[dict]:
        cache_file = os.path.join(self.base_dir, self.CAMERA_ROI_CACHE_FILE)
        cache = load_json(cache_file)
        if not cache:
            return None
        entry = cache.get(str(camera_index))
        if not entry or 'rois' not in entry:
            return None
        rois = {}
        for name, roi_data in entry['rois'].items():
            if isinstance(roi_data, list) and len(roi_data) == 4:
                rois[name] = tuple(roi_data)
            else:
                rois[name] = roi_data
        logger.info(f"摄像头ROI已加载: camera {camera_index} ({len(rois)}个ROI)")
        return rois

    # ── 缓存管理 ──

    def check_cache_valid(self, video_path: str, video_hash: str) -> bool:
        return self._facade.check_cache_valid(video_path, video_hash)

    def clear_cache(self, video_hash: str = None):
        self._facade.clear_cache(video_hash)

    def get_cache_size(self) -> int:
        return self._facade.get_cache_size()

    def list_cached_videos(self) -> List[Dict]:
        return self._facade.list_cached_videos()


# 全局缓存管理器实例
_cache_manager = None


def get_cache_manager() -> CacheManager:
    global _cache_manager

    # 环境变量回退：运行旧版直接 IO 实现
    # 如需回退：git checkout -- utils/cache_manager.py
    # 然后在调用前设置 os.environ["SANTOKR_CACHE_BACKEND"] = "legacy"
    if os.environ.get("SANTOKR_CACHE_BACKEND", "").lower() == "legacy":
        raise RuntimeError(
            "SANTOKR_CACHE_BACKEND=legacy 不再可用。"
            "新版 CacheManager 已内置委托，请移除此环境变量使用新版。"
        )

    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


if __name__ == "__main__":
    # 测试代码
    import sys

    # 设置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    cm = CacheManager()
    print(f"缓存目录: {cm.base_dir}")

    if len(sys.argv) > 1:
        video_path = sys.argv[1]
        if os.path.exists(video_path):
            video_hash = cm.compute_video_hash(video_path)
            print(f"视频hash: {video_hash}")

            # 检查缓存
            if cm.check_cache_valid(video_path, video_hash):
                print("缓存有效")
                rois = cm.load_rois(video_hash)
                results = cm.load_results(video_hash)
                print(f"ROI配置: {rois}")
                print(f"结果数量: {len(results) if results else 0}")
            else:
                print("缓存无效或不存在")
        else:
            print(f"文件不存在: {video_path}")
    else:
        # 列出所有缓存
        cached_videos = cm.list_cached_videos()
        print(f"缓存视频数量: {len(cached_videos)}")
        for video in cached_videos:
            print(f"  - {video['video_hash']}: {video['video_path']}")
            print(f"    ROI: {video['has_rois']}, 结果: {video['has_results']}")