"""
数据模型定义（TypedDict）

当前阶段使用 TypedDict 作为数据契约，后续可升级为 Pydantic BaseModel。
"""

from typing import Dict, List, Optional, TypedDict
from typing_extensions import NotRequired

# ============================================================
# Event 类型常量
# ============================================================


class EventType:
    CHARGE = "入豆"
    TURNAROUND = "回温"
    FC_START = "一爆开始"
    FC_END = "一爆结束"
    SC_START = "二爆开始"
    ROAST_END = "烘焙结束"
    HEATER_ADJUST = "调整火力"
    FAN_ADJUST = "调整风门"


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
    quality: str                       # 置信度标签 e.g. "high"
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
# ROI 配置
# ============================================================


class RoiEntry(TypedDict):
    """单个 ROI 区域定义"""
    x: int
    y: int
    width: int
    height: int


class RoiConfig(TypedDict):
    """完整 ROI 配置（含可选的旋转角度和起始帧）"""
    rois: Dict[str, RoiEntry]
    rotation_angle: NotRequired[float]
    start_frame: NotRequired[int]


# ============================================================
# 视频元信息
# ============================================================


class VideoInfo(TypedDict, total=False):
    """已处理视频的缓存元信息"""
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
    name: str
    variety: str
    process: str                       # 处理法
    origin: str                        # 产地
    altitude: str                      # 海拔
    density: str                       # 密度(g/L)
    moisture: str                      # 含水率(%)
    season: str                        # 产季
    outOfStock: bool                   # 停用标记


# ============================================================
# 烘焙信息（嵌入在 .slog 和 roast_sessions 中）
# ============================================================


class RoastInfo(TypedDict, total=False):
    """烘焙批次信息"""
    bean_name: str
    roast_date: str                    # "YYYY-MM-DD"
    roast_time: str                    # "HH:MM"
    roast_no: str                      # 第 N 锅
    roast_total: str                   # 共 N 锅
    variety: str                       # 豆种
    process: str                       # 处理法
    origin: str                        # 产地
    altitude: str                      # 海拔
    season: str                        # 产季
    density: str                       # 密度(g/L)
    moisture: str                      # 含水率(%)
    green_weight: str                  # 生豆重量
    roasted_weight: str                # 熟豆重量
    weight_loss: str                   # 失重率（自动计算）
    notes: str                         # 备注


# ============================================================
# 烘焙会话（一次烘焙的完整抽象）
# ============================================================


class RoastSession(TypedDict, total=False):
    """完整烘焙会话（对应 .slog 顶层结构）"""
    results: List[ResultRecord]
    events: List[EventRecord]
    heater_initial: float
    fan_initial: float
    roast_info: RoastInfo
