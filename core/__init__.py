"""
SantokrOCR 核心算法模块
包含视频处理、OCR识别、故障位分类等核心功能
"""

from .video_extractor import VideoDigitExtractor
from .led_classifier import LEDDigitClassifier
from .digit_recognizer import DigitRecognizer
from .white_led_recognizer import WhiteLEDRecognizer
from .feature_extractor import FeatureExtractor
from .projection_segmenter import ProjectionSegmenter
from .multi_digit_recognizer import MultiDigitRecognizer
from .digit_recognition_pipeline import DigitRecognitionPipeline
# from .ocr_engine import OCREngine  # 已废弃

__all__ = [
    'VideoDigitExtractor',
    'LEDDigitClassifier',
    'DigitRecognizer',
    'WhiteLEDRecognizer',
    'FeatureExtractor',
    'ProjectionSegmenter',
    'MultiDigitRecognizer',
    'DigitRecognitionPipeline',
    # 'OCREngine',  # 已废弃
]