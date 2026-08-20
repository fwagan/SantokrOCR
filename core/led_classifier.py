"""
故障位LED数字分类器

基于笔画特征的数字识别，专门针对故障LED显示设计。
集成了用户提供的正常和故障模式字典，提供高精度数字识别。

更新：使用WhiteLEDRecognizer算法进行特征提取，专注于白色LED识别
"""

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .white_led_recognizer import WhiteLEDRecognizer

# 用户提供的正常和故障模式字典
# 特征向量顺序: [a,b,c,d,e,f,g] = [上横,右上竖,右下竖,下横,左下竖,左上竖,中间横]
NORMAL_DIGIT_MAPPING = {
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

# 中间段 g 坏掉：索引6强制=0
BROKEN_DIGIT_MAPPING = {
    -2: [1, 1, 1, 1, 1, 1, 0],  # 0的显示中本来g=0，看起来还是0，标记为可疑值-2
    1: [0, 1, 1, 0, 0, 0, 0],   # g=0，不变
    2: [1, 1, 0, 1, 1, 0, 0],   # g=1→0
    3: [1, 1, 1, 1, 0, 0, 0],   # g=1→0
    4: [0, 1, 1, 0, 0, 1, 0],   # g=1→0
    5: [1, 0, 1, 1, 0, 1, 0],   # g=1→0
    6: [1, 0, 1, 1, 1, 1, 0],   # g=1→0
    7: [1, 1, 1, 0, 0, 0, 0],   # g=0，不变
    # 8不存在了
    9: [1, 1, 1, 1, 0, 1, 0],   # g=1→0
}


class LEDDigitClassifier:
    """针对故障LED的专用分类器（使用WhiteLEDRecognizer算法）"""

    def __init__(self, training_samples=None, mode='normal'):
        """
        初始化分类器

        Args:
            training_samples: [(image, label)] 用于训练
            mode: 'normal' 或 'broken'，指定默认识别模式
        """
        self.model = None
        self.mapping = {}
        self.normal_mapping = NORMAL_DIGIT_MAPPING
        self.broken_mapping = BROKEN_DIGIT_MAPPING
        self.mode = mode  # 'normal' 或 'broken'

        if training_samples:
            self.train(training_samples)

    def extract_features(self, image):
        """
        提取笔画特征
        返回7维特征向量: [a,b,c,d,e,f,g] = [上横,右上竖,右下竖,下横,左下竖,左上竖,中间横]
        使用WhiteLEDRecognizer算法进行特征提取，专注于白色LED识别
        """
        recognizer = WhiteLEDRecognizer()
        return recognizer.extract_features(image)

    # 注意: 已删除转换方法，extract_features()现在直接返回标准顺序

    def classify(self, image):
        """对单张图片分类（使用WhiteLEDRecognizer的识别算法）"""
        recognizer = WhiteLEDRecognizer()

        # 提取特征
        features = self.extract_features(image)

        # 首先尝试使用用户提供的字典
        digit = self._recognize_with_dictionary(features, mode=self.mode)
        if digit != -1:
            return digit

        # 如果字典识别失败，使用WhiteLEDRecognizer的识别算法
        return recognizer.recognize_digit(features, mode=self.mode)

    def _recognize_with_dictionary(self, features, mode='normal'):
        """
        使用用户字典识别数字

        Args:
            features: 标准顺序特征向量 [a,b,c,d,e,f,g]
            mode: 'normal' 或 'broken'

        Returns:
            识别的数字，-1表示无法识别
        """
        # 选择字典
        mapping = self.normal_mapping if mode == 'normal' else self.broken_mapping

        # 查找精确匹配
        for digit, target_features in mapping.items():
            if features == target_features:
                return digit

        # 如果没有精确匹配，计算最近邻（汉明距离最小）
        best_digit = -1
        best_distance = float('inf')

        for digit, target_features in mapping.items():
            distance = sum(1 for f, t in zip(features, target_features) if f != t)
            if distance < best_distance:
                best_distance = distance
                best_digit = digit

        # 如果距离过大，认为无法识别
        if best_distance > 2:  # 允许最多2个笔画差异
            return -1

        return best_digit

    def recognize(self, image, mode=None):
        """
        增强的数字识别方法（推荐使用）
        使用WhiteLEDRecognizer进行识别

        Args:
            image: 输入图像
            mode: 'normal' 或 'broken'，为None时使用默认模式

        Returns:
            (digit, confidence, is_suspicious): 识别的数字、置信度(0-1)和是否存疑
            digit = -1 表示无法识别
            digit = -2 表示可疑值（可能是0或8）
            is_suspicious = True 表示识别到的亮段与映射不一致
        """
        # 如果mode为None，使用默认模式
        if mode is None:
            mode = self.mode

        recognizer = WhiteLEDRecognizer(mode=mode)
        return recognizer.recognize(image, mode)

    def set_mode(self, mode):
        """设置识别模式 ('normal' 或 'broken')"""
        if mode not in ['normal', 'broken']:
            raise ValueError(f"模式必须是 'normal' 或 'broken', 得到: {mode}")
        self.mode = mode

    def train(self, samples):
        """
        用真实样本训练（简单版本：统计特征频率）
        samples: [(image, label)]
        """
        feature_stats = {}
        for img, label in samples:
            features = self.extract_features(img)
            if features not in feature_stats:
                feature_stats[features] = {}
            feature_stats[features][label] = feature_stats[features].get(label, 0) + 1

        # 构建映射：特征 -> 最常见标签
        self.mapping = {}
        for features, counts in feature_stats.items():
            self.mapping[features] = max(counts, key=counts.get)

        print(f"训练完成，学习到 {len(self.mapping)} 个特征模式")

    def predict(self, image):
        """预测数字"""
        if hasattr(self, 'mapping') and self.mapping:
            features = self.extract_features(image)
            digit = self.mapping.get(features, -1)
            if digit != -1:
                return digit

        # 尝试字典识别
        features = self.extract_features(image)
        digit = self._recognize_with_dictionary(features, mode=self.mode)
        if digit != -1:
            return digit

        # 回退到硬编码分类
        return self.classify(image)

    def predict_with_confidence(self, image):
        """
        预测数字并返回置信度和存疑标志（推荐使用）
        使用WhiteLEDRecognizer进行识别

        返回: (digit, confidence, is_suspicious)
        """
        # 如果用户有训练过的映射，优先使用
        if hasattr(self, 'mapping') and self.mapping:
            features = self.extract_features(image)
            if features in self.mapping:
                return self.mapping[features], 0.9, False
            # 如果映射中没有，尝试字典识别
            digit = self._recognize_with_dictionary(features, mode=self.mode)
            if digit != -1:
                return digit, 0.8, False
            else:
                # 使用WhiteLEDRecognizer
                recognizer = WhiteLEDRecognizer(mode=self.mode)
                return recognizer.recognize(image, mode=self.mode)

        # 使用WhiteLEDRecognizer
        recognizer = WhiteLEDRecognizer(mode=self.mode)
        return recognizer.recognize(image, mode=self.mode)

    def save_model(self, filepath):
        """保存模型到文件"""
        import pickle
        with open(filepath, 'wb') as f:
            pickle.dump({
                'mapping': self.mapping,
                'model': self.model
            }, f)

    def load_model(self, filepath):
        """从文件加载模型"""
        import pickle
        with open(filepath, 'rb') as f:
            data = pickle.load(f)
            self.mapping = data.get('mapping', {})
            self.model = data.get('model', None)