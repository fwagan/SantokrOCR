"""JsonBeanRepository：咖啡豆信息存储"""

import os
import json
from typing import List, Optional

import logging

from data.json._utils import json_lock, atomic_write, load_json

logger = logging.getLogger(__name__)


def _default_bean_path() -> str:
    """返回 beans.json 默认路径"""
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
    return os.path.join(app_data, 'SantokrOCR', 'BeanInfo', 'beans.json')


class JsonBeanRepository:
    """JSON 文件存储的咖啡豆仓库"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_bean_path()

    def list_all(self) -> List[dict]:
        return self._load_all()

    def save_all(self, beans: List[dict]) -> None:
        with json_lock:
            self._save_all_impl(beans)

    def get_by_name(self, name: str) -> Optional[dict]:
        for bean in self._load_all():
            if bean.get('name') == name:
                return bean
        return None

    def add(self, bean: dict) -> None:
        with json_lock:
            beans = self._load_all()
            beans.append(bean)
            self._save_all_impl(beans)

    def update(self, name: str, bean: dict) -> bool:
        with json_lock:
            beans = self._load_all()
            for i, b in enumerate(beans):
                if b.get('name') == name:
                    beans[i] = bean
                    self._save_all_impl(beans)
                    return True
        return False

    def delete(self, name: str) -> bool:
        with json_lock:
            beans = self._load_all()
            new_beans = [b for b in beans if b.get('name') != name]
            if len(new_beans) == len(beans):
                return False
            self._save_all_impl(new_beans)
        return True

    # ── internal（调用者需持有 json_lock） ──

    def _load_all(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        data = load_json(self.path)
        if isinstance(data, list):
            return data
        logger.warning(f"beans.json 格式异常: {type(data)}")
        return []

    def _save_all_impl(self, beans: List[dict]) -> None:
        to_save = [b for b in beans if b.get('name', '').strip()]
        atomic_write(self.path, to_save)
