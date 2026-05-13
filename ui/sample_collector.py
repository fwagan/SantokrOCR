"""
样本收集器组件

用于收集故障位LED数字的标注样本。
支持显示故障位图像，让用户标注数字(0-9)。
实时显示标注统计信息。
"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
from PIL import Image, ImageTk
import threading
import numpy as np
import os
import time

# 导入OpenCV数字识别器
from core.digit_recognizer import DigitRecognizer


class SampleCollector(tk.Toplevel):
    """样本收集器窗口"""

    def __init__(self, parent, extractor, video_path, faulty_roi, start_frame=0, num_samples=50):
        """
        初始化样本收集器

        Args:
            parent: 父窗口
            extractor: VideoDigitExtractor实例
            video_path: 视频文件路径
            faulty_roi: 故障位ROI区域 (x, y, w, h)
            start_frame: 起始帧号
            num_samples: 需要收集的样本数量
        """
        super().__init__(parent)

        self.extractor = extractor
        self.video_path = video_path
        self.faulty_roi = faulty_roi
        self.start_frame = start_frame
        self.num_samples = num_samples

        # OpenCV数字识别器（用于OCR辅助标注）
        self.digit_recognizer = DigitRecognizer()
        # 设置故障位模式（因为这是故障位样本收集）
        self.digit_recognizer.set_mode('broken')

        # 样本数据
        self.samples = []  # 格式: [(image, label)]
        self.current_image = None
        self.current_frame_num = start_frame
        self.collecting = False

        # OCR识别结果（现在使用OpenCV数字识别器）
        self.current_ocr_digit = None
        self.current_ocr_confidence = 0.0
        self.current_ocr_text = ""

        # 统计信息
        # 包含所有数字（1, 7, 0/8, 2, 3, 4, 5, 6, 9）
        self.stats = {
            "1": 0, "7": 0,
            "0/8": 0,  # 特殊标签：可能是0或8，需要后续推断
            "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "9": 0
        }

        # 配置窗口
        self.title(f"故障位样本收集器 - 目标: {num_samples}个样本")
        self.geometry("800x700")
        self.minsize(600, 500)

        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 创建UI组件
        self.create_widgets()

        # 聚焦窗口
        self.focus_set()
        self.grab_set()

        # 绑定键盘快捷键
        self.bind_keyboard()

        # 初始化状态
        self.update_status("就绪，按'开始收集'按钮开始")

    def create_widgets(self):
        """创建UI组件"""
        # 主框架
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        # 控制面板（顶部）
        control_frame = ttk.LabelFrame(main_frame, text="控制面板", padding=10)
        control_frame.pack(fill="x", pady=(0, 10))

        # 信息和状态
        info_frame = ttk.Frame(control_frame)
        info_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(info_frame, text=f"视频: {os.path.basename(self.video_path)}").pack(side="left", padx=5)
        ttk.Label(info_frame, text=f"目标样本数: {self.num_samples}").pack(side="left", padx=5)
        ttk.Label(info_frame, text=f"起始帧: {self.start_frame}").pack(side="left", padx=5)

        # 状态显示
        self.status_label = ttk.Label(control_frame, text="就绪")
        self.status_label.pack(fill="x", pady=5)

        # 控制按钮
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill="x", pady=5)

        self.start_button = ttk.Button(button_frame, text="开始收集",
                                      command=self.start_collection, state="normal")
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(button_frame, text="停止收集",
                                     command=self.stop_collection, state="disabled")
        self.stop_button.pack(side="left", padx=5)

        ttk.Button(button_frame, text="保存样本",
                  command=self.save_samples, state="disabled").pack(side="left", padx=5)
        ttk.Button(button_frame, text="训练分类器",
                  command=self.train_classifier, state="disabled").pack(side="left", padx=5)

        # 图像显示区域（中间）
        image_frame = ttk.LabelFrame(main_frame, text="当前故障位图像", padding=10)
        image_frame.pack(fill="both", expand=True, pady=(0, 10))

        # 创建画布用于显示图像
        self.canvas = tk.Canvas(image_frame, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # OCR结果显示区域（在标注按钮上方）
        ocr_result_frame = ttk.Frame(image_frame)
        ocr_result_frame.pack(fill="x", pady=(10, 5))

        ttk.Label(ocr_result_frame, text="OCR识别结果:").pack(side="left", padx=(0, 5))
        self.ocr_result_label = ttk.Label(ocr_result_frame, text="无", font=("TkDefaultFont", 9, "bold"))
        self.ocr_result_label.pack(side="left", padx=(0, 10))

        ttk.Label(ocr_result_frame, text="置信度:").pack(side="left", padx=(0, 5))
        self.ocr_confidence_label = ttk.Label(ocr_result_frame, text="0.0", font=("TkDefaultFont", 9))
        self.ocr_confidence_label.pack(side="left", padx=(0, 20))

        self.confirm_button = ttk.Button(ocr_result_frame, text="确认OCR结果",
                                        command=self.confirm_ocr_result,
                                        state="disabled")
        self.confirm_button.pack(side="left", padx=(0, 10))

        # 标注按钮区域
        label_frame = ttk.Frame(image_frame)
        label_frame.pack(fill="x", pady=(5, 0))

        ttk.Label(label_frame, text="请标注这个数字（或纠正OCR）:").pack(side="left", padx=(0, 10))

        # 数字标注按钮（只显示需要用户标注的数字）
        # 数字1和7可以自动识别，不需要用户标注
        # 数字0和8需要特殊处理，标记为"0/8"
        # 其他数字（2、3、4、5、6、9）需要用户标注

        # 定义需要用户标注的数字列表（包含1和7，以便用户纠正OCR错误）
        self.label_digits = ["1", "7", "0/8", "2", "3", "4", "5", "6", "9"]

        for label in self.label_digits:
            if label == "0/8":
                # 特殊按钮：标记为0/8（需要后续推断）
                btn = ttk.Button(label_frame, text="0/8", width=3,
                                command=lambda: self.label_zero_eight(),
                                state="disabled")
            else:
                # 普通数字按钮
                digit = int(label)
                btn = ttk.Button(label_frame, text=label, width=2,
                                command=lambda d=digit: self.label_digit(d),
                                state="disabled")
            btn.pack(side="left", padx=2)
            setattr(self, f'digit_button_{label}', btn)

        # 跳过按钮
        self.skip_button = ttk.Button(label_frame, text="跳过",
                  command=self.skip_image, state="disabled")
        self.skip_button.pack(side="left", padx=(20, 0))

        # 统计信息面板（底部）
        stats_frame = ttk.LabelFrame(main_frame, text="标注统计", padding=10)
        stats_frame.pack(fill="x", pady=(0, 10))

        # 创建统计网格
        self.stats_labels = {}
        stats_grid = ttk.Frame(stats_frame)
        stats_grid.pack(fill="x")

        # 显示需要统计的项目：包含所有数字（1, 7, 0/8, 2, 3, 4, 5, 6, 9）
        display_labels = ["1", "7", "0/8", "2", "3", "4", "5", "6", "9"]

        for i, label in enumerate(display_labels):
            frame = ttk.Frame(stats_grid)
            # 每行显示4个项目
            frame.grid(row=i//4, column=i%4, padx=10, pady=5, sticky="w")

            display_text = f"标签 {label}:" if label == "0/8" else f"数字 {label}:"
            ttk.Label(frame, text=display_text).pack(side="left", padx=(0, 5))
            stats_label = ttk.Label(frame, text="0", font=("TkDefaultFont", 10, "bold"))
            stats_label.pack(side="left")
            self.stats_labels[label] = stats_label

        # 总计
        total_frame = ttk.Frame(stats_grid)
        # 9个标签显示为3行（每行4个），总计在第3行（0-indexed）
        total_frame.grid(row=3, column=0, columnspan=4, padx=10, pady=10, sticky="w")

        ttk.Label(total_frame, text="总计:", font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=(0, 5))
        self.total_label = ttk.Label(total_frame, text="0", font=("TkDefaultFont", 10, "bold"))
        self.total_label.pack(side="left")

        # 快捷键提示
        hint_frame = ttk.Frame(stats_frame)
        hint_frame.pack(fill="x", pady=(5, 0))

        hint_text = "快捷键: 键盘数字键0-9标注对应数字, Space跳过, S保存, T训练, Esc退出"
        ttk.Label(hint_frame, text=hint_text, font=("TkDefaultFont", 8)).pack()

    def start_collection(self):
        """开始收集样本"""
        if self.collecting:
            return

        self.collecting = True
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

        # 启用标注按钮（0/8和数字2、3、4、5、6、9）
        for label in self.label_digits:
            getattr(self, f'digit_button_{label}').config(state="normal")

        # 启用跳过按钮
        self.skip_button.config(state="normal")

        self.update_status("正在收集样本...")

        # 启动样本收集线程
        thread = threading.Thread(target=self.collection_worker, daemon=True)
        thread.start()

    def collection_worker(self):
        """样本收集工作线程"""
        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                self.update_status("无法打开视频文件")
                return

            # 跳转到起始帧
            cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_frame)
            frame_count = self.start_frame

            while self.collecting and len(self.samples) < self.num_samples:
                ret, frame = cap.read()
                if not ret:
                    break

                # 每隔10帧取一个样本（避免连续帧太相似）
                if frame_count % 10 == 0:
                    x, y, w, h = self.faulty_roi

                    # 确保ROI在图像范围内
                    if y+h <= frame.shape[0] and x+w <= frame.shape[1]:
                        digit_img = frame[y:y+h, x:x+w]

                        # 步骤1: 使用OpenCV数字识别器进行识别
                        self.current_ocr_digit = None
                        self.current_ocr_confidence = 0.0
                        self.current_ocr_text = ""

                        # 尝试数字识别（故障位模式）
                        try:
                            # 使用LEDDigitClassifier进行识别（故障位模式）
                            digit, confidence = self.digit_recognizer.led_classifier.recognize(digit_img, mode='broken')

                            # digit可能是: -1 (未知), -2 (可疑值0/8), 或 0-9 的数字
                            # confidence是置信度 (0.0-1.0)

                            self.current_ocr_confidence = confidence

                            if digit >= 0:
                                # 正常识别到的数字
                                self.current_ocr_digit = digit
                                self.current_ocr_text = str(digit)
                            elif digit == -2:
                                # 可疑值，可能是0或8（中间段g坏掉情况）
                                # 在故障位模式下，数字0和8显示相同，标记为可疑值-2
                                self.current_ocr_digit = 0  # 显示为0（但实际是0/8）
                                self.current_ocr_text = "0/8"

                                # 自动处理：直接标记为0/8，不需要用户标注
                                self.samples.append((digit_img.copy(), -2))
                                self.stats["0/8"] += 1
                                self.after(0, self.update_stats)
                                self.after(0, lambda: self.update_status(
                                    f"帧{frame_count}: 识别为可疑值0/8，自动标记 (共 {len(self.samples)}/{self.num_samples})"
                                ))
                                continue
                            else:
                                # digit == -1 或其他，识别失败
                                self.current_ocr_digit = None
                                self.current_ocr_text = ""

                        except Exception as e:
                            # 识别失败，继续处理（仍然显示图像让用户标注）
                            pass

                        # 无论OCR是否成功，都显示图像让用户标注/确认
                        # （除非是数字0，上面已经continue）
                        self.current_image = digit_img.copy()
                        self.current_frame_num = frame_count

                        # 更新UI显示图像
                        self.after(0, lambda: self.display_image(digit_img))

                        # 等待用户标注（通过信号或状态变量）
                        self.waiting_for_label = True
                        while self.waiting_for_label and self.collecting:
                            threading.Event().wait(0.1)

                        # 如果停止收集，退出循环
                        if not self.collecting:
                            break

                frame_count += 1

            cap.release()

            if self.collecting:
                if len(self.samples) >= self.num_samples:
                    self.update_status(f"样本收集完成！共收集 {len(self.samples)} 个样本")
                    messagebox.showinfo("完成", f"已成功收集 {len(self.samples)} 个故障位样本")
                else:
                    self.update_status("视频已结束，样本收集中断")

        except Exception as e:
            self.update_status(f"收集过程中出错: {e}")
            import traceback
            traceback.print_exc()

        finally:
            self.after(0, self.collection_finished)

    def display_image(self, image):
        """在画布上显示图像"""
        if image is None or image.size == 0:
            return

        try:
            # 转换为RGB
            if len(image.shape) == 3 and image.shape[2] == 3:
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            else:
                # 灰度图
                if len(image.shape) == 2:
                    image_rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
                else:
                    return

            # 转换为PIL图像
            pil_image = Image.fromarray(image_rgb)

            # 调整大小以适应画布
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()

            if canvas_width > 1 and canvas_height > 1:
                # 保持宽高比缩放
                img_width, img_height = pil_image.size
                scale = min(canvas_width / img_width, canvas_height / img_height) * 0.9
                new_width = int(img_width * scale)
                new_height = int(img_height * scale)
                pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # 转换为Tkinter PhotoImage
            self.tk_image = ImageTk.PhotoImage(pil_image)

            # 清除画布并显示新图像
            self.canvas.delete("all")
            self.canvas.create_image(canvas_width//2, canvas_height//2,
                                    anchor="center", image=self.tk_image)

            # 显示帧号信息
            self.canvas.create_text(10, 10, anchor="nw", fill="white",
                                   text=f"帧号: {self.current_frame_num}", font=("Arial", 10))

            # 在帧号下方显示OCR结果（如果可用）
            if self.current_ocr_digit is not None:
                ocr_info = f"OCR结果: {self.current_ocr_digit} ({self.current_ocr_text}) 置信度: {self.current_ocr_confidence:.2f}"
                self.canvas.create_text(10, 30, anchor="nw", fill="yellow",
                                       text=ocr_info, font=("Arial", 9))

            # 更新OCR结果显示标签
            if self.current_ocr_digit is not None:
                self.ocr_result_label.config(text=f"{self.current_ocr_digit} ({self.current_ocr_text})")
                self.ocr_confidence_label.config(text=f"{self.current_ocr_confidence:.2f}")

                # 根据OCR结果启用/禁用Confirm按钮
                # 如果OCR识别为数字且置信度>0.5，启用Confirm按钮
                if self.current_ocr_digit >= 0 and self.current_ocr_confidence > 0.5:
                    self.confirm_button.config(state="normal")
                else:
                    self.confirm_button.config(state="disabled")
            else:
                self.ocr_result_label.config(text="无")
                self.ocr_confidence_label.config(text="0.0")
                self.confirm_button.config(state="disabled")

        except Exception as e:
            print(f"显示图像失败: {e}")

    def confirm_ocr_result(self):
        """确认OCR识别结果（用户点击Confirm按钮）"""
        if self.current_image is None or not self.waiting_for_label:
            return

        if self.current_ocr_digit is None:
            return

        # 使用OCR识别的数字作为标签
        digit = self.current_ocr_digit

        # 对于数字0，需要特殊处理（标记为-2，表示0/8）
        if digit == 0:
            # 标记为0/8（-2）
            self.samples.append((self.current_image.copy(), -2))
            self.stats["0/8"] += 1
            self.update_stats()
            self.update_status(f"已确认OCR结果: 0/8 (共 {len(self.samples)}/{self.num_samples})")
        else:
            # 其他数字
            self.samples.append((self.current_image.copy(), digit))
            # 更新统计
            if digit == 1:
                self.stats["1"] += 1
            elif digit == 7:
                self.stats["7"] += 1
            else:
                # 数字2,3,4,5,6,9等
                self.stats[str(digit)] += 1
            self.update_stats()
            self.update_status(f"已确认OCR结果: {digit} (共 {len(self.samples)}/{self.num_samples})")

        # 继续下一张图像
        self.waiting_for_label = False

    def label_zero_eight(self):
        """标注当前图像为"0/8"（可能是0或8，需要后续推断）"""
        if self.current_image is None or not self.waiting_for_label:
            return

        # 添加样本，标签为-2（表示0/8）
        # 注意：在LEDDigitClassifier中，-2表示"可能是0或8"
        self.samples.append((self.current_image.copy(), -2))

        # 更新统计
        self.stats["0/8"] += 1
        self.update_stats()

        # 更新状态
        self.update_status(f"已标注为 0/8 (需要推断) (共 {len(self.samples)}/{self.num_samples})")

        # 继续下一张
        self.waiting_for_label = False

    def label_digit(self, digit):
        """标注当前图像为指定数字"""
        if self.current_image is None or not self.waiting_for_label:
            return

        # 添加样本
        self.samples.append((self.current_image.copy(), digit))

        # 更新统计
        self.stats[str(digit)] += 1
        self.update_stats()

        # 更新状态
        self.update_status(f"已标注数字 {digit} (共 {len(self.samples)}/{self.num_samples})")

        # 继续下一张
        self.waiting_for_label = False

    def skip_image(self):
        """跳过当前图像"""
        if self.waiting_for_label:
            self.update_status("已跳过当前图像")
            self.waiting_for_label = False

    def update_stats(self):
        """更新统计信息显示"""
        total = sum(self.stats.values())

        for digit, count in self.stats.items():
            self.stats_labels[digit].config(text=str(count))

        self.total_label.config(text=str(total))

        # 根据收集的样本数更新按钮状态
        if total >= 10:  # 至少10个样本才能训练
            for widget in self.winfo_children():
                if isinstance(widget, ttk.Button) and widget.cget("text") == "训练分类器":
                    widget.config(state="normal")

        if total > 0:
            for widget in self.winfo_children():
                if isinstance(widget, ttk.Button) and widget.cget("text") == "保存样本":
                    widget.config(state="normal")

    def update_status(self, message):
        """更新状态显示"""
        self.status_label.config(text=message)
        self.status_label.update()

    def stop_collection(self):
        """停止收集样本"""
        self.collecting = False
        self.waiting_for_label = False

        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

        self.update_status(f"已停止收集，共收集 {len(self.samples)} 个样本")

    def collection_finished(self):
        """收集完成后的清理工作"""
        self.collecting = False
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

        # 禁用标注按钮
        for label in self.label_digits:
            getattr(self, f'digit_button_{label}').config(state="disabled")

        # 禁用跳过按钮
        self.skip_button.config(state="disabled")

    def save_samples(self):
        """保存收集的样本"""
        if not self.samples:
            messagebox.showwarning("警告", "没有可保存的样本")
            return

        from tkinter import filedialog
        import os
        import pickle

        # 选择保存路径
        save_dir = filedialog.askdirectory(title="选择样本保存目录")
        if not save_dir:
            return

        try:
            # 保存样本数据
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            data_file = os.path.join(save_dir, f"faulty_samples_{timestamp}.pkl")

            # 保存为pickle文件
            with open(data_file, 'wb') as f:
                pickle.dump(self.samples, f)

            # 保存为图像文件（可选）
            images_dir = os.path.join(save_dir, f"samples_{timestamp}")
            os.makedirs(images_dir, exist_ok=True)

            for i, (img, label) in enumerate(self.samples):
                img_file = os.path.join(images_dir, f"sample_{i:03d}_label_{label}.png")
                cv2.imwrite(img_file, img)

            self.update_status(f"样本已保存到: {save_dir}")
            messagebox.showinfo("保存成功", f"样本已保存到:\n{data_file}")

        except Exception as e:
            messagebox.showerror("保存失败", f"保存过程中出错:\n{e}")

    def train_classifier(self):
        """使用收集的样本训练分类器"""
        if len(self.samples) < 10:
            messagebox.showwarning("警告", "样本数量不足（至少需要10个样本）")
            return

        try:
            self.update_status("正在训练分类器...")

            # 调用extractor的训练方法
            # 注意：extractor中的faulty_classifier需要支持训练方法
            if hasattr(self.extractor.faulty_classifier, 'train'):
                self.extractor.faulty_classifier.train(self.samples)
                self.update_status("分类器训练完成！")
                messagebox.showinfo("训练完成", "故障位分类器已使用新样本重新训练")
            else:
                messagebox.showwarning("警告", "当前分类器不支持训练功能")
                self.update_status("分类器不支持训练")

        except Exception as e:
            messagebox.showerror("训练失败", f"训练过程中出错:\n{e}")
            self.update_status("训练失败")

    def on_closing(self):
        """窗口关闭事件处理"""
        if self.collecting:
            if messagebox.askyesno("确认关闭", "样本收集仍在进行中，确定要关闭吗？"):
                self.stop_collection()
                self.destroy()
        else:
            self.destroy()

    def bind_keyboard(self):
        """绑定键盘快捷键"""
        self.bind('<Escape>', lambda e: self.on_closing())

        # 数字键0-9
        for i in range(10):
            if i == 0 or i == 8:
                # 数字0和8都映射到0/8标注
                self.bind(f'<Key-{i}>', lambda e: self.label_zero_eight())
            else:
                self.bind(f'<Key-{i}>', lambda e, digit=i: self.label_digit(digit))

        self.bind('<space>', lambda e: self.skip_image())
        self.bind('<Key-s>', lambda e: self.save_samples())
        self.bind('<Key-t>', lambda e: self.train_classifier())


if __name__ == "__main__":
    # 测试代码
    root = tk.Tk()
    root.withdraw()

    # 模拟数据
    class MockExtractor:
        def __init__(self):
            pass

        class faulty_classifier:
            @staticmethod
            def train(samples):
                print(f"模拟训练分类器，样本数: {len(samples)}")

    extractor = MockExtractor()
    faulty_roi = (100, 100, 50, 50)

    collector = SampleCollector(root, extractor, "test.mp4", faulty_roi, 0, 10)
    root.mainloop()