"""
进度显示面板

显示处理进度、估计剩余时间等信息。
"""

import tkinter as tk
from tkinter import ttk
import time


class ProgressPanel(ttk.LabelFrame):
    """进度显示面板"""

    def __init__(self, parent, title="处理进度"):
        super().__init__(parent, text=title, padding=10)

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self, variable=self.progress_var,
                                           maximum=100, length=300)
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # 进度文本
        progress_text_frame = ttk.Frame(self)
        progress_text_frame.pack(fill="x", pady=5)

        ttk.Label(progress_text_frame, text="进度:").pack(side="left", padx=(0, 5))
        self.progress_label = ttk.Label(progress_text_frame, text="0% (0/0)")
        self.progress_label.pack(side="left", padx=5)

        # 时间信息
        time_frame = ttk.Frame(self)
        time_frame.pack(fill="x", pady=5)

        ttk.Label(time_frame, text="已用时间:").pack(side="left", padx=(0, 5))
        self.elapsed_label = ttk.Label(time_frame, text="00:00:00")
        self.elapsed_label.pack(side="left", padx=5)

        ttk.Label(time_frame, text="剩余时间:").pack(side="left", padx=(20, 5))
        self.remaining_label = ttk.Label(time_frame, text="00:00:00")
        self.remaining_label.pack(side="left", padx=5)

        # 详细信息
        detail_frame = ttk.Frame(self)
        detail_frame.pack(fill="x", pady=5)

        ttk.Label(detail_frame, text="速度:").pack(side="left", padx=(0, 5))
        self.speed_label = ttk.Label(detail_frame, text="0 帧/秒")
        self.speed_label.pack(side="left", padx=5)

        ttk.Label(detail_frame, text="当前状态:").pack(side="left", padx=(20, 5))
        self.status_label = ttk.Label(detail_frame, text="就绪")
        self.status_label.pack(side="left", padx=5)

        # 初始化计时器
        self.start_time = None
        self.last_update_time = None
        self.last_processed = 0
        self.current_speed = 0

    def start(self, total_items):
        """开始进度跟踪"""
        self.progress_var.set(0)
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.last_processed = 0
        self.current_speed = 0
        self.total_items = total_items

        self.update_progress(0, total_items)
        self.update_status("正在处理...")

    def update_progress(self, processed, total):
        """更新进度"""
        if total > 0:
            percentage = (processed / total) * 100
            self.progress_var.set(percentage)
            self.progress_label.config(text=f"{percentage:.1f}% ({processed}/{total})")

            # 计算速度
            current_time = time.time()
            if self.last_update_time and processed > self.last_processed:
                time_diff = current_time - self.last_update_time
                items_diff = processed - self.last_processed
                if time_diff > 0:
                    self.current_speed = items_diff / time_diff
                    self.speed_label.config(text=f"{self.current_speed:.1f} 帧/秒")

            # 更新已用时间
            if self.start_time:
                elapsed = current_time - self.start_time
                self.elapsed_label.config(self._format_time(elapsed))

                # 计算剩余时间
                if processed > 0 and self.current_speed > 0:
                    remaining_items = total - processed
                    remaining_time = remaining_items / self.current_speed
                    self.remaining_label.config(self._format_time(remaining_time))
                else:
                    self.remaining_label.config(text="计算中...")

            self.last_update_time = current_time
            self.last_processed = processed

    def update_status(self, message):
        """更新状态信息"""
        self.status_label.config(text=message)

    def complete(self):
        """标记为完成"""
        self.progress_var.set(100)
        self.update_status("完成")
        if self.start_time:
            total_time = time.time() - self.start_time
            self.elapsed_label.config(self._format_time(total_time))
            self.remaining_label.config(text="00:00:00")

    def reset(self):
        """重置进度"""
        self.progress_var.set(0)
        self.progress_label.config(text="0% (0/0)")
        self.elapsed_label.config(text="00:00:00")
        self.remaining_label.config(text="00:00:00")
        self.speed_label.config(text="0 帧/秒")
        self.status_label.config(text="就绪")
        self.start_time = None

    def _format_time(self, seconds):
        """格式化时间显示"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


if __name__ == "__main__":
    root = tk.Tk()
    root.title("进度面板测试")

    panel = ProgressPanel(root)
    panel.pack(padx=20, pady=20, fill="x")

    def simulate_progress():
        import threading
        import time

        total = 100
        panel.start(total)

        def update():
            for i in range(total + 1):
                time.sleep(0.1)
                panel.update_progress(i, total)
            panel.complete()

        thread = threading.Thread(target=update, daemon=True)
        thread.start()

    ttk.Button(root, text="开始模拟进度", command=simulate_progress).pack(pady=10)
    ttk.Button(root, text="重置", command=panel.reset).pack(pady=10)

    root.mainloop()