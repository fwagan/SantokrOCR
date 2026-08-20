#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
白色LED数字识别器
基于test_doubao_2.py算法，专注于白色LED数字识别
"""

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


class WhiteLEDRecognizer:
    """白色LED数字识别器（基于test_doubao_2.py算法）"""

    # 固定像素坐标分割区域（在30x50图像上）
    # 坐标格式: (y1, y2, x1, x2)
    # 更新为与test_doubao_multiple_1.py的SEGMENT_ORIGINAL一致
    SEGMENT_AREAS = {
        'a': (2, 10, 11, 19),   # 顶部横段（缩减到原始宽度的1/2避免覆盖右上竖）
        'b': (10, 24, 20, 27), # 右上竖段 - 更新x坐标：20-27（原为22-27）
        'c': (26, 40, 20, 27), # 右下竖段 - 更新x坐标：20-27（原为22-27）
        'd': (40, 48, 5, 21),  # 底部横段 - 更新：下横左移2像素（原为7-23）
        'e': (26, 40, 3, 8),   # 左下竖段
        'f': (10, 24, 3, 8),   # 左上竖段
        'g': (23, 27, 12, 18)   # g段：高度仅4像素，宽度缩到1/2避免误覆盖
    }

    # 白字占比阈值
    WHITE_LIT_RATIO = 0.25

    # 数字模板（标准顺序：[a,b,c,d,e,f,g]）
    STANDARD = {
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

    # g段损坏模式（标准顺序）
    BROKEN_G = {
        1: [0, 1, 1, 0, 0, 0, 0],  # 1不受g段影响，但显式加入确保故障模式下的精确匹配
        2: [1, 1, 0, 1, 1, 0, 0],  # g段损坏的2
        3: [1, 1, 1, 1, 0, 0, 0],  # g段损坏的3
        4: [0, 1, 1, 0, 0, 1, 0],  # g段损坏的4
        5: [1, 0, 1, 1, 0, 1, 0],  # g段损坏的5
        6: [1, 0, 1, 1, 1, 1, 0],  # g段损坏的6
        7: [1, 1, 1, 0, 0, 0, 0],  # 7不受g段影响，显式加入确保精确匹配
        8: [1, 1, 1, 1, 1, 1, 0],  # g段损坏的8（实际上就是0/8，标记为-2）
        9: [1, 1, 1, 1, 0, 1, 0],  # g段损坏的9
    }

    def __init__(self, target_size=(30, 50), mode='normal'):
        """
        初始化识别器

        Args:
            target_size: 目标图像尺寸（宽，高），默认为(30, 50)
            mode: 'normal' 或 'broken'，指定识别模式
        """
        self.target_width, self.target_height = target_size
        self.mode = mode

    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        图像预处理（针对白色LED）

        Args:
            image: 输入图像（BGR或灰度）

        Returns:
            预处理后的二值化图像（30x50大小）
        """
        # 转换为灰度
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # 高斯模糊去噪
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # Resize到目标尺寸
        gray = cv2.resize(gray, (self.target_width, self.target_height))

        # OTSU二值化（白色LED使用OTSU）
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return binary

    def extract_segments(self, image: np.ndarray) -> Tuple[List[int], np.ndarray]:
        """
        提取7段数码管笔画特征

        Args:
            image: 输入图像

        Returns:
            (segments, binary_image): 特征向量和二值化图像
        """
        # 预处理图像
        binary = self.preprocess_image(image)

        # 提取段状态
        segments = []
        seg_names = list(self.SEGMENT_AREAS.keys())

        for seg_name in seg_names:
            y1, y2, x1, x2 = self.SEGMENT_AREAS[seg_name]
            seg_area = binary[y1:y2, x1:x2]

            # 计算白色像素比例
            white_pix = np.sum(seg_area == 255)
            total_pix = seg_area.size if seg_area.size > 0 else 1
            ratio = white_pix / total_pix

            # 判断亮灭（白色LED使用WHITE_LIT_RATIO阈值）
            is_lit = ratio > self.WHITE_LIT_RATIO
            segments.append(1 if is_lit else 0)

        return segments, binary

    def extract_features(self, image: np.ndarray) -> List[int]:
        """
        提取特征向量（标准顺序：[a,b,c,d,e,f,g]）

        Args:
            image: 输入图像

        Returns:
            7维特征向量，1表示亮，0表示灭
        """
        segments, _ = self.extract_segments(image)
        return segments

    def recognize_digit(self, segments: List[int], mode: str = None) -> int:
        """
        严格按字典精确匹配识别数字。

        不再使用「按亮段数猜数字」的兜底逻辑。
        只做精确的字典匹配——匹配不到就返回 -1，由用户人工处理。

        Args:
            segments: 7维特征向量，标准顺序[a,b,c,d,e,f,g]
            mode: 'normal' 或 'broken'，为None时使用self.mode

        Returns:
            识别的数字（-1表示无法识别，-2表示可能是0或8）
        """
        if mode is None:
            mode = self.mode

        if mode == 'normal':
            # 精确匹配 STANDARD 字典
            for num, pattern in self.STANDARD.items():
                if segments == pattern:
                    return num
            return -1

        else:  # broken mode
            # 先匹配 g段损坏模式
            for num, pattern in self.BROKEN_G.items():
                if segments == pattern:
                    if num == 8:  # g段损坏的8 = 0/8 歧义
                        return -2
                    return num
            # 再匹配不依赖g段的数字（STANDARD中g=0的条目：1和7）
            for num, pattern in self.STANDARD.items():
                if pattern[6] == 0 and segments == pattern:
                    return num
            return -1

    def recognize(self, image: np.ndarray, mode: str = None) -> Tuple[int, float, bool]:
        """
        识别数字并返回置信度和存疑标志

        Args:
            image: 输入图像
            mode: 'normal' 或 'broken'，为None时使用self.mode

        Returns:
            (digit, confidence, is_suspicious): 识别的数字、置信度(0-1)和是否存疑
            digit = -1 表示无法识别
            digit = -2 表示可能是0或8
            is_suspicious = True 表示识别到的亮段与映射不一致
        """
        try:
            # 提取特征
            segments = self.extract_features(image)

            # 识别数字
            if mode is None:
                mode = self.mode
            digit = self.recognize_digit(segments, mode)

            # 严格匹配模式下，digit=-1表示无匹配失败，digit>=0表示精确匹配到字典
            # is_suspicious 不再有意义（精确匹配时总是False）
            if digit == -1:
                confidence = 0.0
            elif digit == -2:
                confidence = 0.7
            else:
                confidence = 0.95

            return digit, confidence, False

        except Exception as e:
            print(f"识别失败：{e}")
            return -1, 0.0, False

    def get_segment_info(self, image: np.ndarray) -> Dict:
        """
        获取详细的段信息（用于调试）

        Args:
            image: 输入图像

        Returns:
            包含详细信息的字典
        """
        segments, binary = self.extract_segments(image)

        # 计算每个段的详细统计
        segment_details = {}
        seg_names = list(self.SEGMENT_AREAS.keys())

        for i, seg_name in enumerate(seg_names):
            y1, y2, x1, x2 = self.SEGMENT_AREAS[seg_name]
            seg_area = binary[y1:y2, x1:x2]

            white_pix = np.sum(seg_area == 255)
            total_pix = seg_area.size if seg_area.size > 0 else 1
            ratio = white_pix / total_pix

            segment_details[seg_name] = {
                'is_lit': segments[i] == 1,
                'white_ratio': ratio,
                'white_pixels': white_pix,
                'total_pixels': total_pix,
                'area': (y1, y2, x1, x2)
            }

        lit_count = sum(segments)
        digit = self.recognize_digit(segments)

        return {
            'segments': segments,
            'segment_names': seg_names,
            'segment_details': segment_details,
            'lit_count': lit_count,
            'digit': digit,
            'binary_shape': binary.shape
        }

    def visualize_segments(self, image: np.ndarray, save_path: Optional[str] = None) -> np.ndarray:
        """
        可视化段分割结果

        Args:
            image: 输入图像
            save_path: 保存路径（可选）

        Returns:
            可视化图像
        """
        import matplotlib.pyplot as plt

        segments, binary = self.extract_segments(image)
        seg_info = self.get_segment_info(image)

        # 创建可视化
        fig, axes = plt.subplots(2, 4, figsize=(16, 8))
        axes = axes.ravel()

        # 显示原始图像
        if len(image.shape) == 3:
            display_img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            display_img = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)

        axes[0].imshow(display_img)
        axes[0].set_title("Original Image")
        axes[0].axis('off')

        # 显示二值化图像
        axes[1].imshow(binary, cmap='gray')
        axes[1].set_title(f"Binary Image\nSize: {binary.shape}")
        axes[1].axis('off')

        # 显示每个段
        seg_names = seg_info['segment_names']
        for i, seg_name in enumerate(seg_names):
            if i >= 6:  # 只显示前6个段
                break

            y1, y2, x1, x2 = self.SEGMENT_AREAS[seg_name]

            # 创建段掩码
            segment_mask = np.zeros_like(binary)
            segment_mask[y1:y2, x1:x2] = binary[y1:y2, x1:x2]

            is_lit = seg_info['segment_details'][seg_name]['is_lit']
            ratio = seg_info['segment_details'][seg_name]['white_ratio']

            axes[i+2].imshow(segment_mask, cmap='gray')
            axes[i+2].set_title(f"{seg_name}: {'ON' if is_lit else 'OFF'}\nratio={ratio:.2f}")
            axes[i+2].axis('off')

        # 隐藏最后一个未使用的子图
        axes[-1].axis('off')

        # 添加整体信息
        digit = seg_info['digit']
        lit_count = seg_info['lit_count']
        plt.suptitle(f"WhiteLEDRecognizer\nSegments: {segments}\nDigit: {digit}, Lit count: {lit_count}", fontsize=14)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150)

        plt.show()

        return binary


def get_white_led_recognizer() -> WhiteLEDRecognizer:
    """获取全局白色LED识别器实例（单例模式）"""
    global _global_white_led_recognizer
    if '_global_white_led_recognizer' not in globals():
        _global_white_led_recognizer = WhiteLEDRecognizer()
    return _global_white_led_recognizer


if __name__ == "__main__":
    # 测试代码
    print("白色LED识别器测试")
    print("=" * 80)

    # 创建测试图像
    test_img = np.ones((100, 60, 3), dtype=np.uint8) * 50  # 灰色背景

    # 模拟数字1：右上竖和右下竖亮（b和c段）
    # 注意：在30x50图像上，b段是(10:24, 22:27)，c段是(26:40, 22:27)
    # 这里用近似比例
    h, w = test_img.shape[:2]
    cv2.rectangle(test_img, (int(w*0.73), int(h*0.2)), (int(w*0.9), int(h*0.48)), (255, 255, 255), -1)  # 右上竖
    cv2.rectangle(test_img, (int(w*0.73), int(h*0.52)), (int(w*0.9), int(h*0.8)), (255, 255, 255), -1)  # 右下竖

    recognizer = WhiteLEDRecognizer()

    # 测试特征提取
    features = recognizer.extract_features(test_img)
    print(f"提取的特征向量: {features}")
    print(f"特征顺序: [a,b,c,d,e,f,g]")

    # 测试数字识别
    digit, confidence = recognizer.recognize(test_img)
    print(f"识别结果: 数字{digit}, 置信度{confidence:.2f}")

    # 获取详细信息
    seg_info = recognizer.get_segment_info(test_img)
    print(f"亮段数: {seg_info['lit_count']}")
    print(f"段详情:")
    for seg_name, details in seg_info['segment_details'].items():
        print(f"  {seg_name}: {'ON' if details['is_lit'] else 'OFF'} (ratio={details['white_ratio']:.2f})")

    print("测试完成")