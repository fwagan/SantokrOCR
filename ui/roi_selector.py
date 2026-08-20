"""
ROI选择器 - 基于tkinter的矩形框选对话框

在单个窗口中依次选择多个ROI区域，使用Canvas显示视频帧，
使用ttk控件展示ROI状态和操作提示，图片上不叠加任何文字。
"""

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from data.types import RoiEntry
from utils.screen_utils import center_window

# ROI名称 → 中文标签映射
ROI_LABELS = {
    'temp1_normal': '豆温正常位',
    'temp1_faulty': '豆温故障位',
    'temp2_normal_3digits': '风温前三位',
    'temp2_normal_lastdigit': '风温最后位',
}

# ROI名称 → BGR颜色 (与video_extractor.get_roi_color保持一致)
ROI_COLORS_BGR = {
    'temp1_normal': (255, 0, 0),    # 蓝色
    'temp1_faulty': (0, 0, 255),    # 红色
    'temp2_normal_3digits': (255, 255, 0),   # 黄色
    'temp2_normal_lastdigit': (255, 0, 255), # 紫色
}

# BGR → tkinter hex
_ROI_COLORS_TK = {}


def _bgr_to_tk(bgr):
    """将OpenCV BGR颜色转换为tkinter可用的十六进制颜色字符串"""
    b, g, r = bgr
    return f'#{r:02x}{g:02x}{b:02x}'


# 预计算tkinter颜色
for _name, _bgr in ROI_COLORS_BGR.items():
    _ROI_COLORS_TK[_name] = _bgr_to_tk(_bgr)


class RoiSelector(tk.Toplevel):
    """ROI区域选择器（模态对话框）"""

    # 状态常量
    STATUS_PENDING = 0
    STATUS_SELECTING = 1
    STATUS_DONE = 2

    # 状态样式
    _STATUS_SYMBOLS = {STATUS_PENDING: '○', STATUS_SELECTING: '▶', STATUS_DONE: '✓'}
    _STATUS_COLORS = {
        STATUS_PENDING: '#888888',
        STATUS_SELECTING: '#FFD700',  # 金色
        STATUS_DONE: '#00CC00',       # 绿色
    }

    def __init__(self, parent, video_path=None, frame=None):
        super().__init__(parent)
        self.parent = parent

        # 构建ROI顺序
        self.roi_names = [
            'temp1_normal',
            'temp1_faulty',
            'temp2_normal_3digits',
            'temp2_normal_lastdigit',
        ]

        # 读取视频帧：优先使用传入的frame，否则从video_path读取
        self.frame_rgb, self.frame_size = self._read_frame(video_path, frame)
        self.img_h, self.img_w = self.frame_size

        # 状态
        self.results = {}               # {name: RoiEntry}
        self.current_idx = 0            # 当前正在选择的ROI索引
        self.mouse_x = -1               # 鼠标在图像坐标系中的X
        self.mouse_y = -1               # 鼠标在图像坐标系中的Y
        self.dragging = False           # 是否正在拖拽
        self.rect_start = None          # 拖拽起点 (图像坐标)
        self.rect_end = None            # 拖拽终点 (图像坐标)
        self._result = None             # 最终返回结果
        self._closed = False            # 是否已关闭
        self._auto_close_timer = None   # 全部ROI完成后的自动关闭计时器

        # Canvas显示参数（在布局确定后计算）
        self._scale = 1.0               # 图像→Canvas缩放比
        self._zoom = 1.0               # 用户缩放倍率（1.0=适应画布）
        self._offset_x = 0              # 图像在Canvas中的X偏移
        self._offset_y = 0              # 图像在Canvas中的Y偏移
        self._pan_x = 0                 # 平移X偏移
        self._pan_y = 0                 # 平移Y偏移
        self._canvas_img_id = None      # Canvas上图像item的ID
        self._overlay_items = []        # 叠加层item ID列表

        # 窗口设置
        self.title('ROI区域选择')
        self.geometry("1200x1500")
        self.resizable(False, False)
        center_window(self, 1200, 1500)
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)

        # 创建UI
        self._create_ui()

        # 绑定全局键盘事件
        self.bind('<Key-Escape>', lambda e: self._on_cancel())
        self.bind('<Key-r>', lambda e: self._on_undo())
        self.bind('<Key-R>', lambda e: self._on_undo())

        # 模态
        self.transient(parent)
        self.grab_set()

        # 强制布局计算，确保 canvas 尺寸在 _redraw_canvas 时可用
        self.update_idletasks()

        # 初始绘制
        self._update_status_labels()
        self._update_hint()
        self._redraw_canvas()

        # 阻塞直到窗口关闭
        self.wait_window()

    # ──────── 窗口大小 ────────

    # （窗口大小固定为 1200×1500，在 __init__ 中设置）

    # ──────── 读取帧 ────────

    def _read_frame(self, video_path=None, frame=None):
        """读取视频帧，返回(RGB数组, (h,w))

        优先使用传入的 frame (numpy BGR数组)；若无则从 video_path 读取第10秒帧。
        """
        if frame is not None:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return frame_rgb, frame.shape[:2]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f'无法打开视频: {video_path}')
        fps = cap.get(cv2.CAP_PROP_FPS)
        target_frame = int(10 * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame_bgr = cap.read()
        cap.release()
        if not ret:
            raise ValueError('无法读取视频第10秒的帧')
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return frame_rgb, frame_bgr.shape[:2]

    # ──────── 创建UI ────────

    def _create_ui(self):
        """创建窗口UI布局"""
        # 主frame
        main = ttk.Frame(self, padding=8)
        main.pack(fill='both', expand=True)

        # ── 左侧：Canvas显示视频帧 ──
        canvas_frame = ttk.LabelFrame(main, text='视频帧', padding=4)
        canvas_frame.pack(side='left', fill='both', expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg='#333333',
                                highlightthickness=0,
                                cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)

        # Canvas鼠标事件
        self.canvas.bind('<Motion>', self._on_mouse_move)
        self.canvas.bind('<ButtonPress-1>', self._on_button_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_button_release)
        self.canvas.bind('<Button-3>', self._on_right_click)
        self.canvas.bind('<Configure>', self._on_canvas_resize)
        # 中键拖拽平移
        self.canvas.bind('<Button-2>', self._on_pan_start)
        self.canvas.bind('<B2-Motion>', self._on_pan_move)
        self.canvas.bind('<ButtonRelease-2>', self._on_pan_end)
        # 滚轮缩放
        self.canvas.bind('<MouseWheel>', self._on_mouse_wheel)

        # ── 右侧：状态面板 ──
        right_frame = ttk.Frame(main, width=200)
        right_frame.pack(side='right', fill='y', padx=(8, 0))
        right_frame.pack_propagate(False)

        # 标题
        ttk.Label(right_frame, text='选择状态',
                  font=('TkDefaultFont', 11, 'bold')).pack(anchor='w', pady=(0, 6))

        # ROI状态列表
        self._status_labels = {}  # {name: ttk.Frame}
        for name in self.roi_names:
            frame = ttk.Frame(right_frame)
            frame.pack(fill='x', pady=1)

            icon = ttk.Label(frame, text='○', font=('TkDefaultFont', 10))
            icon.pack(side='left', padx=(0, 4))

            label_text = ROI_LABELS.get(name, name)
            label = ttk.Label(frame, text=label_text, font=('TkDefaultFont', 10))
            label.pack(side='left')

            self._status_labels[name] = {'frame': frame, 'icon': icon, 'label': label}

        # 分隔线
        ttk.Separator(right_frame, orient='horizontal').pack(fill='x', pady=(10, 6))

        # 操作提示
        ttk.Label(right_frame, text='操作提示',
                  font=('TkDefaultFont', 9, 'bold')).pack(anchor='w')
        self._hint_label = ttk.Label(right_frame, text='',
                                     font=('TkDefaultFont', 9), wraplength=180)
        self._hint_label.pack(anchor='w', pady=(4, 0))

        # 快捷键提示
        ttk.Separator(right_frame, orient='horizontal').pack(fill='x', pady=(10, 6))
        shortcuts = ttk.Label(right_frame,
                              text='右键/ R : 撤销上一个\nESC : 取消全部',
                              font=('TkDefaultFont', 8), foreground='#666666')
        shortcuts.pack(anchor='w')

        # ── 底部：进度和按钮 ──
        bottom = ttk.Frame(main)
        bottom.pack(side='bottom', fill='x', pady=(8, 0))

        self._progress_var = tk.StringVar(value='进度: 0/{}'.format(len(self.roi_names)))
        ttk.Label(bottom, textvariable=self._progress_var).pack(side='left')

        ttk.Button(bottom, text='全部清除', command=self._on_clear_all).pack(side='right', padx=(4, 0))
        ttk.Button(bottom, text='完成(无)', command=self._on_finish).pack(side='right')

        # ── 右侧frame不收缩 ──
        right_frame.update_idletasks()

    # ──────── Canvas缩放计算 ────────

    def _update_display_params(self):
        """根据Canvas尺寸、缩放和偏移计算显示参数"""
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        # 计算适应画布的缩放比（保持宽高比，留出边距）
        margin = 4
        fit_scale = min((cw - 2 * margin) / self.img_w, (ch - 2 * margin) / self.img_h)
        self._scale = fit_scale * self._zoom

        # 居中偏移 + 平移偏移
        disp_w = self.img_w * self._scale
        disp_h = self.img_h * self._scale
        self._offset_x = (cw - disp_w) / 2 + self._pan_x
        self._offset_y = (ch - disp_h) / 2 + self._pan_y

    def _canvas_to_image(self, cx, cy):
        """Canvas坐标 → 图像像素坐标"""
        ix = (cx - self._offset_x) / self._scale
        iy = (cy - self._offset_y) / self._scale
        ix = max(0, min(ix, self.img_w - 1))
        iy = max(0, min(iy, self.img_h - 1))
        return int(ix), int(iy)

    def _image_to_canvas(self, ix, iy):
        """图像像素坐标 → Canvas坐标"""
        cx = ix * self._scale + self._offset_x
        cy = iy * self._scale + self._offset_y
        return cx, cy

    # ──────── Canvas绘制 ────────

    def _redraw_canvas(self, update_image=True):
        """重新绘制Canvas（图像 + 叠加层）"""
        if self._closed:
            return

        self._update_display_params()
        if self._scale <= 0:
            return

        # 更新图像
        if update_image:
            self._draw_base_image()

        # 清除旧叠加层
        self._clear_overlays()

        # 绘制已确认的ROI
        for name, roi in self.results.items():
            color = _ROI_COLORS_TK.get(name, '#00FF00')
            self._draw_roi_rect(roi['x'], roi['y'], roi['width'], roi['height'],
                                outline=color, fill_alpha=0.15)

        # 绘制当前选择的矩形
        if self.rect_start and self.rect_end:
            x1, y1 = self.rect_start
            x2, y2 = self.rect_end
            rx = min(x1, x2)
            ry = min(y1, y2)
            rw = abs(x2 - x1)
            rh = abs(y2 - y1)
            if rw > 0 and rh > 0:
                if self.dragging:
                    # 拖拽中：只有绿色边框
                    self._draw_roi_rect(rx, ry, rw, rh, outline='#00FF00', fill_alpha=0.0)
                else:
                    # 已选完待确认（实际已自动确认，但保留视觉反馈）
                    self._draw_roi_rect(rx, ry, rw, rh, outline='#00FF00', fill_alpha=0.08)

        # 绘制十字辅助线（仅非拖拽时）
        if not self.dragging and 0 <= self.mouse_x < self.img_w and 0 <= self.mouse_y < self.img_h:
            cx = self.mouse_x * self._scale + self._offset_x
            cy = self.mouse_y * self._scale + self._offset_y
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()

            # 纵向线
            vline = self.canvas.create_line(cx, 0, cx, ch,
                                           fill='#888888', width=1, tags='overlay')
            self._overlay_items.append(vline)
            # 横向线
            hline = self.canvas.create_line(0, cy, cw, cy,
                                           fill='#888888', width=1, tags='overlay')
            self._overlay_items.append(hline)

    def _draw_base_image(self):
        """在Canvas上绘制（缩放后的）视频帧"""
        # 计算显示尺寸
        disp_w = int(self.img_w * self._scale)
        disp_h = int(self.img_h * self._scale)

        # 缩放图像
        pil = Image.fromarray(self.frame_rgb)
        pil_resized = pil.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
        self._tk_image = ImageTk.PhotoImage(pil_resized)

        # 删除旧图像
        if self._canvas_img_id is not None:
            self.canvas.delete(self._canvas_img_id)

        self._canvas_img_id = self.canvas.create_image(
            self._offset_x, self._offset_y, anchor='nw', image=self._tk_image, tags='base'
        )
        # 将base图像置于底层
        self.canvas.tag_lower(self._canvas_img_id)

    def _clear_overlays(self):
        """清除叠加层"""
        for item in self._overlay_items:
            try:
                self.canvas.delete(item)
            except tk.TclError:
                pass
        self._overlay_items = []

    def _draw_roi_rect(self, rx, ry, rw, rh, outline='#00FF00', fill_alpha=0.15):
        """在Canvas上绘制ROI矩形（自动转换坐标系）"""
        x1 = rx * self._scale + self._offset_x
        y1 = ry * self._scale + self._offset_y
        x2 = (rx + rw) * self._scale + self._offset_x
        y2 = (ry + rh) * self._scale + self._offset_y

        # 半透明填充（tkinter不支持alpha hex，用stipple模拟）
        if fill_alpha > 0:
            rect_fill = self.canvas.create_rectangle(
                x1, y1, x2, y2, outline='', fill=outline,
                stipple='gray50', tags='overlay'
            )
            self._overlay_items.append(rect_fill)

        # 边框
        rect_outline = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline=outline, width=2, tags='overlay'
        )
        self._overlay_items.append(rect_outline)

    # ──────── 状态面板更新 ────────

    def _update_status_labels(self):
        """更新右侧ROI状态列表"""
        for i, name in enumerate(self.roi_names):
            if name in self.results:
                status = self.STATUS_DONE
            elif i == self.current_idx and not self._is_all_done():
                status = self.STATUS_SELECTING
            else:
                status = self.STATUS_PENDING

            info = self._status_labels[name]
            icon_text = self._STATUS_SYMBOLS[status]
            color = self._STATUS_COLORS[status]
            info['icon'].config(text=icon_text, foreground=color)
            info['label'].config(foreground=color)

    def _update_hint(self):
        """更新操作提示文字"""
        if self._is_all_done():
            text = '全部ROI已选择完成！点击"完成"或关闭窗口确认。'
        else:
            name = self.roi_names[self.current_idx]
            label = ROI_LABELS.get(name, name)
            if self.dragging:
                text = f'正在框选「{label}」区域...'
            elif self.rect_start and self.rect_end:
                text = f'已选择「{label}」区域'
            else:
                text = f'在左侧画面上拖拽框选「{label}」区域'
        self._hint_label.config(text=text)

    def _update_progress(self):
        """更新进度显示"""
        done = len(self.results)
        total = len(self.roi_names)
        self._progress_var.set(f'进度: {done}/{total}')
        # 更新完成按钮文本
        for child in self.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Button) and sub.cget('text').startswith('完成'):
                    if self._is_all_done():
                        sub.config(text='完成 ✓')
                    else:
                        sub.config(text=f'完成({done}/{total})')

    def _is_all_done(self):
        """是否全部ROI已选择"""
        return len(self.results) >= len(self.roi_names)

    def _update_all_ui(self):
        """更新所有UI元素"""
        self._update_status_labels()
        self._update_hint()
        self._update_progress()

    # ──────── 事件处理 ────────

    def _on_canvas_resize(self, event):
        """Canvas尺寸变化时重绘"""
        self._redraw_canvas()

    def _on_mouse_move(self, event):
        """鼠标移动：更新辅助线位置"""
        ix, iy = self._canvas_to_image(event.x, event.y)
        if ix != self.mouse_x or iy != self.mouse_y:
            self.mouse_x = ix
            self.mouse_y = iy
            self._redraw_canvas(update_image=False)

    def _on_button_press(self, event):
        """鼠标左键按下：开始拖拽"""
        if self._is_all_done():
            return

        ix, iy = self._canvas_to_image(event.x, event.y)
        self.dragging = True
        self.rect_start = (ix, iy)
        self.rect_end = (ix, iy)
        self.mouse_x, self.mouse_y = ix, iy
        self._update_hint()
        self._redraw_canvas(update_image=False)

    def _on_drag(self, event):
        """鼠标拖拽：更新矩形大小"""
        if not self.dragging:
            return

        ix, iy = self._canvas_to_image(event.x, event.y)
        self.rect_end = (ix, iy)
        self.mouse_x, self.mouse_y = ix, iy
        self._redraw_canvas(update_image=False)

    def _on_button_release(self, event):
        """鼠标左键松开：自动确认ROI"""
        if not self.dragging:
            return

        ix, iy = self._canvas_to_image(event.x, event.y)
        self.rect_end = (ix, iy)
        self.dragging = False
        self.mouse_x, self.mouse_y = ix, iy

        # 检查矩形是否有效（最小5像素）
        if self.rect_start and self.rect_end:
            x1, y1 = self.rect_start
            x2, y2 = self.rect_end
            rw = abs(x2 - x1)
            rh = abs(y2 - y1)
            if rw >= 5 and rh >= 5:
                # 自动确认
                name = self.roi_names[self.current_idx]
                rx = min(x1, x2)
                ry = min(y1, y2)
                self.results[name] = {'x': rx, 'y': ry, 'width': rw, 'height': rh}
                self.current_idx += 1
                self.rect_start = None
                self.rect_end = None

        # 如果全部选完
        if self._is_all_done():
            self._update_all_ui()
            self._redraw_canvas()
            # 自动关闭（延迟1.5秒）
            self._auto_close_timer = self.after(1500, self._on_finish)
            return

        self._update_all_ui()
        self._redraw_canvas()

    def _on_right_click(self, event):
        """右键点击：撤销上一个ROI"""
        self._on_undo()

    def _on_undo(self):
        """撤销上一个ROI"""
        if self.dragging:
            return
        if not self.results:
            return

        # 取消自动关闭计时器
        if self._auto_close_timer is not None:
            self.after_cancel(self._auto_close_timer)
            self._auto_close_timer = None

        # 找到最后一个已确认的ROI
        last_idx = -1
        last_name = None
        for i, name in enumerate(self.roi_names):
            if name in self.results:
                if i > last_idx:
                    last_idx = i
                    last_name = name

        if last_name is not None:
            del self.results[last_name]
            self.current_idx = last_idx
            self.rect_start = None
            self.rect_end = None
            self._update_all_ui()
            self._redraw_canvas()

    def _on_clear_all(self):
        """清除所有已选ROI"""
        if self._auto_close_timer is not None:
            self.after_cancel(self._auto_close_timer)
            self._auto_close_timer = None
        self.results.clear()
        self.current_idx = 0
        self.rect_start = None
        self.rect_end = None
        self.dragging = False
        self._update_all_ui()
        self._redraw_canvas()

    def _on_finish(self):
        """完成选择"""
        if self._is_all_done():
            self._result = self.results.copy()
        self._closed = True
        self.grab_release()
        self.destroy()

    # ──────── 平移与缩放 ────────

    def _on_pan_start(self, event):
        """中键拖拽平移开始"""
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self.canvas.config(cursor="fleur")

    def _on_pan_move(self, event):
        """中键拖拽平移移动"""
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        self._pan_x += dx
        self._pan_y += dy
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._redraw_canvas()

    def _on_pan_end(self, event):
        """中键拖拽平移结束"""
        self.canvas.config(cursor="crosshair")

    def _on_mouse_wheel(self, event):
        """滚轮缩放（以鼠标所在点为中心）"""
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        # 计算当前适应缩放
        margin = 4
        fit_scale = min((cw - 2 * margin) / self.img_w, (ch - 2 * margin) / self.img_h)

        old_zoom = self._zoom
        factor = 1.15 if event.delta > 0 else 0.85
        new_zoom = max(0.1, min(10.0, old_zoom * factor))

        # 光标在图像坐标系中的位置
        old_display_scale = fit_scale * old_zoom
        old_disp_w = self.img_w * old_display_scale
        old_disp_h = self.img_h * old_display_scale
        old_offset_x = (cw - old_disp_w) / 2 + self._pan_x
        old_offset_y = (ch - old_disp_h) / 2 + self._pan_y
        img_x = (event.x - old_offset_x) / old_display_scale
        img_y = (event.y - old_offset_y) / old_display_scale

        # 更新缩放和平移，使光标所在图像点保持不动
        self._zoom = new_zoom
        new_display_scale = fit_scale * new_zoom
        new_disp_w = self.img_w * new_display_scale
        new_disp_h = self.img_h * new_display_scale
        self._pan_x = event.x - img_x * new_display_scale - (cw - new_disp_w) / 2
        self._pan_y = event.y - img_y * new_display_scale - (ch - new_disp_h) / 2

        self._redraw_canvas()

    def _on_cancel(self):
        """取消全部"""
        self._result = None
        self._closed = True
        self.grab_release()
        self.destroy()

    # ──────── 获取结果 ────────

    def get_results(self) -> Optional[Dict[str, RoiEntry]]:
        """获取ROI选择结果，返回 {name: RoiEntry} 或 None"""
        return self._result
