"""
缓存管理模块

功能：
1. 计算视频文件hash（基于文件内容和修改时间）
2. 管理memory文件夹下的缓存数据
3. 保存和加载ROI配置、识别结果
4. 支持缓存失效检查（文件修改时间变化）

缓存结构：
memory/
  {video_hash}/
    video_info.json    # 视频基本信息（路径、大小、修改时间）
    rois.json          # ROI配置列表
    results.json       # 识别结果列表
    user_edits.json    # 用户编辑记录（可选）
"""

import os
import json
import hashlib
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class CacheManager:
    """缓存管理器"""

    def __init__(self, base_dir: str = None):
        """
        初始化缓存管理器

        Args:
            base_dir: 缓存基础目录。
              打包后默认使用 %APPDATA%/SantokrOCR/memory（Windows）
              开发环境默认使用项目根目录下的 memory 文件夹
        """
        if base_dir is None:
            import sys
            app_data = os.environ.get(
                'APPDATA',
                os.path.expanduser('~/.local/share')
            )
            base_dir = os.path.join(app_data, 'SantokrOCR', 'VideoProcessCache')

        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"缓存目录: {self.base_dir}")

    def compute_video_hash(self, video_path: str) -> str:
        """
        计算视频文件的hash值

        基于文件内容的前1MB + 文件大小 + 最后修改时间计算MD5，
        避免大文件读取耗时过长。

        Args:
            video_path: 视频文件路径

        Returns:
            MD5 hash字符串
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 获取文件信息
        stat = os.stat(video_path)
        file_size = stat.st_size
        mtime = stat.st_mtime

        # 计算文件内容hash（只读取前1MB和最后1MB，避免大文件）
        md5 = hashlib.md5()

        # 添加文件大小和修改时间
        md5.update(str(file_size).encode('utf-8'))
        md5.update(str(mtime).encode('utf-8'))

        # 读取文件部分内容
        try:
            with open(video_path, 'rb') as f:
                # 读取前1MB
                data = f.read(1024 * 1024)
                md5.update(data)

                # 如果文件大于2MB，读取最后1MB
                if file_size > 2 * 1024 * 1024:
                    f.seek(-1024 * 1024, os.SEEK_END)
                    data = f.read(1024 * 1024)
                    md5.update(data)
        except Exception as e:
            logger.warning(f"读取视频文件失败，使用简化hash: {e}")
            # 如果读取失败，只使用文件信息
            pass

        return md5.hexdigest()

    def get_cache_dir(self, video_hash: str) -> str:
        """
        获取视频hash对应的缓存目录

        Args:
            video_hash: 视频hash值

        Returns:
            缓存目录路径
        """
        cache_dir = os.path.join(self.base_dir, video_hash)
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def save_video_info(self, video_path: str, video_hash: str) -> str:
        """
        保存视频基本信息

        Args:
            video_path: 视频文件路径
            video_hash: 视频hash值

        Returns:
            保存的文件路径
        """
        cache_dir = self.get_cache_dir(video_hash)
        info_path = os.path.join(cache_dir, 'video_info.json')

        stat = os.stat(video_path)
        video_info = {
            'video_path': video_path,
            'video_hash': video_hash,
            'file_size': stat.st_size,
            'modified_time': stat.st_mtime,
            'created_time': stat.st_ctime,
            'cache_time': time.time(),
            'cache_date': datetime.now().isoformat()
        }

        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(video_info, f, indent=2, ensure_ascii=False)

        logger.info(f"视频信息已保存: {info_path}")
        return info_path

    def save_rois(self, video_hash: str, rois) -> str:
        """
        保存ROI配置

        Args:
            video_hash: 视频hash值
            rois: ROI配置（字典：{name: (x, y, w, h)} 或 列表）

        Returns:
            保存的文件路径
        """
        cache_dir = self.get_cache_dir(video_hash)
        rois_path = os.path.join(cache_dir, 'rois.json')

        # 确保ROI数据可序列化
        serializable_rois = {}
        if isinstance(rois, dict):
            # 字典格式：{name: (x, y, w, h)}
            for name, roi in rois.items():
                if isinstance(roi, (tuple, list)) and len(roi) == 4:
                    x, y, w, h = roi
                    serializable_rois[name] = {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
                elif isinstance(roi, dict) and 'x' in roi and 'y' in roi:
                    # 已经是字典格式
                    serializable_rois[name] = roi
                else:
                    serializable_rois[name] = str(roi)
        elif isinstance(rois, list):
            # 列表格式（向后兼容）
            for roi in rois:
                if isinstance(roi, dict) and 'name' in roi:
                    # 列表中的字典，包含name字段
                    name = roi['name']
                    if 'x' in roi and 'y' in roi:
                        serializable_rois[name] = {'x': roi['x'], 'y': roi['y'],
                                                  'width': roi.get('width', roi.get('w', 0)),
                                                  'height': roi.get('height', roi.get('h', 0))}
                elif isinstance(roi, dict):
                    # 没有name字段的字典，无法处理
                    logger.warning(f"跳过无法处理的ROI格式: {roi}")
        else:
            logger.warning(f"未知的ROI格式: {type(rois)}")
            serializable_rois = {'error': f'unknown_roi_format_{type(rois)}'}

        with open(rois_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_rois, f, indent=2, ensure_ascii=False)

        logger.info(f"ROI配置已保存: {rois_path} (共{len(serializable_rois)}个ROI)")
        return rois_path

    def save_results(self, video_hash: str, results: List[Dict]) -> str:
        """
        保存识别结果

        Args:
            video_hash: 视频hash值
            results: 识别结果列表

        Returns:
            保存的文件路径
        """
        cache_dir = self.get_cache_dir(video_hash)
        results_path = os.path.join(cache_dir, 'results.json')

        # 转换结果数据为可序列化格式
        serializable_results = []
        for result in results:
            serializable_result = {}
            for key, value in result.items():
                # 处理特殊类型
                if isinstance(value, (int, float, str, bool, type(None))):
                    serializable_result[key] = value
                elif hasattr(value, '__dict__'):
                    # 对象转换为字典
                    serializable_result[key] = str(value)
                else:
                    # 其他类型转换为字符串
                    serializable_result[key] = str(value)
            serializable_results.append(serializable_result)

        with open(results_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)

        logger.info(f"识别结果已保存: {results_path} (共{len(serializable_results)}条记录)")
        return results_path

    def load_video_info(self, video_hash: str) -> Optional[Dict]:
        """
        加载视频基本信息

        Args:
            video_hash: 视频hash值

        Returns:
            视频信息字典，如果不存在返回None
        """
        cache_dir = self.get_cache_dir(video_hash)
        info_path = os.path.join(cache_dir, 'video_info.json')

        if not os.path.exists(info_path):
            return None

        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载视频信息失败: {info_path}, 错误: {e}")
            return None

    def load_rois(self, video_hash: str):
        """
        加载ROI配置

        Args:
            video_hash: 视频hash值

        Returns:
            ROI配置字典 {name: (x, y, w, h)}，如果不存在返回None
        """
        cache_dir = self.get_cache_dir(video_hash)
        rois_path = os.path.join(cache_dir, 'rois.json')

        if not os.path.exists(rois_path):
            return None

        try:
            with open(rois_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)

            # 转换回原始格式
            rois = {}
            if isinstance(loaded_data, dict):
                for name, roi_data in loaded_data.items():
                    if isinstance(roi_data, dict) and 'x' in roi_data and 'y' in roi_data:
                        x = roi_data['x']
                        y = roi_data['y']
                        w = roi_data.get('width', roi_data.get('w', 0))
                        h = roi_data.get('height', roi_data.get('h', 0))
                        rois[name] = (int(x), int(y), int(w), int(h))
                    elif isinstance(roi_data, (list, tuple)) and len(roi_data) == 4:
                        rois[name] = tuple(int(v) for v in roi_data)
                    else:
                        logger.warning(f"无法解析ROI数据格式: {name}={roi_data}")
            elif isinstance(loaded_data, list):
                # 向后兼容：列表格式
                for roi_item in loaded_data:
                    if isinstance(roi_item, dict) and 'name' in roi_item:
                        name = roi_item['name']
                        if 'x' in roi_item and 'y' in roi_item:
                            x = roi_item['x']
                            y = roi_item['y']
                            w = roi_item.get('width', roi_item.get('w', 0))
                            h = roi_item.get('height', roi_item.get('h', 0))
                            rois[name] = (int(x), int(y), int(w), int(h))

            logger.info(f"ROI配置已加载: {rois_path} (共{len(rois)}个ROI)")
            return rois if rois else None
        except Exception as e:
            logger.error(f"加载ROI配置失败: {rois_path}, 错误: {e}")
            return None

    def save_events(self, video_hash: str, events: List[Dict]) -> str:
        """
        保存事件列表

        Args:
            video_hash: 视频hash值
            events: 事件列表

        Returns:
            保存的文件路径
        """
        cache_dir = self.get_cache_dir(video_hash)
        events_path = os.path.join(cache_dir, 'events.json')

        with open(events_path, 'w', encoding='utf-8') as f:
            json.dump(events, f, indent=2, ensure_ascii=False)

        logger.info(f"事件已保存: {events_path} (共{len(events)}条)")
        return events_path

    def load_events(self, video_hash: str) -> Optional[List[Dict]]:
        """
        加载事件列表

        Args:
            video_hash: 视频hash值

        Returns:
            事件列表，如果不存在返回None
        """
        cache_dir = self.get_cache_dir(video_hash)
        events_path = os.path.join(cache_dir, 'events.json')

        if not os.path.exists(events_path):
            return None

        try:
            with open(events_path, 'r', encoding='utf-8') as f:
                events = json.load(f)
                logger.info(f"事件已加载: {events_path} (共{len(events)}条)")
                return events
        except Exception as e:
            logger.error(f"加载事件失败: {events_path}, 错误: {e}")
            return None

    def load_results(self, video_hash: str) -> Optional[List[Dict]]:
        """
        加载识别结果

        Args:
            video_hash: 视频hash值

        Returns:
            识别结果列表，如果不存在返回None
        """
        cache_dir = self.get_cache_dir(video_hash)
        results_path = os.path.join(cache_dir, 'results.json')

        if not os.path.exists(results_path):
            return None

        try:
            with open(results_path, 'r', encoding='utf-8') as f:
                results = json.load(f)
                logger.info(f"识别结果已加载: {results_path} (共{len(results)}条记录)")
                return results
        except Exception as e:
            logger.error(f"加载识别结果失败: {results_path}, 错误: {e}")
            return None

    def check_cache_valid(self, video_path: str, video_hash: str) -> bool:
        """
        检查缓存是否有效（视频文件未修改）

        Args:
            video_path: 视频文件路径
            video_hash: 视频hash值

        Returns:
            True表示缓存有效，False表示缓存已过期
        """
        video_info = self.load_video_info(video_hash)
        if not video_info:
            return False

        # 检查文件是否存在
        if not os.path.exists(video_path):
            logger.warning(f"视频文件不存在: {video_path}")
            return False

        # 检查文件大小和修改时间
        stat = os.stat(video_path)
        cached_size = video_info.get('file_size')
        cached_mtime = video_info.get('modified_time')

        if cached_size != stat.st_size or abs(cached_mtime - stat.st_mtime) > 1.0:
            logger.info(f"缓存已过期: 文件已修改 (大小: {cached_size} -> {stat.st_size}, 时间: {cached_mtime} -> {stat.st_mtime})")
            return False

        # 重新计算hash验证
        current_hash = self.compute_video_hash(video_path)
        if current_hash != video_hash:
            logger.info(f"缓存已过期: hash不匹配 ({video_hash} -> {current_hash})")
            return False

        return True

    def clear_cache(self, video_hash: str = None):
        """
        清除缓存

        Args:
            video_hash: 指定视频的缓存，如果为None则清除所有缓存
        """
        if video_hash:
            cache_dir = os.path.join(self.base_dir, video_hash)
            if os.path.exists(cache_dir):
                import shutil
                shutil.rmtree(cache_dir)
                logger.info(f"已清除缓存: {cache_dir}")
        else:
            if os.path.exists(self.base_dir):
                import shutil
                shutil.rmtree(self.base_dir)
                os.makedirs(self.base_dir, exist_ok=True)
                logger.info(f"已清除所有缓存: {self.base_dir}")

    def get_cache_size(self) -> int:
        """
        获取缓存总大小（字节）

        Returns:
            缓存目录总大小
        """
        total_size = 0
        if not os.path.exists(self.base_dir):
            return 0

        for dirpath, dirnames, filenames in os.walk(self.base_dir):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                total_size += os.path.getsize(filepath)

        return total_size

    def list_cached_videos(self) -> List[Dict]:
        """
        列出所有缓存的视频

        Returns:
            缓存视频信息列表
        """
        cached_videos = []
        if not os.path.exists(self.base_dir):
            return cached_videos

        for video_hash in os.listdir(self.base_dir):
            cache_dir = os.path.join(self.base_dir, video_hash)
            if not os.path.isdir(cache_dir):
                continue

            video_info = self.load_video_info(video_hash)
            if video_info:
                cached_videos.append({
                    'video_hash': video_hash,
                    'video_path': video_info.get('video_path', '未知'),
                    'cache_date': video_info.get('cache_date', '未知'),
                    'has_rois': os.path.exists(os.path.join(cache_dir, 'rois.json')),
                    'has_results': os.path.exists(os.path.join(cache_dir, 'results.json'))
                })

        return cached_videos


# 全局缓存管理器实例
_cache_manager = None

def get_cache_manager() -> CacheManager:
    """获取全局缓存管理器实例"""
    global _cache_manager
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