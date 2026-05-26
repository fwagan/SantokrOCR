"""
视频数字提取器

主要功能：
1. 视频选择和ROI框选
2. 自动定位启动时间点
3. 异步视频处理
4. 帧缓存和快速帧访问
"""

import cv2
import numpy as np
from .digit_recognizer import DigitRecognizer
import tkinter as tk
from tkinter import filedialog, messagebox
import os
from datetime import datetime
import threading
from functools import lru_cache
import time
from queue import Queue

from .led_classifier import LEDDigitClassifier


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
        返回: (ret, frame) 或 None
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



class VideoDigitExtractor:
    """视频数字提取器主类"""

    def __init__(self):
        self.digit_recognizer = None  # OpenCV数字识别器
        self.faulty_classifier = LEDDigitClassifier()
        self.rois = {}  # 存储三个ROI区域
        self.frame_cache = FrameCache(maxsize=50)
        self._pipeline = None  # 统一识别管道（懒加载）
        self.processing_stats = {
            'total_frames': 0,
            'processed_frames': 0,
            'current_status': 'idle',
            'start_time': None,
            'elapsed_time': 0
        }
        self.rotation_angle = 5  # 旋转角度，0=不旋转
        self._video_info_cache = {}  # {video_path: (fps, total_frames)}

    @staticmethod
    def build_result(frame, timestamp, temp1_full, temp1_normal, temp1_faulty_digit, temp2):
        """构建单条识别结果字典"""
        return {
            'frame': frame,
            'timestamp': round(timestamp, 3),
            'time_str': f"{int(timestamp//60):02d}:{int(timestamp%60):02d}:{int((timestamp%1)*1000):03d}",
            'temp1_full': temp1_full,
            'temp1_normal': temp1_normal if temp1_normal else "????",
            'temp1_faulty_digit': temp1_faulty_digit,
            'temp2': temp2 if temp2 else "????"
        }

    def get_video_info(self, video_path):
        """获取视频 fps 和总帧数（懒加载缓存，只打开一次）"""
        if video_path in self._video_info_cache:
            return self._video_info_cache[video_path]
        cap = cv2.VideoCapture(video_path)
        fps, total = 30.0, 0
        if cap.isOpened():
            _fps = cap.get(cv2.CAP_PROP_FPS)
            _total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            if _fps and _fps > 0:
                fps = _fps
            if _total and _total > 0:
                total = _total
        self._video_info_cache[video_path] = (fps, total)
        return fps, total

    def _get_digit_recognizer(self):
        """获取数字识别器实例（懒加载）"""
        if self.digit_recognizer is None:
            self.digit_recognizer = DigitRecognizer(rotate_angle=self.rotation_angle)
        return self.digit_recognizer

    def _get_recognition_pipeline(self):
        """获取统一识别管道实例（懒加载）"""
        if self._pipeline is None:
            from .digit_recognition_pipeline import DigitRecognitionPipeline
            self._pipeline = DigitRecognitionPipeline(is_debug=False, rotate_angle=self.rotation_angle)
        return self._pipeline

    def select_video(self):
        """选择视频文件"""
        root = tk.Tk()
        root.withdraw()
        video_path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.mov *.avi *.mkv"), ("所有文件", "*.*")]
        )
        return video_path

    def process_video_async(self, video_path, rois, interval=0.25,
                          progress_callback=None, status_callback=None, result_callback=None):
        """
        异步处理视频，支持进度回调
        progress_callback: function(processed, total)
        status_callback: function(message)
        result_callback: function(result_dict) - 每处理完一个时间点调用，传入结果字典
        返回: threading.Thread对象
        """
        # 更新处理状态
        self.processing_stats.update({
            'total_frames': 0,  # 将在线程中计算
            'processed_frames': 0,
            'current_status': 'processing',
            'start_time': time.time(),
            'elapsed_time': 0
        })

        def process_thread():
            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    if status_callback:
                        status_callback(f"无法打开视频文件: {video_path}")
                    return

                fps = cap.get(cv2.CAP_PROP_FPS)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                self._video_info_cache[video_path] = (fps, total_frames)
                frame_interval = int(fps * interval)
                # 按 interval 跳帧后的采样点总数，用于进度条
                total_time_points = (total_frames - 1) // frame_interval + 1

                # 更新总帧数
                self.processing_stats['total_frames'] = total_frames

                if status_callback:
                    status_callback(f"开始处理视频，总帧数: {total_frames}")

                # 初始化数字识别器
                recognizer = self._get_digit_recognizer()

                # 准备数据存储
                results = []

                # 跳转到第0帧
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frame_count = 0

                while True:
                    # 检查是否应该停止
                    if self.processing_stats['current_status'] == 'stopped':
                        if status_callback:
                            status_callback("处理已停止")
                        break

                    # 检查是否应该暂停
                    while self.processing_stats['current_status'] == 'paused':
                        time.sleep(0.5)
                        if self.processing_stats['current_status'] == 'stopped':
                            break

                    ret, frame = cap.read()
                    if not ret:
                        break

                    # 校对帧号：检查实际读取的帧是否和软件计数器一致
                    actual_pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                    if actual_pos != frame_count + 1:
                        print(f"[帧号偏差] 期望下一帧={frame_count + 1}, 实际={actual_pos}, 存储帧号={frame_count}")

                    timestamp = frame_count / fps

                    # 提取ROI
                    temp1_normal_img = self.crop_roi(frame, rois['temp1_normal'])
                    temp1_faulty_img = self.crop_roi(frame, rois['temp1_faulty'])

                    # temp2 ROI处理（新格式：3位数 + 最后一位 两个ROI）
                    temp2_3digits_img = None
                    temp2_lastdigit_img = None

                    if 'temp2_normal_3digits' in rois and 'temp2_normal_lastdigit' in rois:
                        temp2_3digits_img = self.crop_roi(frame, rois['temp2_normal_3digits'])
                        temp2_lastdigit_img = self.crop_roi(frame, rois['temp2_normal_lastdigit'])

                    recognizer.set_mode('normal')
                    temp1_normal_text, temp1_conf = recognizer.recognize_temperature(temp1_normal_img, digit_count=3)
                    # 识别temp2
                    if temp2_lastdigit_img is not None:
                        temp2_3digits_text, temp2_3digits_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=3)
                        # 识别最后一位数字：先分割再识别
                        _seg_result = recognizer.multi_digit_recognizer.segmenter.segment_digits(temp2_lastdigit_img)
                        if _seg_result:
                            temp2_lastdigit, temp2_lastdigit_conf, _ = recognizer.multi_digit_recognizer.recognize_single_digit(_seg_result[0]['image'])
                        else:
                            temp2_lastdigit, temp2_lastdigit_conf = -1, 0.0
                        # 组合temp2温度值：保留部分识别结果，？标记失败位
                        digit3 = (temp2_3digits_text or "???")[:3].ljust(3, "?")
                        lastdigit_str = str(temp2_lastdigit) if temp2_lastdigit >= 0 else "?"
                        temp2_text = f"{digit3}.{lastdigit_str}"
                        temp2_conf = temp2_3digits_conf if temp2_3digits_text else 0.0
                    else:
                        # 没有temp2 ROI
                        temp2_text = "????"
                        temp2_conf = 0.0

                    # 故障位数字识别
                    faulty_digit_result, method = self.recognize_faulty_digit(temp1_faulty_img)

                    # 初始化完整温度值（保留部分识别结果，？标记失败位）
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

                    # 记录结果
                    result = self.build_result(
                        frame=frame_count,
                        timestamp=timestamp,
                        temp1_full=temp1_full,
                        temp1_normal=temp1_normal_text,
                        temp1_faulty_digit=faulty_digit,
                        temp2=temp2_text,
                    )
                    results.append(result)

                    # 发射结果回调
                    if result_callback:
                        result_callback(result)

                    # 更新进度
                    self.processing_stats['processed_frames'] = len(results)
                    self.processing_stats['elapsed_time'] = time.time() - self.processing_stats['start_time']

                    if progress_callback:
                        progress_callback(len(results), total_time_points)

                    # 连续读取跳过中间帧（比 cap.set() 快 10 倍以上）
                    frame_count += frame_interval
                    if frame_count >= total_frames:
                        break
                    for _ in range(frame_interval - 1):
                        if not cap.grab():
                            break

                cap.release()

                # 更新状态
                self.processing_stats['current_status'] = 'completed'
                if status_callback:
                    status_callback(f"处理完成，共处理 {len(results)} 个时间点")
                return results

            except Exception as e:
                self.processing_stats['current_status'] = 'error'
                if status_callback:
                    status_callback(f"处理出错: {e}")
                import traceback
                traceback.print_exc()
                return []

        # 创建并启动线程
        thread = threading.Thread(target=process_thread, daemon=True)
        thread.start()
        return thread

    def get_frame_at_timestamp(self, video_path, timestamp):
        """
        获取指定时间戳的视频帧
        返回: (ret, frame) 或 None
        """
        # 先尝试从缓存获取
        cached = self.frame_cache.get_frame(video_path, timestamp)
        if cached is not None:
            return cached

        # 缓存未命中，从视频读取
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int(timestamp * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        cap.release()

        if ret:
            # 存入缓存
            self.frame_cache.cache[(video_path, timestamp)] = frame
            return frame

        return None

    def get_frame_with_rois(self, video_path, timestamp, rois):
        """
        获取帧并在图上绘制ROI框
        返回: 绘制了ROI框的图像
        """
        frame = self.get_frame_at_timestamp(video_path, timestamp)
        if frame is None:
            return None

        # 复制帧以避免修改原始数据
        frame_with_rois = frame.copy()

        # 绘制每个ROI框
        for roi_name, roi in rois.items():
            x, y, w, h = roi
            color = self.get_roi_color(roi_name)
            cv2.rectangle(frame_with_rois, (x, y), (x+w, y+h), color, 2)
            cv2.putText(frame_with_rois, roi_name, (x, y-5),
                      cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        return frame_with_rois

    def crop_and_annotate_frame(self, frame, rois, expand_ratio=0.1,
                                 downward_expand_ratio=0.0, extend_right=False):
        """
        对已加载的帧进行ROI裁剪和标注（不涉及视频文件读取）

        Args:
            frame: BGR numpy array（来自缓存或视频的完整帧）
            rois: ROI字典
            expand_ratio: 外扩比例（默认10%，四个方向均匀扩展）
            downward_expand_ratio: 额外向下扩展比例（相对于裁剪高度，默认0%）
            extend_right: 是否将裁剪右边界扩展到视频最右侧

        Returns:
            裁剪到ROI区域的图像，坐标已偏移，带ROI框和标签
        """
        if frame is None:
            return None
        if not rois:
            return frame

        # 计算所有ROI的边界
        min_x = min(roi[0] for roi in rois.values())
        min_y = min(roi[1] for roi in rois.values())
        max_x = max(roi[0] + roi[2] for roi in rois.values())
        max_y = max(roi[1] + roi[3] for roi in rois.values())

        # 外扩
        width = max_x - min_x
        height = max_y - min_y
        expand_x = int(width * expand_ratio)
        expand_y = int(height * expand_ratio)
        # 额外向下扩展
        extra_down = int(height * downward_expand_ratio)

        # 确保不超出帧边界
        h, w = frame.shape[:2]
        crop_x1 = max(0, min_x - expand_x)
        crop_y1 = max(0, min_y - expand_y)
        crop_x2 = w if extend_right else min(w, max_x + expand_x)
        crop_y2 = min(h, max_y + expand_y + extra_down)

        # 裁剪
        cropped = frame[crop_y1:crop_y2, crop_x1:crop_x2].copy()

        # 在裁剪后的图像上绘制ROI框（坐标需要偏移）
        offset_x = crop_x1
        offset_y = crop_y1
        for roi_name, roi in rois.items():
            x, y, w_roi, h_roi = roi
            color = self.get_roi_color(roi_name)
            cv2.rectangle(cropped, (x - offset_x, y - offset_y),
                         (x + w_roi - offset_x, y + h_roi - offset_y), color, 2)
            cv2.putText(cropped, roi_name, (x - offset_x, max(0, y - offset_y - 5)),
                      cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        return cropped

    def get_frame_with_rois_cropped(self, video_path, timestamp, rois, expand_ratio=0.1,
                                    downward_expand_ratio=0.0, extend_right=False):
        """
        获取帧并裁剪到只包含所有ROI区域（外扩指定比例）

        Args:
            video_path: 视频文件路径
            timestamp: 时间戳
            rois: ROI字典
            expand_ratio: 外扩比例（默认10%，四个方向均匀扩展）
            downward_expand_ratio: 额外向下扩展比例
            extend_right: 是否向右扩展（用于完整显示最后一位）

        Returns:
            裁剪到ROI区域的图像，坐标已偏移
        """
        frame = self.get_frame_at_timestamp(video_path, timestamp)
        if frame is None:
            return None
        return self.crop_and_annotate_frame(frame, rois, expand_ratio,
                                            downward_expand_ratio, extend_right)

    def get_processing_stats(self):
        """
        获取处理统计信息
        返回: dict包含总帧数、已处理数、当前状态等
        """
        stats = self.processing_stats.copy()
        if stats['start_time'] and stats['current_status'] == 'processing':
            stats['elapsed_time'] = time.time() - stats['start_time']
        return stats

    def crop_roi(self, frame, roi):
        """裁剪ROI区域"""
        x, y, w, h = roi
        return frame[y:y+h, x:x+w]




    def get_screen_resolution(self):
        """获取屏幕分辨率"""
        try:
            # 使用tkinter获取屏幕分辨率
            root = tk.Tk()
            root.withdraw()  # 不显示窗口
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.destroy()
            return screen_width, screen_height
        except:
            # 默认返回常见分辨率
            return 1920, 1080

    def get_roi_color(self, roi_name):
        """获取ROI对应的颜色"""
        colors = {
            'temp1_normal': (255, 0, 0),  # 蓝色
            'temp1_faulty': (0, 0, 255),   # 红色
            'temp2_normal_3digits': (255, 255, 0),  # 黄色
            'temp2_normal_lastdigit': (255, 0, 255)  # 紫色
        }
        return colors.get(roi_name, (255, 255, 255))  # 默认白色

    def recognize_faulty_digit(self, faulty_roi_image):
        """
        识别故障位数字（使用统一识别管道）

        策略：
        1. 使用统一识别管道识别单个数字（经过完整预处理）
        2. 支持正常位和故障位模式识别

        Args:
            faulty_roi_image: 故障位区域图像

        Returns:
            (digit, method) - 数字、识别方法标识
            digit: 识别的数字（-1表示无法识别，-2表示0/8歧义）
            method: 'digit_recognizer'、'digit_recognizer_0or8' 或 'unknown'
        """
        try:
            # 策略：先直接识别，如果失败（-1）再尝试分割后识别
            # 直接分割故障位会导致g段缺失的4被误分成两个1，所以不能先分割
            pipeline = self._get_recognition_pipeline()
            pipeline.set_mode('broken')
            digit, confidence, _, _ = pipeline.recognize_digit_image(
                faulty_roi_image, mode='broken'
            )

            # 直接识别失败时，尝试分割后重试（可能获得更准确的裁剪区域）
            if digit == -1:
                _segments = pipeline._segmenter.segment_digits(faulty_roi_image)
                if _segments:
                    digit, confidence, _, _ = pipeline.recognize_digit_image(
                        _segments[0]['image'], mode='broken'
                    )

            # 检查识别结果
            if digit == -2:
                return -2, 'digit_recognizer_0or8'
            elif digit != -1:
                # 成功识别数字
                return digit, 'digit_recognizer'
            else:
                # 无法识别
                return -1, 'unknown'

        except Exception as e:
            print(f"数字识别器识别故障位数字失败: {e}")
            return -1, 'unknown'

    # 控制方法
    def pause_processing(self):
        """暂停处理"""
        if self.processing_stats['current_status'] == 'processing':
            self.processing_stats['current_status'] = 'paused'

    def resume_processing(self):
        """继续处理"""
        if self.processing_stats['current_status'] == 'paused':
            self.processing_stats['current_status'] = 'processing'

    def stop_processing(self):
        """停止处理"""
        if self.processing_stats['current_status'] in ['processing', 'paused']:
            self.processing_stats['current_status'] = 'stopped'