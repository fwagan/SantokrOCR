"""
Repository 接口定义（Protocol）

每个实体对应一个独立的 Repository 接口，支持多后端实现（JSON / SQLite）。
"""

from typing import List, Optional, Protocol

from .types import (
    BeanRecord,
    EventRecord,
    ResultRecord,
    RoiConfig,
    RoastSession,
    VideoInfo,
)


class ResultRepository(Protocol):
    """单帧温度识别结果"""

    def save(self, video_hash: str, results: List[ResultRecord]) -> None:
        """保存识别结果（覆盖写入）"""

    def load(self, video_hash: str) -> Optional[List[ResultRecord]]:
        """加载识别结果，不存在返回 None"""

    def delete(self, video_hash: str) -> None:
        """删除指定视频的缓存结果"""

    def exists(self, video_hash: str) -> bool:
        """检查是否有缓存结果"""


class EventRepository(Protocol):
    """烘焙事件"""

    def save(self, video_hash: str, events: List[EventRecord]) -> None:
        """保存事件列表（覆盖写入）"""

    def load(self, video_hash: str) -> Optional[List[EventRecord]]:
        """加载事件列表，不存在返回 None"""

    def delete(self, video_hash: str) -> None:
        """删除指定视频的事件"""

    def exists(self, video_hash: str) -> bool:
        """检查是否有缓存事件"""


class BeanRepository(Protocol):
    """咖啡豆档案"""

    def list_all(self) -> List[BeanRecord]:
        """获取全部咖啡豆"""

    def save_all(self, beans: List[BeanRecord]) -> None:
        """覆盖保存全部咖啡豆"""

    def get_by_name(self, name: str) -> Optional[BeanRecord]:
        """按名称查找咖啡豆"""

    def add(self, bean: BeanRecord) -> None:
        """添加新咖啡豆"""

    def update(self, name: str, bean: BeanRecord) -> bool:
        """更新咖啡豆，返回是否找到并更新"""

    def delete(self, name: str) -> bool:
        """删除咖啡豆，返回是否找到并删除"""


class RoiRepository(Protocol):
    """ROI 配置"""

    def save(self, video_hash: str, config: RoiConfig) -> None:
        """保存 ROI 配置（覆盖写入）"""

    def load(self, video_hash: str) -> Optional[RoiConfig]:
        """加载 ROI 配置，不存在返回 None"""

    def delete(self, video_hash: str) -> None:
        """删除指定视频的 ROI 配置"""


class VideoInfoRepository(Protocol):
    """视频元信息"""

    def save(self, info: VideoInfo) -> None:
        """保存视频信息"""

    def load(self, video_hash: str) -> Optional[VideoInfo]:
        """加载视频信息，不存在返回 None"""

    def delete(self, video_hash: str) -> None:
        """删除指定视频的缓存信息"""

    def list_all(self) -> List[VideoInfo]:
        """列出所有缓存的视频信息"""


class SessionRepository(Protocol):
    """烘焙会话（一次烘焙的完整抽象）

    内部存储层，对应未来 roast_sessions 表。
    阶段 1-2 的 JSON 后端为 placeholder（仅定义接口，不持久化数据），
    完整 CRUD 实现在阶段 3（SQLite 后端）。
    """

    def save(self, session_id: str, session: RoastSession) -> None:
        """保存烘焙会话"""

    def load(self, session_id: str) -> Optional[RoastSession]:
        """加载烘焙会话，不存在返回 None"""

    def delete(self, session_id: str) -> None:
        """删除烘焙会话"""

    def list_all(self) -> List[RoastSession]:
        """列出所有烘焙会话"""
