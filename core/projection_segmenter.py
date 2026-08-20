#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投影分割器 - 基于test_doubao_multiple_1.py算法

专门针对白色LED多数字分割，使用旋转预处理和投影分析。
"""

import os
import shutil
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ===================== 核心配置 =====================
WHITE_LIT_RATIO = 0.25
CUT_EXPAND = 5
TIGHT_EXPAND = 3
ROTATE_ANGLE = 5  # 逆时针旋转5度（与test_doubao_multiple_1.py一致）


class ProjectionSegmenter:
    """投影分割器 - 基于test_doubao_multiple_1.py算法"""

    def __init__(self, rotate_angle: float = ROTATE_ANGLE, cut_expand: int = CUT_EXPAND):
        """
        初始化分割器

        Args:
            rotate_angle: 旋转角度（逆时针为负）
            cut_expand: 切割时扩展的像素数
        """
        self.rotate_angle = rotate_angle
        self.cut_expand = cut_expand

    def safe_save(self, img: np.ndarray, path: str):
        """安全保存图像（用于调试）"""
        dirname = os.path.dirname(path)
        if dirname:  # 只有在目录名非空时才创建目录
            os.makedirs(dirname, exist_ok=True)
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)
        cv2.imwrite(path, img)

    def rotate_image(self, img: np.ndarray, angle: float, debug_prefix: str = None) -> np.ndarray:
        """
        逆时针旋转指定角度，自动补背景色，避免裁剪

        Args:
            img: 输入图像
            angle: 旋转角度（逆时针为负）
            debug_prefix: 调试前缀（用于保存中间结果）

        Returns:
            旋转后的图像
        """
        if angle == 0:
            return img

        # 保存旋转前的原图（如果启用调试）
        if debug_prefix:
            self.safe_save(img, f"{debug_prefix}_00_rotate_original.png")

        # 获取图像尺寸
        (h, w) = img.shape[:2]
        # 计算旋转中心（图像中心）
        center = (w // 2, h // 2)

        # 获取旋转矩阵
        M = cv2.getRotationMatrix2D(center, angle, 1.0)

        # 计算旋转后的图像尺寸（避免裁剪）
        cos = np.abs(M[0, 0])
        sin = np.abs(M[0, 1])
        new_w = int((h * sin) + (w * cos))
        new_h = int((h * cos) + (w * sin))

        # 调整旋转矩阵，使图像居中
        M[0, 2] += (new_w / 2) - center[0]
        M[1, 2] += (new_h / 2) - center[1]

        # 执行旋转（背景色用原图暗色调，避免纯黑）
        # 提取原图暗色调作为旋转背景色
        if len(img.shape) == 3:
            gray_ori = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray_ori = img

        dark_pixels = gray_ori[gray_ori <= 50]
        if len(dark_pixels) == 0:
            bg_color = (50, 50, 50) if len(img.shape) == 3 else 50
        else:
            bg_gray = int(np.mean(dark_pixels))
            bg_color = (bg_gray, bg_gray, bg_gray) if len(img.shape) == 3 else bg_gray

        rotated = cv2.warpAffine(img, M, (new_w, new_h), borderValue=bg_color)

        if debug_prefix:
            self.safe_save(rotated, f"{debug_prefix}_00_rotated_{angle}deg.png")

        return rotated

    def preprocess(self, image: np.ndarray, debug_prefix: str = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        图像预处理（与test_doubao_multiple_1.py一致）

        Args:
            image: 输入图像（BGR）
            debug_prefix: 调试前缀

        Returns:
            (binary, original): 二值化图像和原始图像
        """
        # 步骤1：转换为灰度
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # 步骤2：高斯模糊去噪
        blur = cv2.GaussianBlur(gray, (3, 3), 0)

        # 步骤3：OTSU二值化（全局阈值）
        _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        if debug_prefix:
            self.safe_save(gray, f"{debug_prefix}_02_global_gray.png")
            self.safe_save(blur, f"{debug_prefix}_03_blur.png")
            self.safe_save(binary, f"{debug_prefix}_04_global_thresh.png")

        return binary, image

    def segment_digits(self, image: np.ndarray, debug_prefix: str = None) -> List[Dict]:
        """
        分割多个数字（基于test_doubao_multiple_1.py的project_multiple_image算法）

        Args:
            image: 输入图像（包含多个数字）
            debug_prefix: 调试前缀

        Returns:
            分割结果列表，每个元素为字典：
            {
                'bbox': (x, y, w, h),
                'image': 裁剪的图像（原始颜色，已旋转），
                'rotated_bbox': (x, y, w, h)（在旋转后图像中的位置），
                'aspect_ratio': 宽高比,
                'area': 区域面积
            }
        """
        # 预处理：获取二值图像和旋转后的原始图像
        binary, original_img = self.preprocess(image, debug_prefix)

        h, w = binary.shape

        # 计算垂直投影
        proj = np.sum(binary == 255, axis=0)

        # 计算阈值：最大投影的8%
        threshold = np.max(proj) * 0.08 if np.max(proj) != 0 else 0
        is_black = proj < threshold

        # 找到数字间隔
        intervals = []
        in_digit = False
        start = 0

        for x in range(w):
            if not in_digit and not is_black[x]:
                in_digit = True
                start = x
            elif in_digit and is_black[x]:
                in_digit = False
                intervals.append((start, x))

        if in_digit:
            intervals.append((start, w - 1))

        # 扩展间隔
        expanded = []
        for l, r in intervals:
            expanded.append((max(0, l - self.cut_expand), min(w - 1, r + self.cut_expand)))

        # 分割数字
        segments = []
        for i, (l, r) in enumerate(expanded):
            # 裁剪数字区域（使用原始图像，与test_doubao_multiple_1.py一致）
            digit = original_img[:, l:r]

            # 计算垂直方向的紧包裹（去除上下多余背景）
            if len(digit.shape) == 3:
                gray_digit = cv2.cvtColor(digit, cv2.COLOR_BGR2GRAY)
            else:
                gray_digit = digit

            _, th = cv2.threshold(gray_digit, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            row_proj = np.sum(th == 255, axis=1)
            valid_rows = np.where(row_proj > 0)[0]

            if len(valid_rows) > 0:
                y1 = max(0, valid_rows[0] - TIGHT_EXPAND)
                y2 = min(digit.shape[0] - 1, valid_rows[-1] + TIGHT_EXPAND)
                digit_final = digit[y1:y2+1, :]

                # 计算边界框（在原始图像中的位置）
                bbox = (l, y1, r - l, y2 - y1 + 1)
            else:
                digit_final = digit
                bbox = (l, 0, r - l, digit.shape[0])

            # 计算宽高比和面积
            h_seg, w_seg = digit_final.shape[:2]
            aspect_ratio = w_seg / h_seg if h_seg > 0 else 0
            area = w_seg * h_seg

            segments.append({
                'bbox': bbox,  # 在原始图像中的位置
                'image': digit_final,  # 裁剪后的原始颜色图像（未旋转）
                'rotated_bbox': bbox,
                'aspect_ratio': aspect_ratio,
                'area': area,
                'method': 'projection',
                'digit_index': i
            })

            # 调试保存
            if debug_prefix:
                self.safe_save(digit_final, f"{debug_prefix}_digit_{i+1}_raw.png")

        # 按x坐标排序（从左到右）
        segments.sort(key=lambda s: s['bbox'][0])

        return segments

    def process_digit_1(self, digit_image: np.ndarray, debug_prefix: str = None) -> np.ndarray:
        """
        处理数字1：旋转+精准裁切+等比例缩放+靠右（与test_doubao_multiple_1.py一致）

        Args:
            digit_image: 单个数字图像（原始颜色）
            debug_prefix: 调试前缀

        Returns:
            处理后的30x50图像
        """
        # 步骤1：切分后先逆时针旋转5度（核心新增）
        img_rotated = self.rotate_image(digit_image, self.rotate_angle, debug_prefix)
        h, w = img_rotated.shape[:2]

        if debug_prefix:
            self.safe_save(img_rotated, f"{debug_prefix}_01_raw_crop_rotated.png")

        # 步骤2：精准裁切1的有效区域（去除上下多余）
        if len(img_rotated.shape) == 3:
            gray = cv2.cvtColor(img_rotated, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_rotated

        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        if debug_prefix:
            self.safe_save(th, f"{debug_prefix}_00_1_thresh.png")

        row_proj = np.sum(th == 255, axis=1)
        valid_rows = np.where(row_proj > 0)[0]
        col_proj = np.sum(th == 255, axis=0)
        valid_cols = np.where(col_proj > 0)[0]

        if len(valid_rows) == 0 or len(valid_cols) == 0:
            cropped = img_rotated
        else:
            y1 = max(0, valid_rows[0] - TIGHT_EXPAND)
            y2 = min(img_rotated.shape[0] - 1, valid_rows[-1] + TIGHT_EXPAND)
            x1 = max(0, valid_cols[0] - TIGHT_EXPAND)
            x2 = min(img_rotated.shape[1] - 1, valid_cols[-1] + TIGHT_EXPAND)
            cropped = img_rotated[y1:y2+1, x1:x2+1]

        if debug_prefix:
            self.safe_save(cropped, f"{debug_prefix}_00_1_cropped_valid.png")

        # 步骤3：等比例缩放到宽度≤30
        h_crop, w_crop = cropped.shape[:2]
        scale = 30 / w_crop
        new_w = 30
        new_h = int(h_crop * scale)

        if new_h > 50:
            scale = 50 / new_h
            new_h = 50
            new_w = int(new_w * scale)

        resized = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_AREA)

        if debug_prefix:
            self.safe_save(resized, f"{debug_prefix}_02_resized.png")

        # 步骤4：自定义暗背景画布+靠右
        if len(digit_image.shape) == 3:
            gray_ori = cv2.cvtColor(digit_image, cv2.COLOR_BGR2GRAY)
        else:
            gray_ori = digit_image

        dark_pixels = gray_ori[gray_ori <= 50]
        if len(dark_pixels) == 0:
            bg_color = (50, 50, 50) if len(digit_image.shape) == 3 else 50
        else:
            bg_gray = int(np.mean(dark_pixels))
            bg_color = (bg_gray, bg_gray, bg_gray) if len(digit_image.shape) == 3 else bg_gray

        if len(digit_image.shape) == 3:
            canvas = np.full((50, 30, 3), bg_color, dtype=np.uint8)
        else:
            canvas = np.full((50, 30), bg_color, dtype=np.uint8)

        if debug_prefix:
            self.safe_save(canvas, f"{debug_prefix}_03_canvas_custom_bg.png")

        x_offset = 30 - new_w
        y_offset = (50 - new_h) // 2

        if len(canvas.shape) == 3:
            canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized
        else:
            if len(resized.shape) == 3:
                # 如果resized是彩色，转换为灰度
                resized_gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
                canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized_gray
            else:
                canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized

        if debug_prefix:
            self.safe_save(canvas, f"{debug_prefix}_04_right_aligned.png")
            self.safe_save(canvas, f"{debug_prefix}_05_final_30x50.png")

        return canvas

    def process_digit_other(self, digit_image: np.ndarray, debug_prefix: str = None) -> np.ndarray:
        """
        处理其他数字：旋转+紧包裹→直接拉伸到30x50

        Args:
            digit_image: 单个数字图像（原始颜色）
            debug_prefix: 调试前缀

        Returns:
            处理后的30x50图像
        """
        # 步骤1：切分后先逆时针旋转5度（核心新增）
        img_rotated = self.rotate_image(digit_image, self.rotate_angle, debug_prefix)

        if debug_prefix:
            self.safe_save(img_rotated, f"{debug_prefix}_01_raw_crop_rotated.png")

        # 步骤2：紧包裹清理背景
        if len(img_rotated.shape) == 3:
            gray = cv2.cvtColor(img_rotated, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_rotated

        if debug_prefix:
            self.safe_save(gray, f"{debug_prefix}_00_gray.png")

        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        if debug_prefix:
            self.safe_save(th, f"{debug_prefix}_00_thresh.png")

        col_proj = np.sum(th == 255, axis=0)
        row_proj = np.sum(th == 255, axis=1)
        cols = np.where(col_proj > 2)[0]
        rows = np.where(row_proj > 2)[0]

        if len(cols) == 0 or len(rows) == 0:
            cropped = img_rotated
        else:
            x1 = max(0, cols[0] - TIGHT_EXPAND)
            x2 = min(img_rotated.shape[1] - 1, cols[-1] + TIGHT_EXPAND)
            y1 = max(0, rows[0] - TIGHT_EXPAND)
            y2 = min(img_rotated.shape[0] - 1, rows[-1] + TIGHT_EXPAND)
            cropped = img_rotated[y1:y2+1, x1:x2+1]

        if debug_prefix:
            self.safe_save(cropped, f"{debug_prefix}_00_cropped_clean.png")

        # 步骤3：直接拉伸到30x50
        final = cv2.resize(cropped, (30, 50), interpolation=cv2.INTER_AREA)

        if debug_prefix:
            self.safe_save(final, f"{debug_prefix}_02_stretched_30x50.png")

        return final

    def preprocess_single_digit(self, digit_image: np.ndarray, is_digit_1: bool = None,
                               debug_prefix: str = None) -> np.ndarray:
        """
        预处理单个数字图像（判断是否为1，然后选择对应处理方案）

        Args:
            digit_image: 单个数字图像
            is_digit_1: 是否强制为数字1（如果为None则自动判断）
            debug_prefix: 调试前缀

        Returns:
            预处理后的30x50图像
        """
        # 自动判断是否为数字1（如果未指定）
        if is_digit_1 is None:
            h, w = digit_image.shape[:2]
            if len(digit_image.shape) == 3:
                gray = cv2.cvtColor(digit_image, cv2.COLOR_BGR2GRAY)
            else:
                gray = digit_image

            _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            col_proj = np.sum(th == 255, axis=0)
            valid_cols = np.where(col_proj > 0)[0]
            valid_width = len(valid_cols) if len(valid_cols) > 0 else 0
            aspect_ratio = w / h

            # 判断是否是1
            is_digit_1 = (aspect_ratio < 0.5)

            if debug_prefix:
                with open(f"{debug_prefix}_00_is_digit_1.txt", 'w') as f:
                    f.write(f"is_digit_1: {is_digit_1}\n")
                    f.write(f"aspect_ratio: {aspect_ratio}\n")
                    f.write(f"valid_width: {valid_width}\n")

        # 选择处理方案
        if is_digit_1:
            return self.process_digit_1(digit_image, debug_prefix)
        else:
            return self.process_digit_other(digit_image, debug_prefix)


def test_segmenter():
    """测试投影分割器"""
    print("投影分割器测试")
    print("=" * 80)

    # 创建测试图像（模拟3个数字）
    test_img = np.ones((80, 150, 3), dtype=np.uint8) * 50

    # 模拟数字1, 2, 3
    cv2.rectangle(test_img, (20, 20), (35, 60), (255, 255, 255), -1)  # 数字1（细长）
    cv2.rectangle(test_img, (60, 20), (80, 60), (255, 255, 255), -1)  # 数字2
    cv2.rectangle(test_img, (100, 20), (120, 60), (255, 255, 255), -1)  # 数字3

    segmenter = ProjectionSegmenter()

    # 测试分割
    segments = segmenter.segment_digits(test_img, debug_prefix="test_segment")
    print(f"分割出 {len(segments)} 个数字")

    for i, seg in enumerate(segments):
        x, y, w, h = seg['bbox']
        aspect_ratio = seg['aspect_ratio']
        area = seg['area']
        print(f"数字 {i+1}: 位置({x},{y}) 尺寸{w}x{h}, 宽高比={aspect_ratio:.2f}, 面积={area}")

    # 测试数字1预处理
    if len(segments) > 0:
        digit1_img = segments[0]['image']
        processed = segmenter.preprocess_single_digit(digit1_img, is_digit_1=True, debug_prefix="test_digit1")
        print(f"数字1预处理完成，输出尺寸: {processed.shape}")

    return segments


if __name__ == "__main__":
    segments = test_segmenter()
    print("投影分割器测试完成")