"""
数据模型定义（TypedDict）

当前阶段使用 TypedDict 作为数据契约，后续可升级为 Pydantic BaseModel。
"""

from enum import StrEnum
from typing import Optional, TypedDict

from typing_extensions import NotRequired

# ============================================================
# Event 类型常量
# ============================================================


class EventType(StrEnum):
    CHARGE = "入豆"
    TURNAROUND = "回温"
    FC_START = "一爆开始"
    FC_END = "一爆结束"
    SC_START = "二爆开始"
    SC_END = "二爆结束"
    ROAST_END = "烘焙结束"
    HEATER_ADJUST = "调整火力"
    FAN_ADJUST = "调整风门"


# 事件类型完整列表（UI 下拉框展示顺序，后端单一来源）
# 由 StrEnum 派生：不可变、天然有序；成员本身是 str，可直接用于比较/落库/序列化
EVENT_TYPES = [e.value for e in EventType]


# ============================================================
# 单帧结果
# ============================================================


class ResultRecord(TypedDict, total=False):
    """单帧温度识别结果"""
    frame: int
    timestamp: float
    original_timestamp: float          # 插值前原始时间戳（仅 .slog 导出时写入）
    time_str: str                      # "MM:SS:mmm"
    timer: Optional[str]               # 备用计时器（当前未使用）
    temp1_full: str                    # 完整读取值 e.g. "184.2" / "????"
    temp1_normal: str                  # 正常位三位数字 e.g. "184"
    temp1_faulty_digit: int            # 故障位数字，-1=无法识别，-2=0/8 歧义
    temp2: str                         # 排气温度 e.g. "202.6" / "????"
    abnormal_category: Optional[str]   # 异常类别 e.g. "temperature_diff"


# ============================================================
# 事件
# ============================================================


class EventRecord(TypedDict, total=False):
    """烘焙事件记录"""
    type: str                          # EventType.*
    frame: int
    time: float                        # 秒
    value: Optional[float]             # 火力/风门百分比；事件标记为 None


# ============================================================
# ROI 配置（JSON 缓存层专用，不入 SQLite）
# ============================================================


class RoiEntry(TypedDict):
    """单个 ROI 区域定义"""
    x: int
    y: int
    width: int
    height: int


class RoiConfig(TypedDict):
    """完整 ROI 配置（JSON 缓存层专用）

    仅用于 AppData JSON cache，不写入 SQLite。
    """
    rois: dict[str, RoiEntry]
    rotation_angle: NotRequired[float]
    start_frame: NotRequired[int]


# ============================================================
# 视频元信息（JSON 缓存层专用，不入 SQLite）
# ============================================================


class VideoInfo(TypedDict, total=False):
    """已处理视频的缓存元信息

    仅用于 AppData JSON cache，不写入 SQLite。
    """
    video_path: str
    video_hash: str
    file_size: int
    modified_time: float
    created_time: float
    cache_time: float
    cache_date: str                     # ISO datetime


# ============================================================
# 咖啡豆信息
# ============================================================


class BeanRecord(TypedDict, total=False):
    """咖啡豆档案记录"""
    id: int                            # 自增主键（新记录缺失，已存在的记录有值）
    name: str
    variety: str
    process: str                       # 处理法
    origin: str                        # 产地
    altitude: str                      # 海拔
    density: Optional[float]           # 密度(g/L)
    moisture: Optional[float]          # 含水率(%)
    season: str                        # 产季
    outOfStock: bool                   # 停用标记



# ============================================================
# 烘焙会话
# ============================================================


class RoastSession(TypedDict, total=False):
    """完整烘焙会话"""
    session_id: str
    is_raw_data: bool
    is_favorite: bool
    bean_id: int                       # FK → bean.id
    heater_initial: float
    fan_initial: float
    density_override: Optional[float]  # 覆盖 bean.density
    moisture_override: Optional[float] # 覆盖 bean.moisture
    roast_date: str
    roast_time: str
    roast_no: str
    roast_total: str
    green_weight: Optional[float]
    roasted_weight: Optional[float]
    notes: str
    # .slog 交换格式（组装时从对应表读取）
    results: list[ResultRecord]
    events: list[EventRecord]
