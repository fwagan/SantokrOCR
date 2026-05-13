#!/usr/bin/env python3
"""
SantokrOCR GUI 主程序入口

基于现有OCR视频处理算法，提供用户友好的图形界面。
支持视频选择、ROI框选、异步处理、数据验证等功能。
"""

import sys
import os

# ====== Windows DPI感知（解决tkinter模糊） ======
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ====== 修复PaddleOCR错误：禁用oneDNN优化 ======
# 解决"ConvertPirAttribute2RuntimeAttribute not support"错误
os.environ['FLAGS_enable_pir_api'] = '0'  
os.environ['FLAGS_use_mkldnn'] = '0'      # 禁用MKLDNN
os.environ['FLAGS_use_onednn'] = '0'     # 禁用OneDNN
os.environ['CUDA_VISIBLE_DEVICES'] = '-1' # 强制使用CPU

# 添加项目根目录到Python路径，确保模块导入正常
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import MainWindow

def main():
    """主函数"""
    try:
        # 创建并运行主窗口
        app = MainWindow()
        app.mainloop()
    except Exception as e:
        print(f"程序启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")

if __name__ == "__main__":
    main()