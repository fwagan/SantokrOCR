"""
多数字识别器

整合数字分割和单个数字识别，支持多位数字识别。
专门针对7段数码管显示设计，支持温度值识别。
"""

from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from .led_classifier import LEDDigitClassifier

# 使用投影分割器（基于test_doubao_multiple_1.py算法，100%准确率）
from .projection_segmenter import ProjectionSegmenter


class MultiDigitRecognizer:
    """多数字识别器"""

    def __init__(self, segmenter=None,
                 classifier: Optional[LEDDigitClassifier] = None,
                 rotate_angle: float = 5):
        """
        初始化识别器

        Args:
            segmenter: 数字分割器实例，如果为None则创建默认ProjectionSegmenter实例
            classifier: 数字分类器实例，如果为None则创建默认实例
            rotate_angle: 旋转角度（正数=逆时针，0=不旋转），传递给ProjectionSegmenter
        """
        if segmenter is None:
            self.segmenter = ProjectionSegmenter(rotate_angle=rotate_angle)
        else:
            # 用户提供了分割器
            self.segmenter = segmenter
        # 删除use_projection_segmenter属性，不再需要

        self.classifier = classifier or LEDDigitClassifier()

        # 识别模式：'normal' 或 'broken'
        self.mode = 'normal'

        # 缓存上一次的分割结果，用于优化连续识别
        self.last_segments_cache = None
        self.last_image_hash = None

    def set_mode(self, mode: str):
        """设置识别模式 ('normal' 或 'broken')"""
        if mode not in ['normal', 'broken']:
            raise ValueError(f"模式必须是 'normal' 或 'broken', 得到: {mode}")
        self.mode = mode
        self.classifier.set_mode(mode)

    def recognize_single_digit(self, digit_image: np.ndarray) -> Tuple[int, float, bool]:
        """
        识别单个数字

        Args:
            digit_image: 单个数字图像

        Returns:
            (digit, confidence, is_suspicious): 识别的数字、置信度和是否存疑
            digit = -1 表示无法识别
            digit = -2 表示可疑值（可能是0或8）
            is_suspicious = True 表示识别到的亮段与映射不一致
        """
        # 确保图像有效
        if digit_image is None or digit_image.size == 0:
            return -1, 0.0, False

        # 调整图像尺寸（如果太小）
        h, w = digit_image.shape[:2]
        if h < 10 or w < 5:
            # 太小无法识别
            return -1, 0.0, False

        # 预处理数字图像（旋转、数字1特殊处理等）
        # 使用ProjectionSegmenter的preprocess_single_digit方法
        try:
            # 判断是否为数字1 - 使用与帧查看器调试标签页完全相同的逻辑
            if len(digit_image.shape) == 3:
                gray = cv2.cvtColor(digit_image, cv2.COLOR_BGR2GRAY)
            else:
                gray = digit_image

            _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            col_proj = np.sum(th == 255, axis=0)
            valid_cols = np.where(col_proj > 0)[0]
            valid_width = len(valid_cols) if len(valid_cols) > 0 else 0
            aspect_ratio = w / h if h > 0 else 0

            # 判断是否是1 - 与帧查看器调试标签页完全相同的逻辑
            # 帧查看器调试标签页使用: is_digit_1 = (aspect_ratio < 0.5)
            is_digit_1 = (aspect_ratio < 0.5)

            # 使用ProjectionSegmenter进行预处理
            preprocessed = self.segmenter.preprocess_single_digit(
                digit_image,
                is_digit_1=is_digit_1,
                debug_prefix=None  # 不保存调试图像
            )

            # 使用分类器识别预处理后的图像
            return self.classifier.recognize(preprocessed, mode=self.mode)
        except Exception as e:
            # 如果预处理失败，回退到原始图像识别
            print(f"数字预处理失败，使用原始图像: {e}")
            return self.classifier.recognize(digit_image, mode=self.mode)

    def _segment_digits(self, image: np.ndarray):
        """数字分割方法"""
        # 直接使用ProjectionSegmenter的segment_digits方法
        return self.segmenter.segment_digits(image)

    def _validate_segmentation(self, segments, expected_count=None):
        """分割验证方法"""
        if not segments:
            return False

        # 简单验证：检查数量是否匹配预期
        if expected_count and len(segments) != expected_count:
            return False
        return True

    def _merge_broken_digits(self, segments):
        """合并断裂数字方法"""
        # 简单实现：直接返回原样
        # ProjectionSegmenter的分割算法通常不会产生断裂数字
        return segments

    def recognize_digits(self, image: np.ndarray, expected_count: Optional[int] = None) -> Tuple[str, List[int], float]:
        """
        识别多个数字

        Args:
            image: 包含多个数字的图像
            expected_count: 预期数字个数（可选）

        Returns:
            (digit_string, digits, overall_confidence):
                digit_string: 数字字符串（如"123"）
                digits: 单个数字列表（整数）
                overall_confidence: 整体置信度
        """
        # 分割数字（使用通用方法）
        segments = self._segment_digits(image)

        # 如果仍然没有区域，返回空结果
        if not segments:
            return "", [], 0.0

        # 验证分割结果
        if expected_count and not self._validate_segmentation(segments, expected_count):
            # 分割可能有问题，尝试合并断裂数字
            segments = self._merge_broken_digits(segments)

        # 如果预期数量明确但实际数量不匹配，尝试调整
        if expected_count and len(segments) != expected_count:
            # 可能是数字粘连或断裂，尝试进一步处理
            if len(segments) < expected_count:
                # 数量不足，可能是粘连，对于ProjectionSegmenter没有fallback
                pass
            elif len(segments) > expected_count:
                # 数量过多，可能是断裂，尝试合并
                segments = self._merge_broken_digits(segments)

        # 限制最大数字个数（避免误检测）
        max_digits = expected_count or 10
        segments = segments[:max_digits]

        # 识别每个数字
        digit_results = []
        confidences = []

        for i, segment in enumerate(segments):
            digit_img = segment['image']
            digit, confidence, is_suspicious = self.recognize_single_digit(digit_img)

            digit_results.append(digit)
            confidences.append(confidence)


        # 构建结果
        digit_string = ""
        valid_digits = []

        for digit in digit_results:
            if digit >= 0:
                digit_string += str(digit)
                valid_digits.append(digit)
            elif digit == -2:
                # 可疑值（0或8），用'?'表示
                digit_string += "?"
                valid_digits.append(-2)
            else:
                # 无法识别，用'?'表示
                digit_string += "?"
                valid_digits.append(-1)

        # 计算整体置信度（有效数字的平均置信度）
        valid_confidences = [c for c, d in zip(confidences, digit_results) if d >= -2]
        overall_confidence = np.mean(valid_confidences) if valid_confidences else 0.0

        return digit_string, valid_digits, overall_confidence

    def recognize_temperature(self, image: np.ndarray, digit_count: int = 3) -> Tuple[str, float]:
        """
        识别温度值

        Args:
            image: 温度区域图像
            digit_count: 数字位数 (3或4)

        Returns:
            (temperature_string, confidence): 温度字符串和置信度
                - 对于digit_count=3：返回整数温度字符串（如"161"）
                - 对于digit_count=4：返回浮点数温度字符串（如"200.7"）
        """
        # 识别数字
        digit_string, digits, confidence = self.recognize_digits(image, expected_count=digit_count)

        # 根据digit_count确定小数点位置并计算温度值
        if digit_count == 4:
            # temp2格式：xxx.x（3位整数+1位小数）
            decimal_pos = 3  # 小数点在第3位之后
            calculated_temp = _calculate_temperature_from_digits(digit_string, decimal_pos)

            if calculated_temp is not None:
                # 格式化温度字符串：保留一位小数
                temperature_str = f"{calculated_temp:.1f}"
            else:
                # 无法计算，返回原始数字字符串
                temperature_str = digit_string.ljust(4, '?')
        else:
            # temp1格式：xxx（3位整数）
            calculated_temp = _calculate_temperature_from_digits(digit_string, decimal_pos=None)
            if calculated_temp is not None:
                # 整数温度
                temperature_str = str(int(calculated_temp))
            else:
                # 无法计算，返回原始数字字符串
                temperature_str = digit_string.ljust(3, '?')

        return temperature_str, confidence

    def recognize_with_positional_constraints(self, image: np.ndarray,
                                              expected_positions: List[Tuple[int, int, int, int]]) -> List[Tuple[int, float]]:
        """
        基于位置约束识别数字

        Args:
            image: 输入图像
            expected_positions: 预期数字位置列表 [(x, y, w, h), ...]

        Returns:
            每个位置的识别结果列表 [(digit, confidence), ...]
        """
        results = []

        for i, (x, y, w, h) in enumerate(expected_positions):
            # 确保位置在图像范围内
            img_h, img_w = image.shape[:2]
            x = max(0, min(x, img_w - 1))
            y = max(0, min(y, img_h - 1))
            w = min(w, img_w - x)
            h = min(h, img_h - y)

            if w <= 0 or h <= 0:
                results.append((-1, 0.0))
                continue

            # 裁剪区域
            roi = image[y:y+h, x:x+w]

            if roi.size == 0:
                results.append((-1, 0.0))
                continue

            # 识别数字
            digit, confidence, is_suspicious = self.recognize_single_digit(roi)
            results.append((digit, confidence))

        return results

    def batch_recognize(self, images: List[np.ndarray], mode: str = 'normal') -> List[Tuple[str, float]]:
        """
        批量识别多个图像

        Args:
            images: 图像列表
            mode: 识别模式

        Returns:
            每个图像的识别结果列表 [(digit_string, confidence), ...]
        """
        results = []
        original_mode = self.mode

        try:
            self.set_mode(mode)

            for img in images:
                if img is None or img.size == 0:
                    results.append(("", 0.0))
                    continue

                # 自动判断是单个数字还是多个数字
                h, w = img.shape[:2]
                aspect_ratio = w / h if h > 0 else 0

                if aspect_ratio > 2.0:
                    # 宽高比大，可能是多个数字
                    digit_string, _, confidence = self.recognize_digits(img)
                    results.append((digit_string, confidence))
                else:
                    # 可能是单个数字
                    digit, confidence, is_suspicious = self.recognize_single_digit(img)
                    digit_string = str(digit) if digit >= 0 else ("?" if digit == -2 else "")
                    results.append((digit_string, confidence))
        finally:
            self.set_mode(original_mode)

        return results


def _calculate_temperature_from_digits(digit_string: str, decimal_pos: Optional[int] = None) -> Optional[float]:
    """
    根据识别到的数字字符串和小数点位置计算温度值

    Args:
        digit_string: 识别到的数字字符串（如"2140"）
        decimal_pos: 小数点位置（从0开始，None表示无小数点）

    Returns:
        float: 计算出的温度值，如果无法计算返回None
    """
    if not digit_string:
        return None

    # 过滤掉非数字字符
    digits_only = ''.join(filter(str.isdigit, digit_string))

    if not digits_only:
        return None

    try:
        if decimal_pos is not None and decimal_pos > 0:
            # 有小数的温度值
            if len(digits_only) >= decimal_pos:
                integer_part = digits_only[:decimal_pos]
                decimal_part = digits_only[decimal_pos:] if len(digits_only) > decimal_pos else "0"
                # 确保小数部分有1位
                if len(decimal_part) == 0:
                    decimal_part = "0"
                elif len(decimal_part) > 1:
                    decimal_part = decimal_part[0]  # 只取第一位小数

                temp_str = f"{integer_part}.{decimal_part}"
                return float(temp_str)
            else:
                # 数字不足，无法确定小数点位置
                return float(digits_only)  # 当作整数处理
        else:
            # 无小数的温度值
            return float(digits_only)
    except ValueError:
        return None


def test_recognizer():
    """测试多数字识别器"""
    import os
    import sys

    # 创建测试图像
    test_img = np.ones((100, 200, 3), dtype=np.uint8) * 50

    # 模拟数字"123"
    cv2.rectangle(test_img, (20, 20), (40, 80), (255, 255, 255), -1)  # 数字1
    cv2.rectangle(test_img, (60, 20), (80, 80), (255, 255, 255), -1)  # 数字2
    cv2.rectangle(test_img, (100, 20), (120, 80), (255, 255, 255), -1)  # 数字3

    recognizer = MultiDigitRecognizer()

    # 测试多数字识别
    digit_string, digits, confidence = recognizer.recognize_digits(test_img, expected_count=3)
    print(f"多数字识别: 字符串='{digit_string}', 数字={digits}, 置信度={confidence:.2f}")

    # 测试温度识别
    temp_img = np.ones((80, 120, 3), dtype=np.uint8) * 50
    for i in range(3):
        x = 20 + i * 30
        cv2.rectangle(temp_img, (x, 20), (x+20, 60), (255, 255, 255), -1)

    temp_str, temp_conf = recognizer.recognize_temperature(temp_img, digit_count=3)
    print(f"温度识别: '{temp_str}', 置信度={temp_conf:.2f}")

    return digit_string, temp_str


if __name__ == "__main__":
    test_recognizer()
    print("多数字识别器测试完成")