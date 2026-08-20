"""
.slog 文件查看器（PySide6 QMainWindow 版）

功能：
1. 打开并查看.slog文件（包含results和events的JSON格式）
2. 显示温度曲线、ROR分析、火力/风门曲线、事件标记、阶段条
3. 支持参数调整（重采样间隔、平滑窗口等）
4. 烘焙信息管理（豆种、产地等）
"""

import json
import os
import sys


def _setup_path():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(this_dir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_setup_path()

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ui.qt.bean_manager import BeanManagerDialog
from ui.qt.statistics_panel import StatisticsPanel


class SlogViewer(QMainWindow):
    """.slog文件查看器（QMainWindow 版本）"""

    ROAST_FIELDS = [
        ('roast_date', '烘焙日期:', False),
        ('roast_time', '烘焙时间:', False),
        # 豆种/处理法/产地/海拔 来自生豆信息，只读
        ('variety', '豆种:', True),
        ('process', '处理法:', True),
        ('origin', '产地:', True),
        ('altitude', '海拔(m):', True),
        ('density', '密度(g/L):', False),
        ('moisture', '含水率(%):', False),
        ('green_weight', '生豆重量:', False),
        ('roasted_weight', '熟豆重量:', False),
        ('weight_loss', '失重率:', True),
    ]

    def __init__(self, parent=None, file_path=None):
        super().__init__(parent)

        self.setWindowTitle("Slog Viewer")
        self.setMinimumSize(900, 600)
        self.resize(1800, 1200)

        # 当前加载的文件路径
        self.current_path = None
        self.source_identity = ""
        self._last_dir = ""

        # 烘焙信息编辑控件
        self.roast_edits = {}
        self.roast_notes = None
        self.bean_combo = None
        self.roast_no_edit = None
        self.roast_total_edit = None

        # 生豆数据
        self._beans_data = []

        # 统计面板
        self.stats_panel = StatisticsPanel(self)

        # 创建菜单栏
        self.create_menu()

        # 创建布局
        self._create_layout()

        # 加载生豆信息
        self._load_bean_info()

        # 恢复上次使用的路径
        self._load_config()

        # 如果有文件路径，直接加载
        if file_path:
            self.load_file(file_path)

        # 暗色主题
        self._apply_stylesheet()

    # ============== 菜单栏 ==============

    def create_menu(self):
        menubar = self.menuBar()

        op_menu = menubar.addMenu("操作")

        open_action = QAction("打开", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.open_slog)
        op_menu.addAction(open_action)

        saveas_action = QAction("另存为", self)
        saveas_action.setShortcut(QKeySequence("Ctrl+S"))
        saveas_action.triggered.connect(self.export_slog)
        op_menu.addAction(saveas_action)

        compare_action = QAction("对比曲线", self)
        compare_action.triggered.connect(self.open_comparer)
        op_menu.addAction(compare_action)

        op_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        op_menu.addAction(exit_action)

    # ============== 布局 ==============

    def _create_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 左侧面板（固定宽度，不可调整）
        left_widget = QWidget()
        left_widget.setFixedWidth(480)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 滚动区（烘焙信息）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        roast_container = QWidget()
        self._create_roast_info(roast_container)
        scroll.setWidget(roast_container)
        left_layout.addWidget(scroll, stretch=1)

        # 控制参数（从 statistics_panel 注入）
        control_container = QWidget()
        control_layout = QVBoxLayout(control_container)
        control_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_panel.create_controls(control_container)
        left_layout.addWidget(control_container, stretch=0)

        main_layout.addWidget(left_widget)

        # 右侧面板：图表（自适应剩余空间）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.stats_panel)

        main_layout.addWidget(right_widget, stretch=1)

    def _create_roast_info(self, parent):
        """创建烘焙信息面板"""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("烘焙信息")
        form_layout = QFormLayout(group)
        form_layout.setSpacing(6)

        # 烘焙日期
        edit = QLineEdit()
        self.roast_edits['roast_date'] = edit
        form_layout.addRow("烘焙日期:", edit)

        # 烘焙时间
        edit = QLineEdit()
        self.roast_edits['roast_time'] = edit
        form_layout.addRow("烘焙时间:", edit)

        # 烘焙次序（两个短 textbox 并排）
        order_widget = QWidget()
        order_layout = QHBoxLayout(order_widget)
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_layout.addWidget(QLabel("第"))
        self.roast_no_edit = QLineEdit()
        self.roast_no_edit.setMaxLength(3)
        self.roast_no_edit.setFixedWidth(50)
        order_layout.addWidget(self.roast_no_edit)
        order_layout.addWidget(QLabel("共"))
        self.roast_total_edit = QLineEdit()
        self.roast_total_edit.setMaxLength(3)
        self.roast_total_edit.setFixedWidth(50)
        order_layout.addWidget(self.roast_total_edit)
        order_layout.addStretch()
        form_layout.addRow("烘焙次序:", order_widget)

        # 生豆名称
        bean_widget = QWidget()
        bean_layout = QHBoxLayout(bean_widget)
        bean_layout.setContentsMargins(0, 0, 0, 0)
        self.bean_combo = QComboBox()
        self.bean_combo.currentTextChanged.connect(self._on_bean_selected)
        bean_layout.addWidget(self.bean_combo, stretch=1)
        manage_btn = QPushButton("管理")
        manage_btn.clicked.connect(self._open_bean_manager)
        bean_layout.addWidget(manage_btn)
        form_layout.addRow("生豆名称:", bean_widget)

        # 标准字段（跳过 roast_date, roast_time）
        for key, label, readonly in self.ROAST_FIELDS[2:]:
            edit = QLineEdit()
            if readonly:
                edit.setReadOnly(True)
                # 只读字段用灰色背景
                edit.setStyleSheet("QLineEdit { background-color: #2b2b2b; color: #a0a0a0; }")
            self.roast_edits[key] = edit
            form_layout.addRow(label, edit)

        # green_weight / roasted_weight → 自动计算失重率
        self.roast_edits['green_weight'].textChanged.connect(self._update_weight_loss)
        self.roast_edits['roasted_weight'].textChanged.connect(self._update_weight_loss)

        # 备注
        self.roast_notes = QPlainTextEdit()
        self.roast_notes.setMaximumBlockCount(100)
        self.roast_notes.setPlaceholderText("备注...")
        form_layout.addRow("备注:", self.roast_notes)

        layout.addWidget(group)

    # ============== 烘焙信息管理 ==============

    def _update_weight_loss(self):
        """计算失重率"""
        try:
            green = float(self.roast_edits['green_weight'].text() or 0)
            roasted = float(self.roast_edits['roasted_weight'].text() or 0)
            if green > 0 and roasted > 0:
                loss = (green - roasted) / green * 100
                self.roast_edits['weight_loss'].setText(f"{loss:.1f}%")
            else:
                self.roast_edits['weight_loss'].setText('')
        except ValueError:
            self.roast_edits['weight_loss'].setText('')

    def _collect_roast_info(self):
        """收集烘焙信息为 dict"""
        info = {'bean_name': self.bean_combo.currentText()}
        for key in ('roast_date', 'roast_time', 'variety', 'process', 'origin', 'altitude',
                    'green_weight', 'roasted_weight'):
            info[key] = self.roast_edits[key].text()
        info['roast_no'] = self.roast_no_edit.text()
        info['roast_total'] = self.roast_total_edit.text()

        # density/moisture: 只存 override（与 bean info 默认不同才存）
        bean_name = info['bean_name']
        bean = next((b for b in self._beans_data if b['name'] == bean_name), None)
        for key in ('density', 'moisture'):
            val = self.roast_edits[key].text()
            default = bean.get(key, '') if bean else ''
            info[key] = val if val != default else ''
        info['weight_loss'] = self.roast_edits['weight_loss'].text()
        info['notes'] = self.roast_notes.toPlainText().strip()
        return info

    # ============== 生豆信息管理 ==============

    def _get_bean_json_path(self):
        app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
        return os.path.join(app_data, 'SantokrOCR', 'BeanInfo', 'beans.json')

    @staticmethod
    def _get_config_path():
        app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
        cfg_dir = os.path.join(app_data, 'SantokrOCR', 'SlogViewer')
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, 'config.json')

    def _load_config(self):
        path = self._get_config_path()
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self._last_dir = cfg.get('last_dir', '')
        except Exception:
            self._last_dir = ''

    def _save_config(self):
        path = self._get_config_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'last_dir': self._last_dir}, f)
        except Exception:
            pass

    def _load_bean_info(self):
        path = self._get_bean_json_path()
        self._beans_data = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    all_beans = json.load(f)
                self._beans_data = [b for b in all_beans if not b.get('outOfStock', False)]
            except Exception:
                self._beans_data = []
        names = [b['name'] for b in self._beans_data if b.get('name')]
        self.bean_combo.clear()
        self.bean_combo.addItems(names)

    def _on_bean_selected(self, name):
        if not name:
            return
        bean = next((b for b in self._beans_data if b['name'] == name), None)
        if bean:
            self._apply_bean_info(bean)

    def _apply_bean_info(self, bean):
        for key in ('variety', 'process', 'origin', 'altitude'):
            self.roast_edits[key].setText(bean.get(key, ''))
        for key in ('density', 'moisture'):
            if not self.roast_edits[key].text():
                self.roast_edits[key].setText(bean.get(key, ''))

    def _open_bean_manager(self):
        dialog = BeanManagerDialog(self, on_save_callback=self._refresh_bean_dropdown)
        dialog.exec()

    def _refresh_bean_dropdown(self):
        self._load_bean_info()
        current = self.bean_combo.currentText()
        if current:
            idx = self.bean_combo.findText(current)
            if idx >= 0:
                self.bean_combo.setCurrentIndex(idx)

    # ============== 文件操作 ==============

    def open_slog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开.slog文件",
            self._last_dir, "Slog files (*.slog);;All files (*.*)"
        )
        if file_path:
            self._last_dir = os.path.dirname(file_path)
            self._save_config()
            self.load_file(file_path)

    def load_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法加载文件:\n{e}")
            return

        version = data.get('version', 0)
        if version < 1:
            QMessageBox.warning(self, "警告", "文件格式版本过低，可能无法正确加载")

        results = data.get('results', [])
        events = data.get('events', [])
        heater_initial = data.get('heater_initial', 50.0)
        fan_initial = data.get('fan_initial', 80.0)

        if not results:
            QMessageBox.warning(self, "警告", "文件中没有有效的results数据")
            return

        self.stats_panel.set_results(results)
        self.stats_panel.set_events(events, heater_initial, fan_initial)

        self.current_path = file_path
        self.source_identity = os.path.splitext(os.path.basename(file_path))[0]
        self.setWindowTitle(f"Slog Viewer - {self.source_identity}")
        self.stats_panel.setStatus(
            f"已加载: {os.path.basename(file_path)} "
            f"({len(results)}条记录, {len(events)}个事件)"
        )

        # 加载烘焙信息
        roast_info = data.get('roast_info', {})
        for key, edit in self.roast_edits.items():
            edit.setText(roast_info.get(key, ''))
        self.roast_notes.setPlainText(roast_info.get('notes', ''))

        # 烘焙次序
        self.roast_no_edit.setText(roast_info.get('roast_no', ''))
        self.roast_total_edit.setText(roast_info.get('roast_total', ''))

        # 生豆名称
        bean_name = roast_info.get('bean_name', '')
        if bean_name:
            idx = self.bean_combo.findText(bean_name)
            if idx >= 0:
                self.bean_combo.setCurrentIndex(idx)
                bean = next((b for b in self._beans_data if b['name'] == bean_name), None)
                if bean:
                    self._apply_bean_info(bean)
                    # density/moisture override from slog
                    if roast_info.get('density'):
                        self.roast_edits['density'].setText(roast_info['density'])
                    if roast_info.get('moisture'):
                        self.roast_edits['moisture'].setText(roast_info['moisture'])
            else:
                QMessageBox.warning(self, "警告", f"找不到生豆信息: {bean_name}")

        self._update_weight_loss()

    def export_slog(self):
        if not self.stats_panel.results:
            QMessageBox.warning(self, "警告", "没有可导出的数据")
            return

        default_name = self.source_identity or "export"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出数据",
            os.path.join(self._last_dir, f"{default_name}.slog"),
            "Slog files (*.slog);;All files (*.*)"
        )
        if not file_path:
            return

        self._last_dir = os.path.dirname(file_path)
        self._save_config()

        export = {
            'version': 1,
            'roast_info': self._collect_roast_info(),
            'results': self.stats_panel.results,
            'events': self.stats_panel.events,
            'heater_initial': self.stats_panel.heater_initial,
            'fan_initial': self.stats_panel.fan_initial,
        }

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(export, f, indent=2, ensure_ascii=False)

        self.stats_panel.setStatus(f"数据已导出到: {file_path}")

    def open_comparer(self):
        """打开曲线对比器（桥接到 tkinter SlogComparer）"""
        if not self.current_path:
            QMessageBox.warning(self, "警告", "请先打开一个.slog文件")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self, "选择对比的.slog文件",
            "", "Slog files (*.slog);;All files (*.*)"
        )
        if not files:
            return

        all_files = [self.current_path] + list(files)
        if len(all_files) > 5:
            QMessageBox.critical(self, "错误", "最多允许5个slog参与对比")
            return

        # 桥接到 tkinter SlogComparer（待 slog_comparer 迁移后替换）
        import tkinter as tk
        root = tk._default_root if tk._default_root else tk.Tk()
        if not tk._default_root:
            root.withdraw()
        from ui.slog_comparer import SlogComparer
        SlogComparer(root, file_paths=all_files)

    # ============== 暗色主题 ==============

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
            }
            QWidget {
                background-color: #2b2b2b;
                color: #e0e0e0;
            }
            QGroupBox {
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 4px;
                margin-top: 12px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #e0e0e0;
            }
            QLineEdit, QPlainTextEdit {
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 2px 4px;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #4a90d9;
            }
            QComboBox {
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 2px 4px;
            }
            QComboBox:focus {
                border-color: #4a90d9;
            }
            QComboBox::drop-down {
                border: none;
            }
            QPushButton {
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QLabel {
                color: #e0e0e0;
            }
            QSplitter::handle {
                background-color: #555555;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: #2b2b2b;
                width: 12px;
            }
            QScrollBar::handle:vertical {
                background-color: #555555;
                min-height: 20px;
                border-radius: 4px;
            }
            QMenuBar {
                background-color: #2b2b2b;
                color: #e0e0e0;
            }
            QMenuBar::item:selected {
                background-color: #4a90d9;
            }
            QMenu {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #555555;
            }
            QMenu::item:selected {
                background-color: #4a90d9;
            }
            QSpinBox {
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 2px 4px;
            }
            QSpinBox:focus {
                border-color: #4a90d9;
            }
            QCheckBox {
                color: #e0e0e0;
                spacing: 6px;
            }
        """)


def open_slog_viewer(parent, file_path=None):
    """从父窗口打开slog viewer"""
    return SlogViewer(parent, file_path)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    file_path = sys.argv[1] if len(sys.argv) > 1 else None
    viewer = SlogViewer(file_path=file_path)
    viewer.show()
    sys.exit(app.exec())
