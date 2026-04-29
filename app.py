import sys
import os
import json
import fcntl
import hashlib
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QListWidget, QListWidgetItem, QLineEdit, QLabel, 
                             QPushButton, QInputDialog, QMessageBox, QAbstractItemView)
from PyQt6.QtGui import QCursor, QFont, QIcon, QPixmap
from PyQt6.QtCore import Qt, QEvent, QTimer, QSize, QPropertyAnimation, QEasingCurve

# 导入 Mac 底层 API
import objc
from AppKit import (NSPasteboard, NSPasteboardTypeString, NSPasteboardTypePNG, NSPasteboardTypeTIFF, 
                    NSImage, NSApplication, NSApplicationActivationPolicyAccessory, NSStatusBar, 
                    NSVariableStatusItemLength, NSObject, NSFont)

# 数据存储路径
support_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(support_dir, exist_ok=True)
DATA_FILE = os.path.join(support_dir, "history.json")
IMAGE_DIR = os.path.join(support_dir, "images")
os.makedirs(IMAGE_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(support_dir, "settings.json")

# ==========================================
# 1. 使用原生 Mac Status Bar 解决“左键直接打开”和“二级菜单”问题
# ==========================================
class TrayHandler(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(TrayHandler, self).init()
        self.callback = callback
        # 创建一个状态栏图标
        self.statusItem = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.statusItem.button().setTitle_("📋")
        self.statusItem.button().setFont_(NSFont.systemFontOfSize_(16))
        self.statusItem.button().setTarget_(self)
        self.statusItem.button().setAction_(objc.selector(self.onClick_, signature=b'v@:'))
        return self

    @objc.IBAction
    def onClick_(self, sender):
        # 无论左键还是右键，都直接执行 callback（打开我们的漂亮面板）
        self.callback()

# ==========================================
# 2. 漂亮的主 UI 界面 (高颜值、内嵌搜索)
# ==========================================
class ClipboardApp(QWidget):
    def __init__(self):
        super().__init__()
        self.history = []
        self.max_items = 150
        self.prevent_hide = False # 防止弹窗时主窗口意外隐藏
        
        # 100% 稳定的 Mac 原生剪贴板监控
        self.pb = NSPasteboard.generalPasteboard()
        self.last_change_count = self.pb.changeCount()
        
        self.is_dark_mode = True
        self.load_settings()
        self.load_data()
        self.init_ui()
        self.apply_theme()
        
        # 挂载原生托盘图标
        self.tray_handler = TrayHandler.alloc().initWithCallback_(self.toggle_window)
        
        # 启动定时器监控剪贴板
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_clipboard)
        self.timer.start(500) # 每 0.5 秒检查一次

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.is_dark_mode = data.get("is_dark_mode", True)
            except:
                pass

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump({"is_dark_mode": self.is_dark_mode}, f)
        except:
            pass

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.history = []
                    for item in data:
                        if isinstance(item, str):
                            self.history.append({"type": "text", "content": item, "time": "", "pinned": False, "tags": []})
                        elif isinstance(item, dict):
                            item_type = item.get("type", "text")
                            content = item.get("content", item.get("text", ""))
                            self.history.append({
                                "type": item_type,
                                "content": content,
                                "hash": item.get("hash", ""),
                                "time": item.get("time", ""),
                                "pinned": item.get("pinned", False),
                                "tags": item.get("tags", [])
                            })
            except Exception as e:
                print(f"Error loading data: {e}")
                self.history = []

    def save_data(self):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving data: {e}")

    def init_ui(self):
        # 无边框 + 悬浮窗 + 圆角暗色主题
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(360, 520)
        
        # 主容器
        self.main_widget = QWidget(self)
        self.main_widget.setStyleSheet("""
            QWidget#MainWidget {
                background-color: #252526;
                border: 1px solid #3E3E42;
                border-radius: 12px;
            }
            QLabel { color: #D4D4D4; }
        """)
        self.main_widget.setObjectName("MainWidget")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_widget)
        
        inner_layout = QVBoxLayout(self.main_widget)
        inner_layout.setContentsMargins(12, 16, 12, 12)
        inner_layout.setSpacing(12)
        
        # 复制成功的 Toast 提示 (初始隐藏，悬浮在最上层)
        self.toast_label = QLabel("✓ 已复制到剪贴板", self.main_widget)
        self.toast_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast_label.resize(240, 44)
        self.toast_label.move((self.width() - 240) // 2, 400) # 居中靠下
        self.toast_label.hide()
        
        # Toast 动画效果
        self.toast_anim = QPropertyAnimation(self.toast_label, b"windowOpacity")
        self.toast_anim.setDuration(300)
        self.toast_anim.setStartValue(0.0)
        self.toast_anim.setEndValue(1.0)
        
        # =================================
        # 内嵌搜索框与主题切换
        # =================================
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索剪贴板内容 或 标签...")
        self.search_input.textChanged.connect(self.refresh_list)
        search_layout.addWidget(self.search_input)
        
        self.theme_btn = QPushButton("🌙" if self.is_dark_mode else "☀️")
        self.theme_btn.setToolTip("切换明亮/黑暗模式")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.clicked.connect(self.toggle_theme)
        search_layout.addWidget(self.theme_btn)
        
        inner_layout.addLayout(search_layout)
        
        # =================================
        # 列表展示区
        # =================================
        self.list_widget = QListWidget()
        self.list_widget.setWordWrap(True) # 允许文本换行
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff) # 彻底禁用横向滚动条
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        inner_layout.addWidget(self.list_widget)

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self.theme_btn.setText("🌙" if self.is_dark_mode else "☀️")
        self.save_settings()
        self.apply_theme()
        self.refresh_list()

    def apply_theme(self):
        if self.is_dark_mode:
            self.main_widget.setStyleSheet("""
                QWidget#MainWidget {
                    background-color: #252526;
                    border: 1px solid #3E3E42;
                    border-radius: 12px;
                }
                QLabel { color: #D4D4D4; }
            """)
            self.search_input.setStyleSheet("""
                QLineEdit {
                    padding: 10px 12px;
                    border: 1px solid #3E3E42;
                    border-radius: 8px;
                    background-color: #1E1E1E;
                    color: #E0E0E0;
                    font-size: 14px;
                }
                QLineEdit:focus {
                    border: 1px solid #007ACC;
                }
            """)
            self.list_widget.setStyleSheet("""
                QListWidget {
                    background-color: transparent;
                    border: none;
                    outline: none;
                }
                QListWidget::item {
                    background-color: #2D2D30;
                    border-radius: 8px;
                    margin-bottom: 6px;
                }
                QListWidget::item:selected {
                    background-color: #094771;
                }
                QListWidget::item:hover {
                    background-color: #2A2D2E;
                }
            """)
            self.theme_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1E1E1E;
                    border: 1px solid #3E3E42;
                    border-radius: 8px;
                    font-size: 16px;
                }
                QPushButton:hover { background-color: #2D2D30; }
            """)
            self.toast_label.setStyleSheet("""
                QLabel {
                    background-color: #4EC9B0;
                    color: #1E1E1E;
                    font-size: 14px;
                    font-weight: bold;
                    border-radius: 22px;
                    padding: 10px 20px;
                }
            """)
        else:
            self.main_widget.setStyleSheet("""
                QWidget#MainWidget {
                    background-color: #F3F3F3;
                    border: 1px solid #CCCCCC;
                    border-radius: 12px;
                }
                QLabel { color: #333333; }
            """)
            self.search_input.setStyleSheet("""
                QLineEdit {
                    padding: 10px 12px;
                    border: 1px solid #CCCCCC;
                    border-radius: 8px;
                    background-color: #FFFFFF;
                    color: #333333;
                    font-size: 14px;
                }
                QLineEdit:focus {
                    border: 1px solid #007ACC;
                }
            """)
            self.list_widget.setStyleSheet("""
                QListWidget {
                    background-color: transparent;
                    border: none;
                    outline: none;
                }
                QListWidget::item {
                    background-color: #FFFFFF;
                    border-radius: 8px;
                    margin-bottom: 6px;
                }
                QListWidget::item:selected {
                    background-color: #CCE8FF;
                }
                QListWidget::item:hover {
                    background-color: #E5F3FF;
                }
            """)
            self.theme_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FFFFFF;
                    border: 1px solid #CCCCCC;
                    border-radius: 8px;
                    font-size: 16px;
                }
                QPushButton:hover { background-color: #E5E5E5; }
            """)
            self.toast_label.setStyleSheet("""
                QLabel {
                    background-color: #007ACC;
                    color: #FFFFFF;
                    font-size: 14px;
                    font-weight: bold;
                    border-radius: 22px;
                    padding: 10px 20px;
                }
            """)

    def check_clipboard(self):
        count = self.pb.changeCount()
        if count != self.last_change_count:
            self.last_change_count = count
            
            # 1. 尝试读取文本
            text = self.pb.stringForType_(NSPasteboardTypeString)
            if text and text.strip():
                content = text.strip()
                existing = next((item for item in self.history if item.get('type') == 'text' and item.get('content') == content), None)
                if existing:
                    self.history.remove(existing)
                    existing['time'] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self.history.insert(0, existing)
                else:
                    self.history.insert(0, {
                        "type": "text",
                        "content": content,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "pinned": False,
                        "tags": []
                    })
                
                self.enforce_limits()
                self.save_data()
                if self.isVisible():
                    self.refresh_list()
                return

            # 2. 尝试读取图片
            img_data = self.pb.dataForType_(NSPasteboardTypePNG) or self.pb.dataForType_(NSPasteboardTypeTIFF)
            if img_data:
                try:
                    b = bytes(img_data)
                except Exception:
                    b = str(datetime.now().timestamp()).encode()
                img_hash = hashlib.md5(b).hexdigest()
                img_path = os.path.join(IMAGE_DIR, f"{img_hash}.png")
                
                if not os.path.exists(img_path):
                    img_data.writeToFile_atomically_(img_path, True)
                
                existing = next((item for item in self.history if item.get('type') == 'image' and item.get('hash') == img_hash), None)
                if existing:
                    self.history.remove(existing)
                    existing['time'] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self.history.insert(0, existing)
                else:
                    self.history.insert(0, {
                        "type": "image",
                        "content": img_path,
                        "hash": img_hash,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "pinned": False,
                        "tags": []
                    })
                    
                self.enforce_limits()
                self.save_data()
                if self.isVisible():
                    self.refresh_list()

    def enforce_limits(self):
        pinned = [i for i in self.history if i.get('pinned')]
        unpinned = [i for i in self.history if not i.get('pinned')]
        if len(self.history) > self.max_items:
            allowed = max(0, self.max_items - len(pinned))
            # 删除多余的本地图片文件
            for dropped in unpinned[allowed:]:
                if dropped.get('type') == 'image':
                    try:
                        if os.path.exists(dropped['content']): os.remove(dropped['content'])
                    except Exception: pass
            unpinned = unpinned[:allowed]
            self.history = pinned + unpinned
        self.history.sort(key=lambda x: (x.get('pinned', False), x.get('time', '')), reverse=True)

    def refresh_list(self):
        self.list_widget.clear()
        search_query = self.search_input.text().lower()
        self.sort_history()
        
        # 计算卡片的绝对宽度，防止超出屏幕
        # 主窗口 360，左右边距 12+12，滚动条大约 15，剩余安全宽度约 320
        safe_width = self.width() - 40
        
        for item_data in self.history:
            content = item_data['content']
            item_type = item_data.get('type', 'text')
            
            # 搜索过滤：匹配内容或标签
            if search_query:
                if item_type == 'text' and search_query not in content.lower():
                    if not any(search_query in t.lower() for t in item_data.get('tags', [])):
                        continue
                elif item_type == 'image' and not any(search_query in t.lower() for t in item_data.get('tags', [])):
                    continue
            
            item_widget = QWidget()
            item_widget.setFixedWidth(safe_width) # 强制卡片宽度
            item_layout = QVBoxLayout(item_widget)
            item_layout.setContentsMargins(12, 10, 12, 10)
            item_layout.setSpacing(6)
            
            # 文本或图片内容
            if item_type == 'text':
                # 移除之前的强行截断，改用 Qt 的自动换行属性
                display_text = content.replace('\r', '')
                
                # 限制最大显示字符数，如果超长则在末尾加省略号，防止单条过长
                if len(display_text) > 800:
                    display_text = display_text[:800] + "...\n(内容过长，已省略部分)"
                    
                content_widget = QLabel(display_text)
                content_widget.setWordWrap(True) # 核心：允许文字换行
                content_widget.setFixedWidth(safe_width - 24) # 强制文本标签宽度，留出边距
                content_widget.setStyleSheet(f"font-size: 14px; color: {'#E0E0E0' if self.is_dark_mode else '#333333'};")
                
                # 放宽高度限制，显示更多内容
                content_widget.setMaximumHeight(200) 
            else:
                content_widget = QLabel()
                if os.path.exists(content):
                    pixmap = QPixmap(content)
                    # 限制最大宽度为容器宽度减去边距，保证不会撑出横向滚动条
                    max_width = safe_width - 24 
                    scaled_pixmap = pixmap.scaled(max_width, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    content_widget.setPixmap(scaled_pixmap)
                else:
                    content_widget.setText("[图片已丢失]")
                    content_widget.setStyleSheet("color: #E51400;")
                    
            item_layout.addWidget(content_widget)
            
            # 底部行：时间/标签 + 操作按钮
            bottom_layout = QHBoxLayout()
            bottom_layout.setSpacing(8)
            
            # 左侧：时间和标签
            info_color = "#858585" if self.is_dark_mode else "#666666"
            info_text = f"<span style='color: {info_color}; font-size: 11px;'>{item_data.get('time', '')}</span>"
            tags = item_data.get('tags', [])
            if tags:
                tag_color = "#4EC9B0" if self.is_dark_mode else "#007ACC"
                tags_html = " ".join([f"<span style='color: {tag_color}; font-size: 11px; font-weight: bold;'>#{t}</span>" for t in tags])
                info_text += f" &nbsp; {tags_html}"
            
            info_label = QLabel(info_text)
            bottom_layout.addWidget(info_label)
            bottom_layout.addStretch()
            
            # 右侧：按钮 (打标签、常驻、删除)
            btn_color = "#858585" if self.is_dark_mode else "#666666"
            btn_hover = "#D4D4D4" if self.is_dark_mode else "#333333"
            btn_style = f"""
                QPushButton {{ background: transparent; color: {btn_color}; border: none; font-size: 12px; }}
                QPushButton:hover {{ color: {btn_hover}; }}
            """
            
            tag_btn = QPushButton("🏷️")
            tag_btn.setToolTip("编辑标签")
            tag_btn.setStyleSheet(btn_style)
            tag_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            tag_btn.clicked.connect(lambda checked, c=content: self.edit_tags(c))
            
            pin_btn = QPushButton("📍" if item_data.get('pinned') else "📌")
            pin_btn.setToolTip("取消常驻" if item_data.get('pinned') else "设为常驻")
            pin_btn.setStyleSheet(btn_style if not item_data.get('pinned') else "QPushButton { background: transparent; color: #E51400; border: none; font-size: 12px; }")
            pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pin_btn.clicked.connect(lambda checked, c=content: self.toggle_pin(c))
            
            del_btn = QPushButton("🗑️")
            del_btn.setToolTip("删除")
            del_btn.setStyleSheet(btn_style)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda checked, c=content: self.delete_item(c))
            
            bottom_layout.addWidget(tag_btn)
            bottom_layout.addWidget(pin_btn)
            bottom_layout.addWidget(del_btn)
            
            item_layout.addLayout(bottom_layout)
            
            list_item = QListWidgetItem(self.list_widget)
            list_item.setSizeHint(item_widget.sizeHint())
            list_item.setData(Qt.ItemDataRole.UserRole, content)
            
            self.list_widget.addItem(list_item)
            self.list_widget.setItemWidget(list_item, item_widget)

    def sort_history(self):
        self.history.sort(key=lambda x: (x.get('pinned', False), x.get('time', '')), reverse=True)

    def on_item_clicked(self, item):
        content = item.data(Qt.ItemDataRole.UserRole)
        target = next((i for i in self.history if i['content'] == content), None)
        if not target: return
        
        # 写入系统剪贴板 (使用底层 NSPasteboard)
        self.pb.clearContents()
        if target.get('type', 'text') == 'text':
            self.pb.setString_forType_(content, NSPasteboardTypeString)
        elif target.get('type') == 'image' and os.path.exists(content):
            img = NSImage.alloc().initWithContentsOfFile_(content)
            if img:
                self.pb.writeObjects_([img])
                
        self.last_change_count = self.pb.changeCount()
        
        # 移到最前
        self.history.remove(target)
        self.history.insert(0, target)
        self.enforce_limits()
        self.save_data()
            
        # UI 反馈：弹出 Toast 并延迟关闭
        list_widget_item = self.list_widget.itemWidget(item)
        if list_widget_item:
            bg_color = "#2E7D32" if self.is_dark_mode else "#4CAF50"
            list_widget_item.setStyleSheet(f"background-color: {bg_color}; border-radius: 8px;")
            
        self.prevent_hide = True
        self.toast_label.raise_() # 确保 Toast 提示在最上层
        self.toast_label.show()
        self.toast_anim.start()
        
        # 0.8秒后隐藏面板
        QTimer.singleShot(800, self.finish_copy_feedback)
        
    def finish_copy_feedback(self):
        self.toast_label.hide()
        self.prevent_hide = False
        self.hide()

    def toggle_pin(self, content):
        for item in self.history:
            if item['content'] == content:
                item['pinned'] = not item.get('pinned', False)
                break
        self.enforce_limits()
        self.save_data()
        self.refresh_list()
        
    def delete_item(self, content):
        target = next((i for i in self.history if i['content'] == content), None)
        if target:
            self.history.remove(target)
            self.save_data()
            self.refresh_list()

    def edit_tags(self, content):
        target = next((i for i in self.history if i['content'] == content), None)
        if not target: return
        
        self.prevent_hide = True # 防止弹窗时主窗口消失
        current_tags = ",".join(target.get('tags', []))
        
        # 弹出一个漂亮的对话框
        dialog = QInputDialog(self)
        dialog.setWindowTitle("编辑标签")
        dialog.setLabelText("请输入标签 (多个用逗号分隔):")
        dialog.setTextValue(current_tags)
        
        if self.is_dark_mode:
            dialog.setStyleSheet("""
                QInputDialog { background-color: #252526; color: #D4D4D4; }
                QLabel { color: #D4D4D4; }
                QLineEdit { background-color: #1E1E1E; color: white; border: 1px solid #3E3E42; padding: 4px; }
                QPushButton { background-color: #0E639C; color: white; border: none; padding: 6px 12px; border-radius: 4px; }
                QPushButton:hover { background-color: #1177BB; }
            """)
        else:
            dialog.setStyleSheet("""
                QInputDialog { background-color: #F3F3F3; color: #333333; }
                QLabel { color: #333333; }
                QLineEdit { background-color: #FFFFFF; color: #333333; border: 1px solid #CCCCCC; padding: 4px; }
                QPushButton { background-color: #007ACC; color: white; border: none; padding: 6px 12px; border-radius: 4px; }
                QPushButton:hover { background-color: #005C99; }
            """)
        
        if dialog.exec():
            new_tags_str = dialog.textValue()
            target['tags'] = [t.strip() for t in new_tags_str.split(',') if t.strip()]
            self.save_data()
            self.refresh_list()
            
        self.prevent_hide = False

    def toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.refresh_list()
            self.search_input.clear()
            
            # 定位在鼠标点击处（右上角状态栏下方）
            cursor_pos = QCursor.pos()
            x = cursor_pos.x() - self.width() / 2
            y = cursor_pos.y() + 10 # 留点间距
            
            if x < 0: x = 0
            if y < 0: y = 0
            
            self.move(int(x), int(y))
            self.show()
            self.raise_()
            self.activateWindow()
            self.search_input.setFocus() # 自动聚焦搜索框

    # 失去焦点时自动隐藏（像原生菜单一样）
    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate:
            if not self.prevent_hide:
                self.hide()
        return super().event(e)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # 隐藏 Dock 栏图标
    try:
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except:
        pass
        
    # 单例模式检测：防止重复打开
    lock_file_path = os.path.join(support_dir, "app.lock")
    lock_file = open(lock_file_path, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        QMessageBox.warning(None, "运行提示", "剪贴板程序已经在运行中了！")
        sys.exit(0)
        
    ex = ClipboardApp()
    sys.exit(app.exec())