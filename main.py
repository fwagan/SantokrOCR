#!/usr/bin/env python3
"""
SantokrOCR GUI 主程序入口

基于现有OCR视频处理算法，提供用户友好的图形界面。
支持视频选择、ROI框选、异步处理、数据验证等功能。
"""

import os
import sys

# ====== 限制 MKL 线程数（减少启动开销） ======
os.environ['MKL_NUM_THREADS'] = '4'
os.environ['OPENBLAS_NUM_THREADS'] = '1'

# ====== Windows DPI感知（解决tkinter模糊） ======
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 添加项目根目录到Python路径，确保模块导入正常
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.dashboard import Dashboard


def main():
    """主函数"""
    try:
        # 如果传入了 .slog 文件参数（文件关联双击），直接启动 SlogViewer
        if len(sys.argv) > 1 and sys.argv[1].lower().endswith('.slog'):
            import tkinter as tk

            from ui.slog_viewer import SlogViewer

            root = tk.Tk()
            root.withdraw()
            app = SlogViewer(root, sys.argv[1])
            app.protocol("WM_DELETE_WINDOW", lambda: (root.quit(), root.destroy()))
            root.mainloop()
        else:
            app = Dashboard()
            app.mainloop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            from tkinter import messagebox
            messagebox.showerror("启动失败", str(e))
        except Exception:
            pass


if __name__ == "__main__":
    main()
