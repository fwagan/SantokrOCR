"""
摄像头实时处理线程

支持真实摄像头数据源：
- source=int  → cv2.VideoCapture(索引) 真实摄像头
"""

import threading
import time
import cv2


class Signal:
    """简单的信号类，用于线程间通信（与 async_worker.Signal 相同接口）"""

    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in self._callbacks:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"信号回调出错: {e}")


class CameraProcessingThread(threading.Thread):
    """摄像头实时处理线程"""

    def __init__(self, extractor, source, rois, interval=0.25):
        """
        Args:
            extractor: VideoDigitExtractor 实例
            source: 摄像头索引 (int)
            rois: ROI字典
            interval: 采样间隔（秒）
        """
        super().__init__(daemon=True)
        self.extractor = extractor
        self.source = source
        self.rois = rois
        self.interval = interval

        # 信号
        self.result_signal = Signal()   # (result_dict)
        self.frame_signal = Signal()    # (numpy_frame) 用于预览
        self.status_signal = Signal()   # (message)
        self.finished_signal = Signal() # (success_bool, message)

        # 控制
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始非暂停

        self.results = []

        # 失败帧缓存（用于调试，最多10帧）
        self._failed_frames = []  # [(frame_num, frame_bgr, result_dict), ...]
        self._failed_frames_lock = threading.Lock()

    def run(self):
        cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.finished_signal.emit(False, f"无法打开摄像头 {self.source}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        recognizer = self.extractor._get_digit_recognizer()
        frame_count = 0
        start_time = time.time()
        last_preview_emit = 0

        # 检查ROI格式
        has_temp2_new = 'temp2_normal_3digits' in self.rois and 'temp2_normal_lastdigit' in self.rois
        has_temp2_old = 'temp2_normal' in self.rois

        self.status_signal.emit("实时识别已启动")

        while not self._stop_event.is_set():
            # 处理暂停
            while not self._pause_event.is_set() and not self._stop_event.is_set():
                time.sleep(0.1)

            if self._stop_event.is_set():
                break

            ret, frame = cap.read()
            if not ret:
                self.status_signal.emit("摄像头读取失败，正在重试...")
                time.sleep(0.5)
                continue

            loop_start = time.time()

            # 时间戳
            timestamp = time.time() - start_time

            # 发送预览帧（降频：每秒约5帧）
            if frame_count - last_preview_emit >= max(1, int(1.0 / self.interval / 5)):
                self.frame_signal.emit(frame.copy())
                last_preview_emit = frame_count

            # ──── ROI裁剪与识别（与 process_video_async 完全相同） ────

            temp1_normal_img = self.extractor.crop_roi(frame, self.rois['temp1_normal'])
            temp1_faulty_img = self.extractor.crop_roi(frame, self.rois['temp1_faulty'])

            # temp2 ROI处理（支持新旧格式）
            temp2_3digits_img = None
            temp2_lastdigit_img = None
            temp2_normal_img = None

            if has_temp2_new:
                temp2_3digits_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_3digits'])
                temp2_lastdigit_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_lastdigit'])
            elif has_temp2_old:
                temp2_normal_img = self.extractor.crop_roi(frame, self.rois['temp2_normal'])
                temp2_3digits_img = temp2_normal_img

            recognizer.set_mode('normal')
            temp1_normal_text, temp1_conf = recognizer.recognize_temperature(temp1_normal_img, digit_count=3)

            # temp2识别
            if temp2_lastdigit_img is not None:
                temp2_3digits_text, temp2_3digits_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=3)
                _seg_result = recognizer.multi_digit_recognizer.segmenter.segment_digits(temp2_lastdigit_img)
                if _seg_result:
                    temp2_lastdigit, temp2_lastdigit_conf, _ = recognizer.multi_digit_recognizer.recognize_single_digit(_seg_result[0]['image'])
                else:
                    temp2_lastdigit, temp2_lastdigit_conf = -1, 0.0
                if temp2_3digits_text and len(temp2_3digits_text) >= 3 and temp2_lastdigit >= 0:
                    temp2_text = f"{temp2_3digits_text[:3]}.{temp2_lastdigit}"
                    temp2_conf = (temp2_3digits_conf + temp2_lastdigit_conf) / 2
                else:
                    temp2_text = "????"
                    temp2_conf = 0.0
            elif temp2_normal_img is not None:
                temp2_text, temp2_conf = recognizer.recognize_temperature(temp2_normal_img, digit_count=4)
            else:
                temp2_text = "????"
                temp2_conf = 0.0

            # 故障位识别
            faulty_digit_result, method = self.extractor.recognize_faulty_digit(temp1_faulty_img)

            # 组合完整温度值
            temp1_full = "????"
            faulty_digit = -1
            if temp1_normal_text and len(temp1_normal_text) >= 3:
                if faulty_digit_result == -2:
                    faulty_digit = -2
                    temp1_full = "????"
                elif faulty_digit_result != -1:
                    faulty_digit = faulty_digit_result
                    temp1_full = temp1_normal_text + "." + str(faulty_digit)
                else:
                    faulty_digit = -1
                    temp1_full = "????"
            else:
                faulty_digit = -1
                temp1_full = "????"

            # 构建结果
            result = {
                'frame': frame_count,
                'timestamp': round(timestamp, 3),
                'original_timestamp': round(timestamp, 3),
                'time_str': f"{int(timestamp // 60):02d}:{int(timestamp % 60):02d}:{int((timestamp % 1) * 1000):03d}",
                'temp1_full': temp1_full,
                'temp1_normal': temp1_normal_text if temp1_normal_text else "????",
                'temp1_faulty_digit': faulty_digit,
                'temp2': temp2_text if temp2_text else "????"
            }
            self.results.append(result)
            self.result_signal.emit(result)

            # 缓存失败帧用于调试（至多10帧，满了不再追加）
            if (temp1_full == '????' or temp2_text == '????') and len(self._failed_frames) < 10:
                with self._failed_frames_lock:
                    if len(self._failed_frames) < 10:
                        self._failed_frames.append((frame_count, frame.copy(), dict(result)))

            frame_count += 1

            # 等待到下一个采样间隔
            elapsed = time.time() - loop_start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        cap.release()

        if self._stop_event.is_set():
            self.finished_signal.emit(False, "处理已停止")
        else:
            self.finished_signal.emit(True, "处理完成")

    def stop(self):
        self._stop_event.set()

    def pause(self):
        self._pause_event.clear()
        self.status_signal.emit("处理已暂停")

    def resume(self):
        self._pause_event.set()
        self.status_signal.emit("继续处理")

    def is_paused(self):
        return not self._pause_event.is_set()

    def is_stopped(self):
        return self._stop_event.is_set()

    def get_failed_frames(self):
        """返回失败帧缓存列表 [(frame_num, frame_bgr, result_dict), ...]"""
        with self._failed_frames_lock:
            return list(self._failed_frames)
