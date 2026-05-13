"""
帧缓存管理

LRU缓存机制，减少视频帧的重复读取。
"""

import threading
import cv2
from functools import lru_cache


class FrameCache:
    """帧缓存管理（LRU缓存）"""

    def __init__(self, maxsize=100):
        self.cache = {}
        self.lock = threading.Lock()
        self.maxsize = maxsize
        self.access_order = []  # 用于LRU策略

    def get_frame(self, video_path, timestamp):
        """
        获取指定时间戳的视频帧（带缓存）
        返回: frame 或 None
        """
        key = (video_path, timestamp)

        with self.lock:
            # 检查缓存
            if key in self.cache:
                # 更新访问顺序
                self.access_order.remove(key)
                self.access_order.append(key)
                return self.cache[key]

        # 缓存未命中，从视频读取
        frame = self._read_frame_from_video(video_path, timestamp)
        if frame is not None:
            with self.lock:
                # 添加到缓存
                self.cache[key] = frame
                self.access_order.append(key)

                # 如果超过最大大小，移除最久未使用的
                if len(self.cache) > self.maxsize:
                    oldest_key = self.access_order.pop(0)
                    del self.cache[oldest_key]

        return frame

    def _read_frame_from_video(self, video_path, timestamp):
        """从视频文件读取指定时间戳的帧"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int(timestamp * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        cap.release()

        if ret:
            return frame
        return None

    def clear(self):
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()

    def get_size(self):
        """获取当前缓存大小"""
        with self.lock:
            return len(self.cache)

    def get_hit_rate(self):
        """获取缓存命中率统计（需要外部记录）"""
        # 这个类不自动统计命中率，需要外部调用时记录
        pass


# 使用Python内置的lru_cache的简化版本（线程不安全）
def create_lru_cache_function(maxsize=100):
    """创建使用functools.lru_cache的缓存函数"""
    @lru_cache(maxsize=maxsize)
    def cached_read_frame(video_path, timestamp):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int(timestamp * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        cap.release()

        return frame if ret else None

    return cached_read_frame


if __name__ == "__main__":
    # 简单测试
    import time

    cache = FrameCache(maxsize=3)

    # 模拟读取帧
    test_key1 = ("test_video.mp4", 10.0)
    test_key2 = ("test_video.mp4", 20.0)
    test_key3 = ("test_video.mp4", 30.0)
    test_key4 = ("test_video.mp4", 40.0)

    # 模拟缓存数据
    cache.cache[test_key1] = "frame1"
    cache.cache[test_key2] = "frame2"
    cache.access_order = [test_key1, test_key2]

    print("初始缓存:", cache.get_size())

    # 测试LRU淘汰
    cache.cache[test_key3] = "frame3"
    cache.access_order.append(test_key3)
    print("添加第三个帧后:", cache.get_size())

    # 添加第四个帧，应该淘汰第一个
    cache.cache[test_key4] = "frame4"
    cache.access_order.append(test_key4)
    if len(cache.cache) > cache.maxsize:
        oldest = cache.access_order.pop(0)
        del cache.cache[oldest]

    print("添加第四个帧后:", cache.get_size())
    print("当前缓存键:", list(cache.cache.keys()))