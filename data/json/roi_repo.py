"""JsonRoiRepository：ROI 配置存储（含新旧格式兼容）"""

import logging
import os
from typing import Any, Dict, Optional

from data.json._utils import atomic_write, json_lock, load_json
from data.types import RoiConfig, RoiEntry

logger = logging.getLogger(__name__)

_FILENAME = "rois.json"


class JsonRoiRepository:
    """JSON 文件存储的 ROI 配置仓库

    支持新旧两种缓存格式（向后兼容）：
    - 新格式：{'rois': {...}, 'rotation_angle': ..., 'start_frame': ...}
    - 旧格式：ROI 数据直接在顶层
    - 列表格式：旧版列表结构
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _file_path(self, video_hash: str) -> str:
        return os.path.join(self.base_dir, video_hash, _FILENAME)

    def save(self, video_hash: str, config: RoiConfig) -> None:
        path = self._file_path(video_hash)
        serializable = self._make_serializable(config.get('rois', {}))
        payload: Dict[str, Any] = {'rois': serializable}
        rotation_angle = config.get('rotation_angle')
        start_frame = config.get('start_frame')
        if rotation_angle is not None:
            payload['rotation_angle'] = float(rotation_angle)
        if start_frame is not None:
            payload['start_frame'] = int(start_frame)
        with json_lock:
            atomic_write(path, payload)
        logger.info(f"ROI 已保存: {path} ({len(serializable)} 个)")

    def load(self, video_hash: str) -> Optional[RoiConfig]:
        """返回 {'rois': {name: (x,y,w,h)}, 'rotation_angle': ..., 'start_frame': ...}
        或 None（不存在时）"""
        path = self._file_path(video_hash)
        loaded = load_json(path)
        if loaded is None:
            return None

        try:
            return self._parse_loaded(loaded)
        except Exception as e:
            logger.error(f"ROI 配置解析失败: {path}, 错误: {e}")
            return None

    def _parse_loaded(self, loaded) -> Optional[RoiConfig]:
        """将 JSON 数据解析为标准 ROI 配置格式"""
        rois: Dict[str, dict] = {}
        rotation_angle = None
        start_frame = None

        if isinstance(loaded, dict):
            if 'rois' in loaded:
                # 新格式
                roi_dict = loaded['rois']
                rotation_angle = loaded.get('rotation_angle')
                start_frame = loaded.get('start_frame')
            else:
                # 旧格式：ROI 在顶层
                roi_dict = loaded

            for name, roi_data in roi_dict.items():
                parsed = self._parse_roi_entry(name, roi_data)
                if parsed is not None:
                    rois[name] = parsed

        elif isinstance(loaded, list):
            # 旧列表格式
            for item in loaded:
                if isinstance(item, dict) and 'name' in item:
                    parsed = self._parse_roi_entry(item['name'], item)
                    if parsed is not None:
                        rois[item['name']] = parsed

        if not rois:
            return None

        result: RoiConfig = {'rois': rois}
        if rotation_angle is not None:
            result['rotation_angle'] = rotation_angle
        if start_frame is not None:
            result['start_frame'] = start_frame
        return result

    def delete(self, video_hash: str) -> None:
        path = self._file_path(video_hash)
        with json_lock:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"ROI 已删除: {path}")

    def exists(self, video_hash: str) -> bool:
        return os.path.exists(self._file_path(video_hash))

    @staticmethod
    def _make_serializable(rois: dict) -> dict:
        """将各种 ROI 输入格式统一为 {name: {x, y, width, height}}"""
        result: Dict[str, dict] = {}
        if isinstance(rois, dict):
            for name, roi in rois.items():
                if isinstance(roi, (tuple, list)) and len(roi) == 4:
                    x, y, w, h = roi
                    result[name] = {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
                elif isinstance(roi, dict):
                    if 'x' in roi and 'y' in roi:
                        result[name] = {
                            'x': int(roi['x']),
                            'y': int(roi['y']),
                            'width': int(roi.get('width', roi.get('w', 0))),
                            'height': int(roi.get('height', roi.get('h', 0))),
                        }
                    else:
                        result[name] = dict(roi)
                else:
                    result[name] = {'x': 0, 'y': 0, 'width': 0, 'height': 0}
        return result

    @staticmethod
    def _parse_roi_entry(name: str, roi_data) -> Optional[RoiEntry]:
        """解析单条 ROI 数据为 {x, y, width, height} 字典"""
        if isinstance(roi_data, dict) and 'x' in roi_data and 'y' in roi_data:
            return {
                'x': int(roi_data['x']),
                'y': int(roi_data['y']),
                'width': int(roi_data.get('width', roi_data.get('w', 0))),
                'height': int(roi_data.get('height', roi_data.get('h', 0))),
            }
        elif isinstance(roi_data, (list, tuple)) and len(roi_data) == 4:
            x, y, w, h = (int(v) for v in roi_data)
            return {'x': x, 'y': y, 'width': w, 'height': h}
        logger.warning(f"无法解析 ROI: {name}={roi_data}")
        return None
