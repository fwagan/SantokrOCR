"""
异步工作线程

处理视频的异步线程，避免阻塞UI。
使用信号机制与主线程通信。
"""

import threading
import time
import queue


class Signal:
    """简单的信号类，用于线程间通信"""

    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        """连接回调函数"""
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        """触发信号"""
        for callback in self._callbacks:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"信号回调出错: {e}")


class ProcessingThread(threading.Thread):
    """处理视频的异步线程"""

    def __init__(self, extractor, video_path, rois, start_frame=0, interval=0.25):
        super().__init__()
        self.extractor = extractor
        self.video_path = video_path
        self.rois = rois
        self.start_frame = start_frame
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
                start_frame=self.start_frame,
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


class BatchProcessingQueue:
    """批量处理队列"""

    def __init__(self, max_workers=1):
        self.queue = queue.Queue()
        self.max_workers = max_workers
        self.workers = []
        self.running = False

        # 信号
        self.queue_progress_signal = Signal()  # (current, total)
        self.queue_status_signal = Signal()    # (message)
        self.queue_finished_signal = Signal()  # (success, message)

    def add_task(self, video_path, rois, start_frame=0, interval=0.25):
        """添加处理任务"""
        task = {
            'video_path': video_path,
            'rois': rois,
            'start_frame': start_frame,
            'interval': interval,
            'status': 'pending'
        }
        self.queue.put(task)
        return task

    def start(self, extractor_factory):
        """开始处理队列"""
        if self.running:
            return

        self.running = True
        self.queue_status_signal.emit("开始批量处理")

        # 创建工作线程
        for i in range(self.max_workers):
            worker = threading.Thread(target=self._worker_func,
                                     args=(extractor_factory,),
                                     daemon=True)
            worker.start()
            self.workers.append(worker)

    def stop(self):
        """停止所有处理"""
        self.running = False
        self.queue_status_signal.emit("正在停止批量处理...")

        # 清空队列
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

        # 等待工作线程结束
        for worker in self.workers:
            worker.join(timeout=1)

        self.workers.clear()
        self.queue_status_signal.emit("批量处理已停止")

    def _worker_func(self, extractor_factory):
        """工作线程函数"""
        while self.running and not self.queue.empty():
            try:
                task = self.queue.get_nowait()
            except queue.Empty:
                break

            # 更新任务状态
            task['status'] = 'processing'
            self.queue_status_signal.emit(f"开始处理: {task['video_path']}")

            # 创建extractor并处理
            extractor = extractor_factory()
            try:
                # 这里可以调用异步处理方法
                # 简化处理：直接使用同步方法
                results = extractor.process_video(
                    video_path=task['video_path'],
                    rois=task['rois'],
                    start_frame=task['start_frame'],
                    interval=task['interval']
                )

                task['status'] = 'completed'
                task['results'] = results
                self.queue_status_signal.emit(f"完成处理: {task['video_path']}")

            except Exception as e:
                task['status'] = 'failed'
                task['error'] = str(e)
                self.queue_status_signal.emit(f"处理失败: {task['video_path']}")

            finally:
                self.queue.task_done()

        # 检查队列是否已空
        if self.queue.empty() and self.running:
            self.running = False
            self.queue_finished_signal.emit(True, "批量处理完成")


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