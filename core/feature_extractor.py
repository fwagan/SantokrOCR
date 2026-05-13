"""
笔画特征提取器

基于OpenCV的7段数码管笔画特征提取。
支持可配置的笔画区域定义和特征向量归一化。

笔画命名约定（7段数码管标准）：
    a (上横)
    b (右上竖)
    c (右下竖)
    d (下横)
    e (左下竖)
    f (左上竖)
    g (中间横)

特征向量顺序：[a, b, c, d, e, f, g]

更新：使用WhiteLEDRecognizer算法进行特征提取，专注于白色LED识别
"""

import cv2
import numpy as np
from typing import Tuple, List, Dict, Optional

from .white_led_recognizer import WhiteLEDRecognizer


class FeatureExtractor:
    """7段数码管笔画特征提取器"""

    # 用户提供的正常和故障模式字典
    NORMAL_MAPPING = {
        0: [1, 1, 1, 1, 1, 1, 0],
        1: [0, 1, 1, 0, 0, 0, 0],
        2: [1, 1, 0, 1, 1, 0, 1],
        3: [1, 1, 1, 1, 0, 0, 1],
        4: [0, 1, 1, 0, 0, 1, 1],
        5: [1, 0, 1, 1, 0, 1, 1],
        6: [1, 0, 1, 1, 1, 1, 1],
        7: [1, 1, 1, 0, 0, 0, 0],
        8: [1, 1, 1, 1, 1, 1, 1],
        9: [1, 1, 1, 1, 0, 1, 1],
    }

    BROKEN_MAPPING = {
        -2: [1, 1, 1, 1, 1, 1, 0],  # 0的显示中本来g=0，看起来还是0，标记为可疑值-2
        1: [0, 1, 1, 0, 0, 0, 0],   # g=0，不变
        2: [1, 1, 0, 1, 1, 0, 0],   # g=1→0
        3: [1, 1, 1, 1, 0, 0, 0],   # g=1→0
        4: [0, 1, 1, 0, 0, 1, 0],   # g=1→0
        5: [1, 0, 1, 1, 0, 1, 0],   # g=1→0
        6: [1, 0, 1, 1, 1, 1, 0],   # g=1→0
        7: [1, 1, 1, 0, 0, 0, 0],   # g=0，不变
        9: [1, 1, 1, 1, 0, 1, 0],   # g=1→0
    }

    def __init__(self, region_config: Optional[Dict] = None):
        """
        初始化特征提取器

        Args:
            region_config: 笔画区域配置字典，格式为：
                {
                    'a': {'x_start': 0.2, 'x_end': 0.8, 'y_start': 0.0, 'y_end': 0.2},
                    'b': {'x_start': 0.8, 'x_end': 1.0, 'y_start': 0.3, 'y_end': 0.7},
                    ...
                }
                如果为None，使用默认配置
        """
        self.region_config = region_config or self._get_default_region_config()
        self.threshold = 127  # 笔画亮灭判断阈值（根据测试数据调整）

    def _get_default_region_config(self) -> Dict:
        """获取默认笔画区域配置"""
        return {
            'a': {'x_start': 0.2, 'x_end': 0.8, 'y_start': 0.0, 'y_end': 0.2},   # 上横
            'b': {'x_start': 0.8, 'x_end': 1.0, 'y_start': 0.3, 'y_end': 0.7},   # 右上竖
            'c': {'x_start': 0.8, 'x_end': 1.0, 'y_start': 0.7, 'y_end': 1.0},   # 右下竖
            'd': {'x_start': 0.2, 'x_end': 0.8, 'y_start': 0.8, 'y_end': 1.0},   # 下横
            'e': {'x_start': 0.0, 'x_end': 0.2, 'y_start': 0.7, 'y_end': 1.0},   # 左下竖
            'f': {'x_start': 0.0, 'x_end': 0.2, 'y_start': 0.3, 'y_end': 0.7},   # 左上竖
            'g': {'x_start': 0.2, 'x_end': 0.8, 'y_start': 0.4, 'y_end': 0.6},   # 中间横
        }

    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        图像预处理

        Args:
            image: 输入图像 (BGR或灰度)

        Returns:
            二值化图像 (0-255, 0为黑色，255为白色)
        """
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # 自适应阈值或固定阈值
        if gray.shape[0] * gray.shape[1] < 1000:  # 小图像使用固定阈值
            _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
        else:
            # 大图像使用自适应阈值，增强对比度
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            binary = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 11, 2
            )

        # 反转：确保笔画为白色(255)，背景为黑色(0)
        # 7段数码管通常亮段为白色
        if np.mean(binary) > 127:  # 如果大部分是白色，则反转
            binary = cv2.bitwise_not(binary)

        return binary

    def extract_features(self, image: np.ndarray, mode: str = 'normal') -> List[int]:
        """
        提取7段数码管笔画特征
        使用WhiteLEDRecognizer算法进行特征提取，专注于白色LED识别

        Args:
            image: 输入图像 (BGR或灰度)
            mode: 识别模式 'normal' 或 'broken'

        Returns:
            7维特征向量 [a, b, c, d, e, f, g]，1表示亮，0表示灭
        """
        recognizer = WhiteLEDRecognizer(mode=mode)
        return recognizer.extract_features(image)

    def extract_features_with_confidence(self, image: np.ndarray, mode: str = 'normal') -> Tuple[List[int], float]:
        """
        提取特征并计算置信度

        Args:
            image: 输入图像
            mode: 识别模式 'normal' 或 'broken'

        Returns:
            (features, confidence): 特征向量和置信度(0-1)
        """
        features = self.extract_features(image, mode=mode)

        # 计算置信度：基于特征的一致性和图像质量
        binary = self.preprocess_image(image)

        # 简单置信度：基于笔画区域的对比度
        confidences = []
        h, w = binary.shape

        for segment, feature in zip(['a', 'b', 'c', 'd', 'e', 'f', 'g'], features):
            config = self.region_config[segment]
            x_start = int(w * config['x_start'])
            x_end = int(w * config['x_end'])
            y_start = int(h * config['y_start'])
            y_end = int(h * config['y_end'])

            if x_end <= x_start or y_end <= y_start:
                confidences.append(0.0)
                continue

            region = binary[y_start:y_end, x_start:x_end]
            if region.size == 0:
                confidences.append(0.0)
                continue

            # 区域对比度（最大最小差值）
            if region.size > 0:
                region_min = np.min(region)
                region_max = np.max(region)
                contrast = (region_max - region_min) / 255.0
                confidences.append(contrast)
            else:
                confidences.append(0.0)

        # 平均置信度，加权处理
        avg_confidence = np.mean(confidences) if confidences else 0.0

        # 如果图像太小或质量太差，降低置信度
        if h * w < 100:
            avg_confidence *= 0.5

        return features, float(avg_confidence)

    def convert_to_legacy_order(self, features: List[int]) -> List[int]:
        """
        将标准特征顺序转换为LEDDigitClassifier的旧顺序

        LEDDigitClassifier顺序: [上横, 下横, 左上竖, 右上竖, 左下竖, 右下竖, 中间横]
        即: [a, d, f, b, e, c, g]

        Args:
            features: 标准顺序特征 [a, b, c, d, e, f, g]

        Returns:
            旧顺序特征
        """
        if len(features) != 7:
            return features

        return [
            features[0],  # a -> 上横
            features[3],  # d -> 下横
            features[5],  # f -> 左上竖
            features[1],  # b -> 右上竖
            features[4],  # e -> 左下竖
            features[2],  # c -> 右下竖
            features[6],  # g -> 中间横
        ]

    def convert_from_legacy_order(self, features: List[int]) -> List[int]:
        """
        将LEDDigitClassifier的旧顺序转换为标准顺序

        Args:
            features: 旧顺序特征 [上横, 下横, 左上竖, 右上竖, 左下竖, 右下竖, 中间横]

        Returns:
            标准顺序特征 [a, b, c, d, e, f, g]
        """
        if len(features) != 7:
            return features

        return [
            features[0],  # 上横 -> a
            features[3],  # 右上竖 -> b
            features[5],  # 右下竖 -> c
            features[1],  # 下横 -> d
            features[4],  # 左下竖 -> e
            features[2],  # 左上竖 -> f
            features[6],  # 中间横 -> g
        ]

    def match_digit(self, features: List[int], mode: str = 'normal') -> Tuple[int, float]:
        """
        匹配特征向量到数字

        Args:
            features: 7维特征向量 [a, b, c, d, e, f, g]
            mode: 'normal' 或 'broken'，指定使用哪个字典

        Returns:
            (digit, confidence): 识别的数字和匹配置信度
            digit = -1 表示无法识别
            digit = -2 表示可疑值（可能是0或8）
        """
        mapping = self.NORMAL_MAPPING if mode == 'normal' else self.BROKEN_MAPPING

        # 计算与每个数字特征的汉明距离
        best_digit = -1
        best_distance = float('inf')
        best_match = None

        for digit, target_features in mapping.items():
            # 计算汉明距离
            distance = sum(1 for f, t in zip(features, target_features) if f != t)

            if distance < best_distance:
                best_distance = distance
                best_digit = digit
                best_match = target_features

        # 计算置信度
        max_distance = 7  # 最大可能距离
        confidence = 1.0 - (best_distance / max_distance)

        # 如果距离为0，完全匹配
        if best_distance == 0:
            confidence = 1.0
        # 如果距离为1，部分匹配
        elif best_distance == 1:
            confidence = 0.8
        # 如果距离为2，可能匹配
        elif best_distance == 2:
            confidence = 0.5
        # 距离更大认为不可信
        else:
            confidence = 0.1

        return best_digit, confidence

    def analyze_image_quality(self, image: np.ndarray, mode: str = 'normal') -> Dict:
        """
        分析图像质量

        Args:
            image: 输入图像
            mode: 识别模式 'normal' 或 'broken'

        Returns:
            质量指标字典
        """
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        h, w = gray.shape

        # 计算基本质量指标
        mean_brightness = np.mean(gray)
        std_brightness = np.std(gray)
        contrast = np.max(gray) - np.min(gray)

        # 计算边缘信息
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / (h * w)

        # 提取笔画特征（用于质量分析）
        features = self.extract_features(image, mode=mode)
        features_conf = self.extract_features_with_confidence(image, mode=mode)

        return {
            'size': (w, h),
            'mean_brightness': mean_brightness,
            'std_brightness': std_brightness,
            'contrast': contrast,
            'edge_density': edge_density,
            'pixel_count': h * w,
            'aspect_ratio': w / h if h > 0 else 0,
            'features': features,
            'features_confidence': features_conf[1],
        }


def get_global_feature_extractor() -> FeatureExtractor:
    """获取全局特征提取器实例（单例模式）"""
    global _global_feature_extractor
    if '_global_feature_extractor' not in globals():
        _global_feature_extractor = FeatureExtractor()
    return _global_feature_extractor


# 测试函数
if __name__ == "__main__":
    import sys
    import os

    # 创建测试图像
    test_img = np.ones((100, 60, 3), dtype=np.uint8) * 50  # 灰色背景

    # 模拟数字1：左上竖和左下竖亮
    cv2.rectangle(test_img, (5, 30), (15, 70), (255, 255, 255), -1)  # 左上竖
    cv2.rectangle(test_img, (5, 70), (15, 90), (255, 255, 255), -1)  # 左下竖

    extractor = FeatureExtractor()

    # 测试特征提取
    features = extractor.extract_features(test_img)
    print(f"提取的特征向量: {features}")
    print(f"特征顺序: [a,b,c,d,e,f,g] = 上横,右上竖,右下竖,下横,左下竖,左上竖,中间横")

    # 测试数字匹配
    digit, confidence = extractor.match_digit(features, mode='normal')
    print(f"匹配结果: 数字{digit}, 置信度{confidence:.2f}")

    # 测试图像质量分析
    quality = extractor.analyze_image_quality(test_img)
    print(f"图像质量: {quality}")

    print("测试完成")