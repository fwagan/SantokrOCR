"""
在指定帧标记事件的对话框

不依赖帧查看器，用于非视频源会话（无视频 / 无帧截图）在数据表格上
右键标记事件。事件类型、数值规则与 FrameViewer 的事件标记一致。
"""

import tkinter as tk
from tkinter import messagebox, ttk

from data.types import EVENT_TYPES
from utils.screen_utils import center_window


class MarkEventDialog(tk.Toplevel):
    """在指定帧标记事件的对话框

    确认后通过 result 属性返回 (new_event, overwrite_event)：
        - new_event: 新事件 dict（含 type/frame/time/value）
        - overwrite_event: 被覆盖的旧事件（无则为 None）
    取消时 result 为 None。
    """

    def __init__(self, parent, frame_num, timestamp, events,
                 heater_initial=60.0, fan_initial=50.0):
        super().__init__(parent)
        try:
            self.frame_num = frame_num
            self.timestamp = timestamp
            # 保持与调用方 events 相同的列表引用（可能为空列表）
            self.events = events if events is not None else []
            self.heater_initial = heater_initial
            self.fan_initial = fan_initial
            self.result = None  # (new_event, overwrite_event)

            self.title("标记事件")
            self.resizable(False, False)
            self.transient(parent)
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.bind("<Escape>", lambda e: self.destroy())

            body = ttk.Frame(self, padding=14)
            body.pack(fill="both", expand=True)

            # 目标帧信息
            ttk.Label(body, text=f"目标帧: {frame_num}   (时间 {timestamp:.1f} 秒)").pack(anchor="w")

            # 事件类型
            type_row = ttk.Frame(body)
            type_row.pack(fill="x", pady=(12, 0))
            ttk.Label(type_row, text="事件类型:").pack(side="left")
            self.event_type_var = tk.StringVar(value=self._default_event_type())
            self.event_combo = ttk.Combobox(type_row, textvariable=self.event_type_var,
                                            values=EVENT_TYPES, state="readonly", width=16)
            self.event_combo.pack(side="left", padx=(8, 0))
            self.event_combo.bind("<<ComboboxSelected>>", self._on_type_changed)

            # 数值（仅调整火力/调整风门可用）
            value_row = ttk.Frame(body)
            value_row.pack(fill="x", pady=(8, 0))
            ttk.Label(value_row, text="数值(%):").pack(side="left")
            self.value_var = tk.StringVar(value="")
            self.value_entry = ttk.Entry(value_row, textvariable=self.value_var, width=10, state="disabled")
            self.value_entry.pack(side="left", padx=(8, 0))

            # 按钮
            btn_row = ttk.Frame(body)
            btn_row.pack(fill="x", pady=(14, 0))
            ttk.Button(btn_row, text="确认", command=self._confirm).pack(side="right", padx=(6, 0))
            ttk.Button(btn_row, text="取消", command=self.destroy).pack(side="right")

            self._on_type_changed()
            center_window(self, 340, 200)
            self.focus_set()
            self.grab_set()
        except Exception:
            # 构造中途失败：销毁已创建的原生窗口再抛出。调用方无法访问到未完成的
            # 实例（赋值不成立），只能由本类自毁，避免残留孤儿"标记事件"空窗。
            self.destroy()
            raise

    def _default_event_type(self):
        """默认事件类型：优先尚未标记的入豆/回温，否则调整火力"""
        if not any(e.get('type') == '入豆' for e in self.events):
            return "入豆"
        if not any(e.get('type') == '回温' for e in self.events):
            return "回温"
        return "调整火力"

    def _on_type_changed(self, event=None):
        """事件类型变化时控制数值输入框可用状态"""
        event_type = self.event_type_var.get()
        if event_type in ("调整火力", "调整风门"):
            self.value_entry.config(state="normal")
            # 查找该类型最近一次标记的值作为默认值
            last_val = None
            for ev in reversed(self.events):
                if ev.get('type') == event_type and ev.get('value') is not None:
                    last_val = ev['value']
                    break
            if last_val is not None:
                self.value_var.set(str(int(last_val)))
            else:
                default = self.heater_initial if event_type == "调整火力" else self.fan_initial
                self.value_var.set(str(int(default)))
        else:
            self.value_entry.config(state="disabled")
            self.value_var.set("")

    def _confirm(self):
        """校验输入并返回事件数据（逻辑与 FrameViewer.mark_event 一致）"""
        event_type = self.event_type_var.get()
        value = None
        if event_type in ("调整火力", "调整风门"):
            try:
                value = float(self.value_var.get())
                if value < 0 or value > 200:
                    messagebox.showwarning("数值错误", "火力/风门值必须在0-200之间", parent=self)
                    return
            except ValueError:
                messagebox.showwarning("数值错误", "请输入有效的数值（0-200）", parent=self)
                return

        # 调整火力/调整风门可多次记录；其他事件只记录一次（重复则确认覆盖）
        overwrite_event = None
        if event_type not in ("调整火力", "调整风门"):
            for ev in self.events:
                if ev.get('type') == event_type:
                    if not messagebox.askyesno("重复事件",
                                               f"已存在'{event_type}'事件，是否覆盖？",
                                               parent=self):
                        return
                    overwrite_event = ev
                    break

        event_data = {
            'type': event_type,
            'frame': self.frame_num,
            'time': round(self.timestamp, 1),
            'value': value,
        }
        self.result = (event_data, overwrite_event)
        self.destroy()
