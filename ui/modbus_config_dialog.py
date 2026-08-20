"""
温度读取器配置对话框

双通道布局：豆温通道 / 风温通道 两个 Tab，各自独立配置。
支持自动扫描 COM 口发现设备、手动配置、验证连接。
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import logging

from core.modbus_config import (
    load_modbus_config, save_modbus_config,
    scan_com_ports, probe_device, auto_detect_device,
)

logger = logging.getLogger(__name__)

_CHANNEL_KEYS = ['temp1', 'temp2']


def _safe_int(val: str, default: int) -> int:
    """安全整数转换，无效值返回默认值"""
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return default
_CHANNEL_LABELS = {'temp1': '豆温', 'temp2': '风温'}


class ChannelConfigFrame(ttk.Frame):
    """单个通道的配置表单"""

    def __init__(self, parent, channel_key: str, config: dict):
        super().__init__(parent)
        self.channel_key = channel_key
        self.channel_label = _CHANNEL_LABELS.get(channel_key, channel_key)
        self._config = config

        self._build_ui()

    def _build_ui(self):
        ch = self._config.get('channels', {}).get(self.channel_key, {})

        # ── 启停开关 ──
        enabled_frame = ttk.Frame(self)
        enabled_frame.pack(fill="x", pady=(0, 8))
        self._enabled_var = tk.BooleanVar(value=ch.get('enabled', False))
        self._enabled_cb = ttk.Checkbutton(enabled_frame, text="启用此通道",
                                            variable=self._enabled_var)
        self._enabled_cb.pack(side="left")

        # ── 自动发现 ──
        discover_frame = ttk.LabelFrame(self, text="自动发现设备", padding=8)
        discover_frame.pack(fill="x", pady=4)

        ttk.Label(discover_frame,
                  text="点击扫描按钮自动检测连接在 USB 的温度读取器。").pack(anchor="w")
        scan_btn = ttk.Button(discover_frame, text="🔍 扫描设备",
                              command=self._on_scan)
        scan_btn.pack(anchor="w", pady=(6, 0))
        self._scan_result_var = tk.StringVar(value="")
        ttk.Label(discover_frame, textvariable=self._scan_result_var,
                  foreground="#666666").pack(anchor="w", pady=(4, 0))

        # ── 手动配置 ──
        manual_frame = ttk.LabelFrame(self, text="手动配置", padding=8)
        manual_frame.pack(fill="x", pady=4)

        grid = ttk.Frame(manual_frame)
        grid.pack(fill="x")

        fields = [
            ("COM 口:", "port", 8),
            ("波特率:", "baudrate", 8),
            ("数据位:", "bytesize", 4),
            ("校验位:", "parity", 4),
            ("停止位:", "stopbits", 4),
            ("从站地址:", "slave_id", 6),
            ("寄存器地址:", "register", 6),
        ]
        self._entry_vars = {}
        for i, (label, key, width) in enumerate(fields):
            ttk.Label(grid, text=label).grid(row=i, column=0, sticky="e", padx=(0, 4), pady=2)
            var = tk.StringVar(value=str(ch.get(key, '')))
            self._entry_vars[key] = var
            entry = ttk.Entry(grid, textvariable=var, width=width)
            entry.grid(row=i, column=1, sticky="w", pady=2)

        # 验证按钮
        verify_btn = ttk.Button(manual_frame, text="✓ 验证连接",
                                command=self._on_verify)
        verify_btn.pack(anchor="w", pady=(6, 0))
        self._verify_result_var = tk.StringVar(value="")
        ttk.Label(manual_frame, textvariable=self._verify_result_var,
                  foreground="#666666").pack(anchor="w", pady=(4, 0))

    # ── 事件 ──

    def _on_scan(self):
        """自动扫描 COM 口"""
        self._scan_result_var.set("正在扫描...")
        self.update()

        def do_scan():
            try:
                device = auto_detect_device()
                if device:
                    self._entry_vars['port'].set(device.get('port', ''))
                    self._entry_vars['baudrate'].set(str(device.get('baudrate', 9600)))
                    self._entry_vars['bytesize'].set(str(device.get('bytesize', 8)))
                    self._entry_vars['parity'].set(device.get('parity', 'N'))
                    self._entry_vars['stopbits'].set(str(device.get('stopbits', 1)))
                    self._entry_vars['slave_id'].set(str(device.get('slave_id', 1)))
                    self._entry_vars['register'].set(str(device.get('register', 0)))
                    temp = probe_device(
                        device['port'],
                        slave_id=device['slave_id'],
                        register=device['register'],
                        baudrate=device['baudrate'],
                    )
                    self._scan_result_var.set(
                        f"✅ 发现设备: {device['port']}，温度: {temp:.1f}℃"
                        if temp is not None
                        else f"✅ 发现设备: {device['port']}（等待连接）"
                    )
                else:
                    self._scan_result_var.set("❌ 未发现温度读取器，请检查 USB 连接")
            except Exception as e:
                self._scan_result_var.set(f"❌ 扫描出错: {e}")

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_verify(self):
        """验证当前配置的连接"""
        config = self.read_config()
        if not config['port']:
            self._verify_result_var.set("请先填写 COM 口")
            return
        self._verify_result_var.set("正在验证...")
        self.update()

        def do_verify():
            temp = probe_device(
                port=config['port'],
                slave_id=config['slave_id'],
                register=config['register'],
                baudrate=config['baudrate'],
            )
            if temp is not None:
                self._verify_result_var.set(f"✅ 连接成功，当前温度: {temp:.1f}℃")
            else:
                self._verify_result_var.set("❌ 连接失败，请检查端口、地址和波特率")

        threading.Thread(target=do_verify, daemon=True).start()

    def read_config(self) -> dict:
        """读取表单数据"""
        return {
            'enabled': self._enabled_var.get(),
            'label': self.channel_label,
            'port': self._entry_vars['port'].get().strip(),
            'baudrate': _safe_int(self._entry_vars['baudrate'].get(), 9600),
            'bytesize': _safe_int(self._entry_vars['bytesize'].get(), 8),
            'parity': self._entry_vars['parity'].get().strip().upper() or 'N',
            'stopbits': _safe_int(self._entry_vars['stopbits'].get(), 1),
            'slave_id': _safe_int(self._entry_vars['slave_id'].get(), 1),
            'register': _safe_int(self._entry_vars['register'].get(), 0),
            'data_format': 'int16_x10',
        }


class ModbusConfigDialog(tk.Toplevel):
    """温度读取器配置对话框（双通道）"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("温度读取器配置")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # 加载现有配置
        self._config = load_modbus_config()

        # ── 通道 Tab ──
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=12, pady=(12, 4))

        self._channel_frames = {}
        for key in _CHANNEL_KEYS:
            frame = ChannelConfigFrame(self._notebook, key, self._config)
            label = _CHANNEL_LABELS[key]
            self._notebook.add(frame, text=label)
            self._channel_frames[key] = frame

        # ── 底部按钮 ──
        btn_frame = ttk.Frame(self, padding=12)
        btn_frame.pack(fill="x")

        cancel_btn = ttk.Button(btn_frame, text="取消", command=self.destroy)
        cancel_btn.pack(side="right", padx=(4, 0))

        save_btn = ttk.Button(btn_frame, text="保存配置", command=self._on_save)
        save_btn.pack(side="right", padx=4)

        # 窗口位置
        self.update_idletasks()
        pw = parent.winfo_width() if parent else 600
        ph = parent.winfo_height() if parent else 400
        sw = self.winfo_width()
        sh = self.winfo_height()
        x = parent.winfo_rootx() + (pw - sw) // 2
        y = parent.winfo_rooty() + (ph - sh) // 2
        self.geometry(f"+{x}+{y}")

    def _on_save(self):
        """保存配置到文件"""
        if 'channels' not in self._config:
            self._config['channels'] = {}

        for key, frame in self._channel_frames.items():
            ch_config = frame.read_config()
            self._config['channels'][key] = ch_config

        if save_modbus_config(self._config):
            messagebox.showinfo("成功", "温度读取器配置已保存", parent=self)
            self.destroy()
        else:
            messagebox.showerror("错误", "保存配置失败", parent=self)
