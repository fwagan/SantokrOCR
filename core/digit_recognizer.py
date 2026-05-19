"""
数字识别器主类

基于OpenCV的数字识别器，专门针对7段数码管显示设计，支持正常位和故障位识别。
"""

import cv2
import numpy as np
from typing import List, Tuple, Dict, Optional, Union, Any

from .feature_extractor import FeatureExtractor
from .led_classifier import LEDDigitClassifier
from .multi_digit_recognizer import MultiDigitRecognizer


class DigitRecognizer:
    """替代OCREngine的OpenCV数字识别器"""

    def __init__(self, use_gpu=False, lang='ch', show_log=False,
                 rotate_angle: float = 5):
        """
        初始化数字识别器

        注意：use_gpu, lang, show_log参数为兼容性保留，实际不使用。

        Args:
            use_gpu: 是否使用GPU (已弃用，保留参数用于兼容性)
            lang: 语言 (已弃用，保留参数用于兼容性)
            show_log: 是否显示日志 (已弃用，保留参数用于兼容性)
            rotate_angle: 旋转角度（正数=逆时针，0=不旋转）
        """
        # 初始化各个组件
        self.feature_extractor = FeatureExtractor()
        self.led_classifier = LEDDigitClassifier()
        self.multi_digit_recognizer = MultiDigitRecognizer(
            segmenter=None,  # 传入None，让MultiDigitRecognizer创建默认ProjectionSegmenter
            classifier=self.led_classifier,
            rotate_angle=rotate_angle
        )
        # 删除digit_segmenter属性，不再需要

        # 兼容性属性
        self.use_gpu = use_gpu
        self.lang = lang
        self.show_log = show_log

        # 识别模式
        self.mode = 'normal'  # 'normal' 或 'broken'

        # 缓存最近识别结果
        self._cache = {}
        self._cache_size = 100

    def set_mode(self, mode: str):
        """
        设置识别模式

        Args:
            mode: 'normal' 或 'broken'
        """
        if mode not in ['normal', 'broken']:
            raise ValueError(f"模式必须是 'normal' 或 'broken', 得到: {mode}")
        self.mode = mode
        self.led_classifier.set_mode(mode)
        self.multi_digit_recognizer.set_mode(mode)

    def recognize(self, image: np.ndarray, mode: Optional[str] = None) -> List[Dict]:
        """
        识别图像中的数字（兼容OCREngine API）

        Args:
            image: 输入图像 (numpy数组)
            mode: 识别模式，为None时使用当前模式

        Returns:
            list: OCR结果，格式为[(bbox, text, confidence), ...]
                  为保持兼容性，返回与OCREngine相同的格式
        """
        current_mode = mode or self.mode

        # 检查缓存
        cache_key = self._get_cache_key(image, current_mode)
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # 判断图像内容：单个数字还是多个数字
            h, w = image.shape[:2]
            aspect_ratio = w / h if h > 0 else 0

            if aspect_ratio > 2.0:
                # 宽高比大，可能是多个数字
                digit_string, digits, confidence = self.multi_digit_recognizer.recognize_digits(
                    image, expected_count=None
                )

                # 转换为兼容格式
                result = self._format_result_as_ocr(digit_string, confidence, image)
            else:
                # 可能是单个数字
                digit, confidence, is_suspicious = self.led_classifier.recognize(image, mode=current_mode)

                # 转换为兼容格式
                if digit >= 0:
                    text = str(digit)
                elif digit == -2:
                    text = "0"  # 可疑值，默认视为0
                else:
                    text = ""

                result = self._format_result_as_ocr(text, confidence, image)

            # 更新缓存
            self._update_cache(cache_key, result)

            return result

        except Exception as e:
            print(f"数字识别失败: {e}")
            return []

    def _format_result_as_ocr(self, text: str, confidence: float,
                              image: np.ndarray) -> List[Dict]:
        """
        将识别结果格式化为OCR兼容格式

        Args:
            text: 识别文本
            confidence: 置信度
            image: 原始图像

        Returns:
            OCR格式结果列表
        """
        if not text:
            return []

        # 模拟OCR返回格式
        h, w = image.shape[:2]

        # 创建一个假想的边界框（整个图像区域）
        bbox = [
            [0, 0],          # 左上
            [w, 0],          # 右上
            [w, h],          # 右下
            [0, h]           # 左下
        ]

        return [{
            'bbox': bbox,
            'text': text,
            'confidence': confidence
        }]

    def _get_cache_key(self, image: np.ndarray, mode: str) -> str:
        """生成缓存键"""
        # 使用图像哈希和模式作为键
        import hashlib
        if image.size > 0:
            # 计算图像哈希
            img_bytes = image.tobytes()
            img_hash = hashlib.md5(img_bytes).hexdigest()[:16]
        else:
            img_hash = "empty"

        return f"{img_hash}_{mode}"

    def _update_cache(self, key: str, value: Any):
        """更新缓存"""
        self._cache[key] = value

        # 限制缓存大小
        if len(self._cache) > self._cache_size:
            # 移除最旧的条目
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def recognize_batch(self, images: List[np.ndarray]) -> List[List[Dict]]:
        """
        批量识别图像（兼容OCREngine API）

        Args:
            images: 图像列表

        Returns:
            每个图像的OCR结果列表
        """
        results = []
        for img in images:
            results.append(self.recognize(img))
        return results

    def recognize_with_preprocessing(self, image: np.ndarray,
                                     preprocess_func=None) -> List[Dict]:
        """
        带预处理的OCR识别（兼容OCREngine API）

        Args:
            image: 输入图像
            preprocess_func: 预处理函数

        Returns:
            OCR结果列表
        """
        if preprocess_func:
            image = preprocess_func(image)
        return self.recognize(image)

    def extract_text(self, image: np.ndarray, join_char: str = ' ') -> str:
        """
        提取图像中的文本并连接（兼容OCREngine API）

        Args:
            image: 输入图像
            join_char: 连接字符

        Returns:
            str: 所有识别到的文本，用指定字符连接
        """
        results = self.recognize(image)
        texts = [r['text'] for r in results]
        return join_char.join(texts)

    def extract_digits(self, image: np.ndarray, min_confidence: float = 0.5) -> List[str]:
        """
        提取数字（过滤非数字字符）（兼容OCREngine API）

        Args:
            image: 输入图像
            min_confidence: 最小置信度

        Returns:
            list: 数字字符串列表
        """
        results = self.recognize(image)
        digits = []
        for r in results:
            if r['confidence'] >= min_confidence:
                # 过滤非数字字符
                text_digits = ''.join(filter(str.isdigit, r['text']))
                if text_digits:
                    digits.append(text_digits)
        return digits

    def recognize_temperature(self, image: np.ndarray,
                              digit_count: int = 3) -> Tuple[str, float]:
        """
        识别温度值

        Args:
            image: 温度区域图像
            digit_count: 数字位数 (3或4)

        Returns:
            (temperature_string, confidence): 温度字符串和置信度
        """
        return self.multi_digit_recognizer.recognize_temperature(image, digit_count)

    def recognize_digits_with_count(self, image: np.ndarray,
                                    expected_count: Optional[int] = None) -> Tuple[str, List[int], float]:
        """
        识别多个数字

        Args:
            image: 包含多个数字的图像
            expected_count: 预期数字个数

        Returns:
            (digit_string, digits, overall_confidence):
                digit_string: 数字字符串
                digits: 单个数字列表
                overall_confidence: 整体置信度
        """
        return self.multi_digit_recognizer.recognize_digits(image, expected_count)

    def set_language(self, lang: str):
        """设置语言（兼容性方法，实际不使用）"""
        self.lang = lang
        # 数字识别不受语言影响，但保留方法用于兼容性

    def enable_gpu(self, enable: bool = True):
        """启用/禁用GPU（兼容性方法，实际不使用）"""
        self.use_gpu = enable
        # OpenCV数字识别不支持GPU加速，但保留方法用于兼容性

    def analyze_image(self, image: np.ndarray) -> Dict:
        """
        分析图像质量并提取笔画特征

        Args:
            image: 输入图像

        Returns:
            分析结果字典
        """
        # 图像质量分析
        quality = self.feature_extractor.analyze_image_quality(image, mode=self.mode)

        # 笔画特征提取
        features = self.feature_extractor.extract_features(image, mode=self.mode)
        features_with_conf = self.feature_extractor.extract_features_with_confidence(image, mode=self.mode)

        # 数字识别尝试
        digit_result = self.recognize(image)

        return {
            'quality': quality,
            'features': features,
            'features_with_confidence': features_with_conf,
            'digit_recognition': digit_result,
            'image_size': image.shape[:2],
            'mode': self.mode
        }


# 全局数字识别器实例（兼容OCREngine的单例模式）
_default_digit_recognizer = None


def get_global_digit_recognizer(use_gpu=False, lang='ch'):
    """获取全局数字识别器实例（单例模式）"""
    global _default_digit_recognizer
    if _default_digit_recognizer is None:
        _default_digit_recognizer = DigitRecognizer(use_gpu=use_gpu, lang=lang)
    return _default_digit_recognizer


def test_digit_recognizer():
    """测试数字识别器"""
    import sys
    import os

    # 创建测试图像
    test_img = np.ones((100, 60, 3), dtype=np.uint8) * 50

    # 模拟数字1
    cv2.rectangle(test_img, (5, 30), (15, 70), (255, 255, 255), -1)  # 左上竖
    cv2.rectangle(test_img, (5, 70), (15, 90), (255, 255, 255), -1)  # 左下竖

    recognizer = DigitRecognizer()

    # 测试兼容性API
    results = recognizer.recognize(test_img)
    print(f"OCR兼容结果: {results}")

    # 测试文本提取
    text = recognizer.extract_text(test_img)
    print(f"提取文本: '{text}'")

    # 测试数字提取
    digits = recognizer.extract_digits(test_img)
    print(f"提取数字: {digits}")

    # 测试图像分析
    analysis = recognizer.analyze_image(test_img)
    print(f"图像分析 - 特征: {analysis['features']}")

    return results, text, digits


if __name__ == "__main__":
    test_digit_recognizer()
    print("数字识别器测试完成")