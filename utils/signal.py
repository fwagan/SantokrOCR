"""
统一的 Signal 类 + 线程安全调度

取代 core/camera_capture.py 和 ui/async_worker.py 中重复的 Signal 实现。
"""

import threading


class Signal:
    """线程安全的信号类，用于线程间通信"""

    def __init__(self):
        self._callbacks = []
        self._lock = threading.Lock()

    def connect(self, callback):
        """连接回调函数"""
        with self._lock:
            self._callbacks.append(callback)

    def disconnect(self, callback):
        """断开回调函数"""
        with self._lock:
            self._callbacks.remove(callback)

    def emit(self, *args, **kwargs):
        """触发信号（在调用者线程同步执行回调）"""
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(*args, **kwargs)
            except Exception as e:
                import traceback
                print(f"Signal callback error: {e}")
                traceback.print_exc()
