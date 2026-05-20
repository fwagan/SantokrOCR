"""
数据表格组件

基于ttk.Treeview实现的数据表格，支持：
1. 显示OCR识别结果
2. 排序和筛选
3. 双击查看对应帧截图
"""

import tkinter as tk
from tkinter import ttk


class DataTable(ttk.Frame):
    """数据表格组件"""

    def __init__(self, parent):
        super().__init__(parent)

        # 配置列
        self.columns = [
            ("frame", "帧号", 80),
            ("timestamp", "时间戳", 100),
            ("time_str", "时间", 120),
            ("temp1_full", "豆温", 120),
            ("temp1_normal", "豆温正常位", 120),
            ("temp1_faulty_digit", "豆温故障位", 100),
            ("temp2", "风温", 120)
        ]

        # 提取列ID列表
        column_ids = [col_id for col_id, _, _ in self.columns]

        # 创建Treeview和滚动条
        self.tree = ttk.Treeview(self, columns=column_ids, show="headings", selectmode="extended")
        self.scrollbar_v = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.scrollbar_h = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.scrollbar_v.set, xscrollcommand=self.scrollbar_h.set)

        self.setup_columns()

        self.setup_tags()

        # 回调函数，用于查看帧
        self.on_view_frame_callback = None

        # 布局（使用grid确保纵横滚动条共存）
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_v.grid(row=0, column=1, sticky="ns")
        self.scrollbar_h.grid(row=1, column=0, sticky="ew")

        # 绑定事件
        self.tree.bind("<Double-1>", self.on_row_double_click)

        # 右键菜单
        self.setup_context_menu()

    def setup_columns(self):
        """配置表格列"""
        for col_id, col_text, col_width in self.columns:
            self.tree.heading(col_id, text=col_text)
            self.tree.column(col_id, width=col_width, minwidth=50)

    def setup_tags(self):
        """配置标签颜色"""
        # 识别失败：无map匹配
        self.tree.tag_configure('recognition_failed', background='red', foreground='white')
        # 温差异常：
        self.tree.tag_configure('abnormal_temp', background='purple', foreground='white')
        # 分组分隔行
        self.tree.tag_configure('separator', background='#C0C0C0')

    def setup_context_menu(self):
        """设置右键菜单"""
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="查看帧截图", command=self.view_frame)
        self.context_menu.add_command(label="复制选中行", command=self.copy_row)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="删除选中行", command=self.delete_selected_rows)
        self.context_menu.add_command(label="删除所有选中行", command=self.delete_all_selected_rows)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="清除所有数据", command=self.clear)

        self.tree.bind("<Button-3>", self.show_context_menu)

    def add_row(self, data):
        """添加一行数据"""
        values = []
        for col_id, _, _ in self.columns:
            value = data.get(col_id, "")
            # 转换数字为字符串
            if isinstance(value, (int, float)):
                value = str(value)
            values.append(value)

        tags = []

        abnormal_category = data.get('abnormal_category')
        if abnormal_category == 'temperature_diff':
            tags.append('abnormal_temp')
        elif data.get('temp1_faulty_digit') == -1 or data.get('temp2', '') == '????':
            tags.append('recognition_failed')

        item = self.tree.insert("", "end", values=values, tags=tuple(tags))
        return item

    def load_all(self, results):
        """批量加载所有结果到表格（一次性插入）"""
        self.clear()
        for data in results:
            values = []
            for col_id, _, _ in self.columns:
                value = data.get(col_id, "")
                if isinstance(value, (int, float)):
                    value = str(value)
                values.append(value)

            tags = []
            abnormal_category = data.get('abnormal_category')
            if abnormal_category == 'temperature_diff':
                tags.append('abnormal_temp')
            elif data.get('temp1_faulty_digit') == -1 or data.get('temp2', '') == '????':
                tags.append('recognition_failed')

            self.tree.insert("", "end", values=values, tags=tuple(tags))

    def add_separator(self):
        """添加灰色分隔行（3行）"""
        empty_values = [""] * len(self.columns)
        for _ in range(3):
            self.tree.insert("", "end", values=empty_values, tags=('separator',))

    def clear(self):
        """清空所有数据"""
        for item in self.tree.get_children():
            self.tree.delete(item)

    def get_selected_row(self):
        """获取选中行的数据"""
        selection = self.tree.selection()
        if not selection:
            return None

        item = selection[0]
        values = self.tree.item(item, "values")
        if not values:
            return None

        # 转换为字典
        data = {}
        for i, (col_id, _, _) in enumerate(self.columns):
            data[col_id] = values[i]

        return data

    def get_row_data_by_item(self, item):
        """
        根据treeview项获取数据字典

        Args:
            item: treeview项ID

        Returns:
            数据字典，如果项不存在返回None
        """
        values = self.tree.item(item, "values")
        if not values:
            return None

        # 转换为字典，并恢复原始数据类型
        data = {}
        for i, (col_id, _, _) in enumerate(self.columns):
            value = values[i]

            # 根据列ID恢复原始数据类型
            if col_id == 'frame' and value:
                try:
                    data[col_id] = int(value)
                except ValueError:
                    data[col_id] = value
            elif col_id == 'timestamp' and value:
                try:
                    data[col_id] = float(value)
                except ValueError:
                    data[col_id] = value
            elif col_id == 'temp1_faulty_digit' and value:
                try:
                    data[col_id] = int(value)
                except ValueError:
                    data[col_id] = value
            else:
                data[col_id] = value

        return data

    def on_row_double_click(self, event):
        """双击行事件：查看帧截图"""
        self.view_frame()

    def view_frame(self):
        """查看帧截图"""
        data = self.get_selected_row()
        if not data:
            return

        # 获取帧号和时间戳（用于帧定位）
        try:
            frame_num = int(data['frame'])
            timestamp = float(data.get('timestamp', 0))
        except (ValueError, KeyError):
            return

        # 如果有回调函数，调用它（传入时间戳用于定位帧）
        if self.on_view_frame_callback:
            self.on_view_frame_callback(frame_num, timestamp, data)

    def copy_row(self):
        """复制选中行的数据到剪贴板"""
        data = self.get_selected_row()
        if not data:
            return

        # 格式化数据为文本
        lines = []
        for col_id, col_text, _ in self.columns:
            value = data.get(col_id, "")
            lines.append(f"{col_text}: {value}")

        text = "\n".join(lines)

        # 复制到剪贴板
        self.clipboard_clear()
        self.clipboard_append(text)

    def show_context_menu(self, event):
        """显示右键菜单"""
        # 检查是否点击了某行
        item = self.tree.identify_row(event.y)
        if item:
            # 如果点击的行不在当前选择中，则选择该行
            # 这样可以保持Shift/Ctrl多选
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def sort_by_column(self, col_id, reverse=False):
        """按列排序"""
        # 获取所有数据
        data = []
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            data.append((values, item))

        # 确定列索引
        col_index = None
        for i, (cid, _, _) in enumerate(self.columns):
            if cid == col_id:
                col_index = i
                break

        if col_index is None:
            return

        # 排序
        try:
            data.sort(key=lambda x: float(x[0][col_index]) if x[0][col_index].replace('.', '', 1).isdigit() else x[0][col_index], reverse=reverse)
        except:
            data.sort(key=lambda x: x[0][col_index], reverse=reverse)

        # 重新插入数据
        for i, (values, item) in enumerate(data):
            self.tree.move(item, "", i)

        # 切换排序方向
        self.tree.heading(col_id, command=lambda: self.sort_by_column(col_id, not reverse))

    def set_view_frame_callback(self, callback):
        """设置查看帧回调函数"""
        self.on_view_frame_callback = callback

    def delete_selected_rows(self):
        """删除选中行（单行或多行）"""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        # 如果有回调函数，通知主窗口
        if hasattr(self, 'on_rows_deleted_callback'):
            # 获取要删除的行的数据
            deleted_data = []
            for item in selected_items:
                data = self.get_row_data_by_item(item)
                if data:
                    deleted_data.append(data)
            self.on_rows_deleted_callback(selected_items, deleted_data)

        # 从treeview中删除
        for item in selected_items:
            self.tree.delete(item)

    def delete_all_selected_rows(self):
        """删除所有选中行（与delete_selected_rows相同，保持兼容性）"""
        self.delete_selected_rows()

    def set_rows_deleted_callback(self, callback):
        """设置行删除回调函数"""
        self.on_rows_deleted_callback = callback


if __name__ == "__main__":
    root = tk.Tk()
    root.title("数据表格测试")

    table = DataTable(root)
    table.pack(fill="both", expand=True, padx=10, pady=10)

    # 添加测试数据
    test_data = {
        'frame': 100,
        'timestamp': 10.5,
        'time_str': '00:00:10.500',
        'temp1_full': '1234',
        'temp1_normal': '123',
        'temp1_faulty_digit': '4',
        'temp2': '5678',
        'quality': 'high'
    }
    table.add_row(test_data)

    root.mainloop()