"""
统一数字识别管道

整合数字分割、预处理和分类，提供统一的识别API。
确保帧查看器调试标签页与实际视频处理管道使用完全相同的识别代码。

is_debug=False 时仅运行识别，零额外开销。
is_debug=True 时记录中间步骤图像和信息，供帧查看器调试标签页使用。
"""

import numpy as np
from typing import List, Tuple, Dict, Optional, Any

from .projection_segmenter import ProjectionSegmenter
from .led_classifier import LEDDigitClassifier


class DigitRecognitionPipeline:
    """统一的数字识别管道"""

    def __init__(self, is_debug: bool = False, rotate_angle: float = 5):
        """
        初始化识别管道

        Args:
            is_debug: 是否记录调试数据（中间图像、7段向量等）
            rotate_angle: 旋转角度（正数=逆时针，0=不旋转），传递给ProjectionSegmenter
        """
        self.is_debug = is_debug
        self._segmenter = ProjectionSegmenter(rotate_angle=rotate_angle)
        self._classifier = LEDDigitClassifier()
        self._debug_data = {}  # {roi_name: {step: data}}

    def set_mode(self, mode: str):
        """设置识别模式 'normal' 或 'broken'"""
        self._classifier.set_mode(mode)

    @property
    def mode(self) -> str:
        return self._classifier.mode

    def recognize_digit_image(self, digit_image: np.ndarray, mode: str = 'normal'
                              ) -> Tuple[int, float, bool, Optional[Dict]]:
        """
        识别单个数字图像。

        这是核心统一方法，封装了完整流程：
        数字1检测 → 预处理(旋转/紧裁剪/归一化到30x50) → 特征提取 → 分类

        确保所有调用路径（视频处理、调试标签页）使用完全相同的识别代码。

        Args:
            digit_image: 单个数字图像
            mode: 'normal' 或 'broken'

        Returns:
            (digit, confidence, is_suspicious, debug_info)
            digit: -1=无法识别, -2=可疑(0/8), 0-9=识别结果
            confidence: 置信度 0-1
            is_suspicious: 亮段与映射不一致时为True
            debug_info: 仅 is_debug=True 时有内容，否则为None
                       包含 'segments'(7段向量), 'preprocessed'(30x50图像),
                       'is_digit_1', 'aspect_ratio'
        """
        h, w = digit_image.shape[:2]
        aspect_ratio = w / h if h > 0 else 0
        is_digit_1 = (aspect_ratio < 0.5)

        # 预处理：旋转纠正 + 紧裁剪 + 归一化到30x50
        preprocessed = self._segmenter.preprocess_single_digit(
            digit_image, is_digit_1=is_digit_1
        )

        # 分类识别
        digit, confidence, is_suspicious = self._classifier.recognize(
            preprocessed, mode=mode
        )

        # Debug模式：提取7段向量和中间数据
        debug_info = None
        if self.is_debug:
            # 延迟导入，避免循环依赖
            from .white_led_recognizer import WhiteLEDRecognizer
            wr = WhiteLEDRecognizer(mode=mode)
            segments_vector, _ = wr.extract_segments(preprocessed)
            debug_info = {
                'segments': segments_vector,
                'preprocessed': preprocessed.copy(),
                'is_digit_1': is_digit_1,
                'aspect_ratio': aspect_ratio,
            }

        return digit, confidence, is_suspicious, debug_info

    def recognize_roi(self, roi_image: np.ndarray, mode: str = 'normal',
                      expected_count: Optional[int] = None,
                      roi_name: str = '') -> List[Dict]:
        """
        识别ROI区域中的所有数字（分割 → 逐个识别）。

        用于调试标签页的完整流程展示。

        Args:
            roi_image: ROI裁剪图像
            mode: 'normal' 或 'broken'
            expected_count: 预期数字个数（用于限制分割数量）
            roi_name: ROI名称（用于debug数据索引）

        Returns:
            List[Dict], 每项包含:
                'digit': int, 'confidence': float, 'is_suspicious': bool
                (is_debug=True时还有):
                'segments': List[int], 'preprocessed': np.ndarray
                'is_digit_1': bool, 'aspect_ratio': float
                'seg_image': np.ndarray (原始分割图像)
        """
        # 分割数字
        segments = self._segmenter.segment_digits(roi_image)

        if expected_count:
            segments = segments[:expected_count]

        roi_debug_results = []
        for seg in segments:
            seg_image = seg['image']
            digit, confidence, is_suspicious, debug_info = self.recognize_digit_image(
                seg_image, mode=mode
            )

            entry = {
                'digit': digit,
                'confidence': confidence,
                'is_suspicious': is_suspicious,
            }

            if self.is_debug:
                # 保存分割出的原始图像用于调试显示
                entry['seg_image'] = seg_image.copy()
                if debug_info:
                    entry.update(debug_info)

            roi_debug_results.append(entry)

        # 保存ROI级别的debug数据
        if self.is_debug:
            self._debug_data[roi_name] = {
                'roi_image': roi_image.copy(),
                'segments_data': segments,
                'results': roi_debug_results,
            }

        return roi_debug_results

    def get_roi_debug_data(self, roi_name: str) -> Optional[Dict]:
        """获取指定ROI的调试数据"""
        return self._debug_data.get(roi_name)

    def get_all_debug_data(self) -> Dict:
        """获取所有调试数据"""
        return dict(self._debug_data)

    def clear_debug(self):
        """清除调试数据"""
        self._debug_data.clear()
