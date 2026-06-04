"""
Repository 接口定义（Protocol）

每个实体对应一个独立的 Repository 接口，支持多后端实现（JSON / SQLite）。
"""

from typing import List, Optional, Protocol

from .types import (
    BeanRecord,
    EventRecord,
    ResultRecord,
    RoastSession,
)


class ResultRepository(Protocol):
    """帧温度识别结果"""

    def save(self, session_id: str, results: List[ResultRecord]) -> None:
        """保存识别结果（覆盖写入）"""

    def load(self, session_id: str) -> Optional[List[ResultRecord]]:
        """加载识别结果，不存在返回 None"""

    def delete(self, session_id: str) -> None:
        """删除指定会话的缓存结果"""

    def exists(self, session_id: str) -> bool:
        """检查是否有缓存结果"""


class EventRepository(Protocol):
    """烘焙事件"""

    def save(self, session_id: str, events: List[EventRecord]) -> None:
        """保存事件列表（覆盖写入）"""

    def load(self, session_id: str) -> Optional[List[EventRecord]]:
        """加载事件列表，不存在返回 None"""

    def delete(self, session_id: str) -> None:
        """删除指定会话的事件"""

    def exists(self, session_id: str) -> bool:
        """检查是否有缓存事件"""


class BeanRepository(Protocol):
    """咖啡豆档案"""

    def list_all(self) -> List[BeanRecord]:
        """获取全部咖啡豆（不含已删除）"""

    def get_by_name(self, name: str) -> Optional[BeanRecord]:
        """按名称查找咖啡豆"""

    def add(self, bean: BeanRecord) -> None:
        """添加新咖啡豆"""

    def update(self, name: str, bean: BeanRecord) -> bool:
        """更新咖啡豆，返回是否找到并更新"""

    def delete(self, name: str) -> bool:
        """删除咖啡豆，返回是否找到并删除"""


class SessionRepository(Protocol):
    """烘焙会话

    SQLite 持久化层，对应 roast_session 表。
    """

    def save(self, session_id: str, session: RoastSession) -> None:
        """保存烘焙会话"""

    def load(self, session_id: str) -> Optional[RoastSession]:
        """加载烘焙会话，不存在返回 None"""

    def delete(self, session_id: str) -> None:
        """删除烘焙会话"""

    def list_all(self) -> List[RoastSession]:
        """列出所有烘焙会话"""
