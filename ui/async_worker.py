"""
异步工作线程

处理视频的异步线程，避免阻塞UI。
使用信号机制与主线程通信。
"""

import threading
import time

from utils.signal import Signal


class ProcessingThread(threading.Thread):
    """处理视频的异步线程"""

    def __init__(self, extractor, video_path, rois, interval=0.25):
        super().__init__()
        self.extractor = extractor
        self.video_path = video_path
        self.rois = rois
        self.interval = interval

        # 信号
        self.progress_signal = Signal()  # (processed, total)
        self.status_signal = Signal()    # (message)
        self.result_signal = Signal()    # (result_dict)
        self.finished_signal = Signal()  # (success, message)

        # 控制标志
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始状态为运行

        # 结果存储
        self.results = []

    def run(self):
        """线程主函数"""
        try:
            self.status_signal.emit("正在初始化处理...")

            # 调用extractor的异步处理方法
            thread = self.extractor.process_video_async(
                video_path=self.video_path,
                rois=self.rois,
                interval=self.interval,
                progress_callback=self._on_progress,
                status_callback=self._on_status,
                result_callback=self._on_result
            )

            # 等待处理线程完成（但需要响应停止/暂停事件）
            while thread.is_alive():
                if self._stop_event.is_set():
                    self.extractor.stop_processing()
                    thread.join(timeout=1)
                    break

                if not self._pause_event.is_set():
                    # 暂停状态
                    time.sleep(0.5)
                    continue

                thread.join(timeout=0.5)

            # 检查线程是否正常结束
            if not self._stop_event.is_set():
                self.status_signal.emit("处理完成")
                self.finished_signal.emit(True, "处理完成")
            else:
                self.status_signal.emit("处理已停止")
                self.finished_signal.emit(False, "处理被用户停止")

        except Exception as e:
            error_msg = f"处理过程中出错: {str(e)}"
            self.status_signal.emit(error_msg)
            self.finished_signal.emit(False, error_msg)
            import traceback
            traceback.print_exc()

    def _on_progress(self, processed, total):
        """进度回调"""
        self.progress_signal.emit(processed, total)

    def _on_status(self, message):
        """状态回调"""
        self.status_signal.emit(message)

    def _on_result(self, result):
        """结果回调"""
        self.result_signal.emit(result)

    def stop(self):
        """停止处理"""
        self._stop_event.set()

    def pause(self):
        """暂停处理"""
        self._pause_event.clear()
        self.status_signal.emit("处理已暂停")

    def resume(self):
        """继续处理"""
        self._pause_event.set()
        self.status_signal.emit("继续处理")

    def is_paused(self):
        """检查是否暂停"""
        return not self._pause_event.is_set()

    def is_stopped(self):
        """检查是否停止"""
        return self._stop_event.is_set()


if __name__ == "__main__":
    # 简单的测试
    class TestExtractor:
        def process_video_async(self, **kwargs):
            print(f"处理参数: {kwargs}")
            thread = threading.Thread(target=self._mock_process, daemon=True)
            thread.start()
            return thread

        def _mock_process(self):
            time.sleep(3)
            print("模拟处理完成")

    extractor = TestExtractor()
    thread = ProcessingThread(extractor, "test.mp4", {}, 0, 0.25)

    # 连接信号
    def on_progress(processed, total):
        print(f"进度: {processed}/{total}")

    def on_status(message):
        print(f"状态: {message}")

    def on_finished(success, message):
        print(f"完成: {success}, {message}")

    thread.progress_signal.connect(on_progress)
    thread.status_signal.connect(on_status)
    thread.finished_signal.connect(on_finished)

    thread.start()
    thread.join()