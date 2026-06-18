"""
SrlogSerializer：.srlog 交换格式的序列化/反序列化

.srlog 是 ZIP 压缩格式，包含烘焙温度数据（results.json）、
会话元信息（metadata.json）和视频帧图像（frames/*.jpg）。

这是 SantokrOCR 的实时采集会话交换格式，用于保存和分享
完整的摄像头采集结果（含帧图像）。

ZIP 内部结构：
  metadata.json      — 会话元信息（version, created, rois, events, ...）
  results.json       — 温度识别结果列表
  frames/
    frame_000001.jpg — 视频帧图像
    frame_000002.jpg
    ...
"""

import json
import logging
import os
import tempfile
import time
import zipfile
from typing import List, Optional

logger = logging.getLogger(__name__)

_CURRENT_VERSION = 1


def _default_created() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S')


class SrlogSerializer:
    """.srlog 交换格式序列化器"""

    # ── 读取 ──

    @classmethod
    def read(
        cls,
        path: str,
        extract_to: Optional[str] = None,
    ) -> dict:
        """读取 .srlog 文件

        返回 dict 包含 metadata、results 和 frames_dir（提取到的帧目录路径）。

        Args:
            path: .srlog 文件路径
            extract_to: 帧提取目标目录（默认使用临时目录，
                        调用方负责清理；传入路径则由调用方管理生命周期）

        Returns:
            {
                'metadata': {...},       # metadata.json 内容
                'results': [...],        # results.json 内容
                'frames_dir': str|None,  # 帧图像所在目录，无帧时为 None
                '_extract_to': str,      # 实际提取根目录（供调用方清理用）
            }

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: ZIP 结构异常或 JSON 解析失败
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f".srlog 文件不存在: {path}")

        try:
            with zipfile.ZipFile(path, 'r') as zf:
                missing = [n for n in ('metadata.json', 'results.json') if n not in zf.namelist()]
                if missing:
                    raise ValueError(f".srlog 缺少必需条目: {missing}")

                metadata = json.loads(zf.read('metadata.json'))
                results = json.loads(zf.read('results.json'))

                if not isinstance(metadata, dict):
                    raise ValueError(f"metadata.json 格式异常: 期望 dict，实际为 {type(metadata).__name__}")
                if not isinstance(results, list):
                    raise ValueError(f"results.json 格式异常: 期望 list，实际为 {type(results).__name__}")

                has_frames = any(name.startswith('frames/') for name in zf.namelist())
                extract_to = extract_to or tempfile.mkdtemp(prefix='srlog_')
                zf.extractall(extract_to)
                frames_dir = os.path.join(extract_to, 'frames') if has_frames else None

        except zipfile.BadZipFile as e:
            raise ValueError(f".srlog 文件损坏: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f".srlog 内 JSON 解析失败: {e}") from e

        return {
            'metadata': metadata,
            'results': results,
            'frames_dir': frames_dir,
            '_extract_to': extract_to,
        }

    # ── 写入 ──

    @staticmethod
    def write(
        output_path: str,
        results: List[dict],
        rois: Optional[dict] = None,
        interval: float = 0.25,
        rotate_angle: float = 5.0,
        source: str = "",
        events: Optional[List[dict]] = None,
        frames_dir: Optional[str] = None,
        progress_callback: Optional[callable] = None,
    ) -> None:
        """将会话数据写入 .srlog (ZIP) 文件

        Args:
            output_path: 输出文件路径
            results: 温度识别结果列表
            rois: ROI 配置 dict（可选）
            interval: 采集间隔（秒）
            rotate_angle: 旋转角度
            source: 来源描述
            events: 事件列表（可选）
            frames_dir: 帧图像目录路径（可选，目录内的 *.jpg 会被打包）
            progress_callback: 进度回调函数 callback(current, total)，
                在帧打包循环中调用，可用于后台线程进度更新
        """
        events = events or []

        metadata = {
            "version": _CURRENT_VERSION,
            "created": _default_created(),
            "source": source,
            "interval": interval,
            "rotate_angle": rotate_angle,
            "rois": rois or {},
            "frame_count": len(results),
            "duration": results[-1]['timestamp'] if results else 0.0,
            "events": events,
        }

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('metadata.json', json.dumps(metadata, indent=2, ensure_ascii=False))
            zf.writestr('results.json', json.dumps(results, indent=2, ensure_ascii=False))

            if frames_dir and os.path.isdir(frames_dir):
                frame_files = sorted(os.listdir(frames_dir))
                total_frames = len([f for f in frame_files if f.endswith('.jpg')])
                last_pct = -1
                frame_idx = 0
                for fname in frame_files:
                    if not fname.endswith('.jpg'):
                        continue
                    fpath = os.path.join(frames_dir, fname)
                    zf.write(fpath, f"frames/{fname}")
                    frame_idx += 1
                    if progress_callback:
                        pct = int(frame_idx / max(total_frames, 1) * 100)
                        if pct != last_pct:
                            last_pct = pct
                            progress_callback(frame_idx, total_frames)

        logger.info(f".srlog 已写入: {output_path} ({len(results)} 条记录, "
                    f"{len(events)} 个事件)")
