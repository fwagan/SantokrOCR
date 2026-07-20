"""
摄像头实时处理线程

从预览线程取帧，不直接操作摄像头。
"""

import threading
import time
from typing import Optional

from utils.signal import Signal
from .temperature_source import TemperatureDataSource

_NONE_FRAME_RETRY_INTERVAL = 0.05  # 取到空帧时的重试间隔（秒）
_TEMP_DIFF_THRESHOLD_DEFAULT = 3.0  # 温差异常检测默认阈值（℃/帧）
_MAX_EFFECTIVE_GAP = 4             # 温差异常检测最大有效gap，防止长时间遮挡后阈值过大（4帧×3℃=12℃）
_PAUSE_CHECK_INTERVAL = 0.1        # 暂停循环唤醒检查间隔（秒）


class CameraProcessingThread(threading.Thread, TemperatureDataSource):
    """摄像头实时处理线程"""

    def __init__(self, extractor, get_frame, rois, interval=0.25, cache=None,
                 temp_diff_threshold: float = _TEMP_DIFF_THRESHOLD_DEFAULT):
        """
        Args:
            extractor: VideoDigitExtractor 实例
            get_frame: 可调用，返回当前帧 BGR ndarray（由预览线程提供）
            rois: ROI字典
            interval: 采样间隔（秒）
            cache: RealTimeProcessCache 实例（可选）
            temp_diff_threshold: 温差异常检测帧变化率阈值（℃/帧），
                                 默认 _TEMP_DIFF_THRESHOLD_DEFAULT
        """
        super().__init__(daemon=True)
        self.extractor = extractor
        self._get_frame = get_frame
        self.rois = rois
        self.interval = interval
        self.cache = cache
        self.temp_diff_threshold = temp_diff_threshold

        # 信号
        self.result_signal = Signal()   # (result_dict)
        self.status_signal = Signal()   # (message)
        self.finished_signal = Signal() # (success_bool, message)

        # 控制
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始非暂停

        self.results = []

        # 温差异常检测：上一次有效温度值 + 连续无效帧计数
        self._last_valid_temp1: Optional[float] = None
        self._consecutive_invalid_frames: int = 0

    def reset_temperature_tracking(self) -> None:
        """重置温差异常检测状态（清空数据后调用，避免与清空前温度比较）"""
        self._last_valid_temp1 = None
        self._consecutive_invalid_frames = 0

    def run(self):
        recognizer = self.extractor._get_digit_recognizer()
        frame_count = 0
        start_time = time.time()

        # 检查ROI格式
        has_temp2_new = 'temp2_normal_3digits' in self.rois and 'temp2_normal_lastdigit' in self.rois
        has_temp2_old = 'temp2_normal' in self.rois

        self.status_signal.emit("实时识别已启动")

        while not self._stop_event.is_set():
            # 处理暂停
            while not self._pause_event.is_set() and not self._stop_event.is_set():
                time.sleep(_PAUSE_CHECK_INTERVAL)

            if self._stop_event.is_set():
                break

            frame = self._get_frame()
            if frame is None:
                time.sleep(_NONE_FRAME_RETRY_INTERVAL)
                continue

            loop_start = time.time()

            # 时间戳
            timestamp = time.time() - start_time

            # ──── ROI裁剪与识别 ────

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
                if temp2_3digits_text and len(temp2_3digits_text) >= 3:
                    if temp2_lastdigit >= 0:
                        temp2_text = f"{temp2_3digits_text[:3]}.{temp2_lastdigit}"
                        temp2_conf = (temp2_3digits_conf + temp2_lastdigit_conf) / 2
                    else:
                        # 保留正常位，仅末位标记 ?
                        temp2_text = f"{temp2_3digits_text[:3]}.?"
                        temp2_conf = temp2_3digits_conf
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

            # 组合完整温度值（保留部分识别结果，? 标记失败位）
            faulty_digit = -1
            temp1_full = f"{(temp1_normal_text or '???')[:3].ljust(3, '?')}.?"

            if temp1_normal_text and len(temp1_normal_text) >= 3:
                if faulty_digit_result == -2:
                    faulty_digit = -2
                    temp1_full = f"{temp1_normal_text[:3]}.?"
                elif faulty_digit_result != -1:
                    faulty_digit = faulty_digit_result
                    temp1_full = f"{temp1_normal_text[:3]}.{faulty_digit}"
                else:
                    faulty_digit = -1
                    temp1_full = f"{temp1_normal_text[:3]}.?"

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
            # 温差异常检测（过滤数码管过渡态产生的瞬发尖峰）
            # 使用帧率归一化：连续无效帧越多，允许的温差越大，避免冷却时误杀
            try:
                curr_temp = float(temp1_full)
                if self._last_valid_temp1 is not None:
                    gap = min(self._consecutive_invalid_frames + 1, _MAX_EFFECTIVE_GAP)
                    if abs(curr_temp - self._last_valid_temp1) > gap * self.temp_diff_threshold:
                        result['abnormal_category'] = 'temperature_diff'
                    else:
                        self._last_valid_temp1 = curr_temp
                        self._consecutive_invalid_frames = 0
                else:
                    self._last_valid_temp1 = curr_temp
            except (ValueError, TypeError):
                # 含 ? 的温度值，累加连续无效帧计数，不参与温差检测
                self._consecutive_invalid_frames += 1

            self.results.append(result)
            self.result_signal.emit(result)

            # 异步缓存帧（仅在配置了 cache 时）
            if self.cache is not None:
                self.cache.save_frame(frame.copy(), frame_count)

            frame_count += 1

            # 等待到下一个采样间隔
            elapsed = time.time() - loop_start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self._stop_event.is_set():
            self.finished_signal.emit(False, "处理已停止")
        else:
            self.finished_signal.emit(True, "处理完成")

    def stop(self):
        self._stop_event.set()
        if self.cache is not None:
            self.cache.stop_writer()

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

