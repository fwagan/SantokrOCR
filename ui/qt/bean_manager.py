"""
生豆信息管理窗口（PySide6 QDialog 版）

逐步替换 ui/bean_manager.py 的 tkinter 版本。
迁移完成后此文件应移回 ui/ 并删除 tkinter 版。
"""

import os
import json
import copy

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeView, QGroupBox, QFormLayout, QLineEdit,
    QCheckBox, QPushButton, QWidget, QLabel,
    QDialogButtonBox, QMessageBox, QAbstractItemView,
    QHeaderView,
)
from PySide6.QtGui import QStandardItemModel, QStandardItem, QBrush, QColor

BEAN_FIELDS = [
    ('name', '名称:'),
    ('variety', '豆种:'),
    ('process', '处理法:'),
    ('origin', '产地:'),
    ('altitude', '海拔(m):'),
    ('density', '密度(g/L):'),
    ('moisture', '含水率(%):'),
]

# 右侧字段编辑框样式（深色主题）
_BASE_EDIT_STYLE = ("background-color: #3c3c3c; color: #e0e0e0; "
                    "border: 1px solid #555555; border-radius: 3px; padding: 2px 4px;")
_MODIFIED_EDIT_STYLE = ("background-color: #3c3c3c; color: #FFD700; font-style: italic; "
                        "border: 1px solid #555555; border-radius: 3px; padding: 2px 4px;")
_BASE_CB_STYLE = "color: #e0e0e0; spacing: 6px;"
_MODIFIED_CB_STYLE = "color: #FFD700; font-style: italic; spacing: 6px;"


def _get_bean_json_path():
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
    return os.path.join(app_data, 'SantokrOCR', 'BeanInfo', 'beans.json')


class BeanManagerDialog(QDialog):
    """生豆信息管理对话框"""

    saved = Signal()  # 保存后发出

    def __init__(self, parent=None, on_save_callback=None):
        super().__init__(parent)
        self.on_save_callback = on_save_callback

        self.setWindowTitle("生豆信息管理")
        self.setMinimumSize(700, 500)
        self.resize(800, 550)

        # 适配深色模式：控件使用暗色背景，浅色文字
        self.setStyleSheet("""
            QDialog {
                background-color: #2b2b2b;
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
            QLineEdit {
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 2px 4px;
            }
            QLineEdit:focus {
                border-color: #4a90d9;
            }
            QCheckBox {
                color: #e0e0e0;
                spacing: 6px;
            }
            QTreeView {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #555555;
                alternate-background-color: #333333;
            }
            QTreeView::item:selected {
                background-color: #4a90d9;
                color: #ffffff;
            }
            QLabel {
                color: #e0e0e0;
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
            QSplitter::handle {
                background-color: #555555;
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
        """)

        # ---------- 数据 ----------
        self._beans = []
        self._original = []
        self._new_indices = set()
        self._deleted_indices = set()
        self._loading_detail = False

        # 右侧字段控件
        self._field_edits = {}   # field_name -> QLineEdit
        self._outofstock_cb = None

        # 加载数据
        self._load()

        # 创建 UI
        self._create_ui()

    # ============== 数据层 ==============

    def _load(self):
        path = _get_bean_json_path()
        self._beans = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._beans = json.load(f)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"加载生豆信息失败:\n{e}")
        self._original = copy.deepcopy(self._beans)
        self._new_indices.clear()
        self._deleted_indices.clear()

    def _save(self):
        path = _get_bean_json_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        to_save = [
            b for i, b in enumerate(self._beans)
            if i not in self._deleted_indices and b.get('name', '').strip()
        ]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)

        self._original = copy.deepcopy(to_save)
        self._new_indices.clear()
        self._deleted_indices.clear()
        self._beans = copy.deepcopy(to_save)

        self._clear_detail()
        self._rebuild_tree()
        self._clear_field_styles()

        self.saved.emit()
        if self.on_save_callback:
            # 延迟执行 tkinter 回调，避免在 processEvents() 内嵌套调用导致 GIL 异常
            QTimer.singleShot(0, self.on_save_callback)

    def _has_unsaved_changes(self):
        if self._new_indices or self._deleted_indices:
            return True
        for i, bean in enumerate(self._beans):
            if i >= len(self._original):
                return True
            if bean != self._original[i]:
                return True
        return False

    def _bean_status(self, idx):
        """返回 (prefix_char, color) 或 (None, None)"""
        if idx in self._deleted_indices:
            return 'D', QColor('red')
        if idx in self._new_indices:
            return 'N', QColor('green')
        if idx < len(self._original) and self._beans[idx] != self._original[idx]:
            return 'M', QColor('#CC8800')
        return None, None

    # ============== UI 创建 ==============

    def _create_ui(self):
        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)

        # ---- 左侧：TreeView ----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.tree_model = QStandardItemModel(0, 2, self)
        self.tree_model.setHeaderData(0, Qt.Horizontal, "名称")
        self.tree_model.setHeaderData(1, Qt.Horizontal, "已用尽")

        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        left_layout.addWidget(self.tree)

        # [+]/[-] 按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        del_btn = QPushButton("-")
        del_btn.setFixedWidth(30)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left_widget)

        # ---- 右侧：详情编辑 ----
        right_widget = QGroupBox("生豆详情")
        form_layout = QFormLayout(right_widget)
        form_layout.setSpacing(8)

        for field_name, label_text in BEAN_FIELDS:
            edit = QLineEdit()
            edit.textChanged.connect(lambda _, fn=field_name: self._on_field_changed(fn))
            self._field_edits[field_name] = edit
            form_layout.addRow(label_text, edit)

        self._outofstock_cb = QCheckBox("已用尽")
        self._outofstock_cb.stateChanged.connect(lambda: self._on_field_changed('outOfStock'))
        form_layout.addRow(self._outofstock_cb)

        splitter.addWidget(right_widget)
        splitter.setSizes([260, 540])

        main_layout.addWidget(splitter)

        # ---- 底部按钮 ----
        button_box = QDialogButtonBox()
        save_btn = button_box.addButton("保存", QDialogButtonBox.AcceptRole)
        cancel_btn = button_box.addButton("取消", QDialogButtonBox.RejectRole)
        main_layout.addWidget(button_box)

        # ---- 信号连接 ----
        self.tree.selectionModel().currentChanged.connect(self._on_tree_selection)
        add_btn.clicked.connect(self._on_add)
        del_btn.clicked.connect(self._on_delete)
        button_box.accepted.connect(self._on_save_clicked)
        button_box.rejected.connect(self._on_close)

        # 初始填充
        self._rebuild_tree()

    def _rebuild_tree(self):
        """刷新 treeview"""
        self.tree_model.removeRows(0, self.tree_model.rowCount())

        for i, bean in enumerate(self._beans):
            name_item = self._make_name_item(i, bean)
            oos_item = self._make_oos_item(i, bean)
            self.tree_model.appendRow([name_item, oos_item])

        if self.tree_model.rowCount() > 0:
            self.tree.setCurrentIndex(self.tree_model.index(0, 0))

    def _make_name_item(self, idx, bean):
        """为树创建名称 QStandardItem（颜色由 _bean_status 决定）"""
        prefix_char, color = self._bean_status(idx)
        display_name = bean.get('name', '')
        if prefix_char:
            display_name = f"{prefix_char} {display_name}"

        item = QStandardItem(display_name)
        if color:
            item.setForeground(QBrush(color))
        if bean.get('outOfStock', False):
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        return item

    def _make_oos_item(self, idx, bean):
        """为树创建已用尽 QStandardItem"""
        oos = bean.get('outOfStock', False)
        item = QStandardItem('☑' if oos else '☐')
        return item

    def _update_tree_row(self, idx):
        """只更新树中特定行（避免全量重建导致选中跳转）"""
        if idx < 0 or idx >= self.tree_model.rowCount():
            return
        bean = self._beans[idx]
        self.tree_model.setItem(idx, 0, self._make_name_item(idx, bean))
        self.tree_model.setItem(idx, 1, self._make_oos_item(idx, bean))

    # ============== 交互处理 ==============

    def _current_index(self):
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        return idx.row()

    def _load_detail(self, idx):
        """加载指定索引的生豆数据到右侧字段"""
        self._loading_detail = True
        bean = self._beans[idx]
        for field_name in ('name', 'variety', 'process', 'origin', 'altitude', 'density', 'moisture'):
            self._field_edits[field_name].setText(bean.get(field_name, ''))
        self._outofstock_cb.setChecked(bean.get('outOfStock', False))
        self._loading_detail = False
        self._update_field_styles(idx)

    def _clear_detail(self):
        """清空右侧字段"""
        self._loading_detail = True
        for edit in self._field_edits.values():
            edit.clear()
        self._outofstock_cb.setChecked(False)
        self._loading_detail = False

    def _on_tree_selection(self, *args):
        """选中哪行就显示哪行的数据，fields 直接绑定到 _beans"""
        idx = self._current_index()
        if idx is None:
            return
        self._selected_index = idx
        self._load_detail(idx)

    def _on_field_changed(self, field_name):
        if self._loading_detail:
            return
        idx = self._current_index()
        if idx is None:
            return

        bean = self._beans[idx]
        if field_name == 'outOfStock':
            bean['outOfStock'] = self._outofstock_cb.isChecked()
        else:
            bean[field_name] = self._field_edits[field_name].text()

        self._update_field_styles(idx)
        self._update_tree_row(idx)

    def _update_field_styles(self, idx):
        """右侧面板：M记录中被修改的字段设为黄色斜体文字"""
        if idx is None or idx >= len(self._original) or idx in (self._deleted_indices | self._new_indices):
            self._clear_field_styles()
            return

        orig = self._original[idx] if idx < len(self._original) else {}
        bean = self._beans[idx]

        for field_name, edit in self._field_edits.items():
            curr = bean.get(field_name, '')
            orig_val = orig.get(field_name, '') if orig else ''
            edit.setStyleSheet(_MODIFIED_EDIT_STYLE if curr != orig_val else _BASE_EDIT_STYLE)

        # 已用尽复选框
        curr_oos = bean.get('outOfStock', False)
        orig_oos = orig.get('outOfStock', False) if orig else False
        self._outofstock_cb.setStyleSheet(_MODIFIED_CB_STYLE if curr_oos != orig_oos else _BASE_CB_STYLE)

    def _clear_field_styles(self):
        """重置右侧字段为默认样式"""
        for edit in self._field_edits.values():
            edit.setStyleSheet(_BASE_EDIT_STYLE)
        self._outofstock_cb.setStyleSheet(_BASE_CB_STYLE)

    def _on_add(self):
        """新增生豆记录"""
        new_idx = len(self._beans)
        self._beans.append({
            'name': '', 'variety': '', 'process': '', 'origin': '',
            'altitude': '', 'density': '', 'moisture': '',
            'outOfStock': False,
        })
        self._new_indices.add(new_idx)
        self._original.append({})
        self._rebuild_tree()

        idx = self.tree_model.index(new_idx, 0)
        self.tree.setCurrentIndex(idx)
        # 聚焦到名称输入框
        self._field_edits['name'].setFocus()

    def _on_delete(self):
        """标记删除选中的记录"""
        idx = self._current_index()
        if idx is None:
            return

        if idx in self._new_indices:
            # 新增记录→直接从列表中移除
            self._beans.pop(idx)
            self._new_indices.discard(idx)
            self._new_indices = {i if i < idx else i - 1 for i in self._new_indices}
            self._selected_index = None
            self._rebuild_tree()
            if self.tree_model.rowCount() > 0:
                target = min(idx, self.tree_model.rowCount() - 1)
                self.tree.setCurrentIndex(self.tree_model.index(target, 0))
        else:
            # 已有记录→仅标记已删除，不改变选中行和右侧数据
            self._deleted_indices.add(idx)
            self._update_tree_row(idx)

    def _on_save_clicked(self):
        """保存按钮"""
        self._save()

    def _on_close(self):
        """关闭"""
        if self._has_unsaved_changes():
            result = QMessageBox.question(
                self, "未保存的更改",
                "有未保存的更改，是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if result == QMessageBox.Yes:
                self._on_save_clicked()
                self.accept()
            elif result == QMessageBox.No:
                self.reject()
            else:
                return
        else:
            self.reject()

    def closeEvent(self, event):
        """覆盖关闭事件"""
        if self._has_unsaved_changes():
            result = QMessageBox.question(
                self, "未保存的更改",
                "有未保存的更改，是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if result == QMessageBox.Yes:
                self._save()
                event.accept()
            elif result == QMessageBox.No:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
