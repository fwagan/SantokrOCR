"""
屏幕工具函数 - 窗口大小计算与居中
"""


def center_window(window, width, height):
    """将窗口居中放置在屏幕上"""
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def calc_image_window_size(sw, sh, img_w, img_h, chrome_w, chrome_h):
    """计算图片窗口的最佳尺寸，chrome之外撑满90%屏幕"""
    max_w = int(sw * 0.9)
    max_h = int(sh * 0.9)
    aw = max_w - chrome_w
    ah = max_h - chrome_h
    if aw <= 0 or ah <= 0:
        return (max_w, max_h)
    s = min(aw / img_w, ah / img_h)
    return (int(img_w * s + chrome_w), int(img_h * s + chrome_h))
