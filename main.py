import sys
import os
import re
import threading
import subprocess
import shutil
import time
import glob
import datetime
from lxml import etree as ET
from PyQt5.QtCore import QEvent, QObject, QUrl, Qt, pyqtSignal, pyqtSlot, QDir, QTimer
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout,
                             QPushButton, QFileDialog, QListWidget,
                             QHBoxLayout, QLabel, QComboBox, QCheckBox,
                             QSpacerItem, QSizePolicy, QDesktopWidget,
                             QTextBrowser, QDialog, QLineEdit,
                             QDialogButtonBox, QMessageBox, QToolButton, QGroupBox)
from PyQt5.QtGui import QTextCursor, QFont, QColor
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.Qt import QDesktopServices

CURRENT_VERSION = "1.3.0"

class QListWidgetItemEvent(QEvent):
    EVENT_TYPE = QEvent.registerEventType()

    def __init__(self, category_name):
        super().__init__(QListWidgetItemEvent.EVENT_TYPE)
        self.category_name = category_name

class ModifyNameDialog(QDialog):
    def __init__(self, old_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改游戏名称")
        self.setFixedSize(400, 150)

        layout = QVBoxLayout()
        self.name_edit = QLineEdit(old_name if old_name else "")
        self.name_edit.setPlaceholderText("请输入新游戏名称")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(QLabel("新名称:"))
        layout.addWidget(self.name_edit)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_new_name(self):
        return self.name_edit.text().strip()

class XMLNameExtractor(QWidget):
    status_signal = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self.enable_video_playback = False  # 新增播放控制状态
        self.initUI()
        self.category_dirs = {}
        self.name_video_mapping = {}
        self.media_player = None
        self.raw_results = []
        self.sorted_results = []
        self.current_tree = None
        self.current_xml_path = None
        self.lock = threading.Lock()
        self.status_signal.connect(self._append_status, Qt.QueuedConnection)
        self.last_highlight = None
        self.export_button = None
        self.dir_link_button = None
        self.status_bar.installEventFilter(self)
        self._last_export_path = None
        self.deleted_games = []
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.filter_games)
        self.check_update()

    def initUI(self):
        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()

        left_layout.setStretch(0, 1)
        left_layout.setStretch(1, 1)
        left_layout.setStretch(2, 1)
        left_layout.setStretch(3, 1)

        self.select_folder_button = QPushButton('选择RetroBat所在目录', self)
        self.select_folder_button.clicked.connect(self.select_folder)
        left_layout.addWidget(self.select_folder_button)

        category_group = QVBoxLayout()
        self.category_list = QListWidget(self)
        self.category_list.itemClicked.connect(self.show_category_info)
        category_group.addWidget(self.category_list)

        self.category_count_label = QLabel("当前列表的机种数量：0", self)
        self.category_count_label.setStyleSheet("color: #666;")
        category_group.addWidget(self.category_count_label)
        left_layout.addLayout(category_group)

        result_group = QVBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("输入关键字过滤游戏...")
        self.search_box.textChanged.connect(self.on_search_text_changed)
        result_group.addWidget(self.search_box)

        self.result_list = QListWidget(self)
        self.result_list.itemClicked.connect(self.handle_item_click)
        self.result_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.result_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                padding: 2px;
            }
            QListWidget::item {
                padding: 3px;
            }
            QListWidget::item:selected {
                background-color: #4CAF50;
                color: white;
            }
        """)
        result_group.addWidget(self.result_list)

        self.game_count_label = QLabel("当前列表拥有游戏：0", self)
        self.game_count_label.setStyleSheet("color: #666;")
        result_group.addWidget(self.game_count_label)
        left_layout.addLayout(result_group)

        main_layout.addLayout(left_layout, stretch=1)

        right_main_layout = QHBoxLayout()
        right_main_layout.setContentsMargins(10, 0, 0, 0)
        right_main_layout.setStretch(0, 3)
        right_main_layout.setStretch(1, 1)

        original_right_layout = QVBoxLayout()

        video_container = QHBoxLayout()
        self.video_widget = QVideoWidget(self)
        self.video_widget.setFixedSize(300, 225)
        self.video_widget.setStyleSheet("""
            QVideoWidget {
                border: 3px solid #4CAF50;
                border-radius: 8px;
                background: #333;
            }
            QVideoWidget:hover {
                border-color: #45a049;
            }
        """)
        video_container.addWidget(self.video_widget, 0, Qt.AlignCenter)
        original_right_layout.addLayout(video_container)

        desc_group = QVBoxLayout()
        desc_group.setContentsMargins(0, 10, 0, 0)

        desc_group_box = QGroupBox("游戏描述 （如需修改内容，请直接在下方修改，然后点击右则的修改游戏描述）")
        desc_group_box.setStyleSheet("""
            QGroupBox {
                border: 1px solid #4CAF50;
                border-radius: 4px;
                margin-top: 8px;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        desc_group_layout = QVBoxLayout(desc_group_box)

        self.desc_text = QTextBrowser(self)
        self.desc_text.setReadOnly(False)
        self.desc_text.setMinimumHeight(100)
        self.desc_text.setStyleSheet("""
            QTextBrowser {
                border: none;
                padding: 5px;
                background: white;
                border-radius: 4px;
            }
        """)
        desc_group_layout.addWidget(self.desc_text)
        desc_group.addWidget(desc_group_box)

        self.status_bar = QTextBrowser(self)
        self.status_bar.setReadOnly(True)
        self.status_bar.setMinimumHeight(100)
        self.status_bar.setOpenLinks(False)
        self.status_bar.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #ccc;
                background: #f8f8f8;
                color: #666;
                margin-top: 8px;
                border-radius: 4px;
            }
        """)
        desc_group.addWidget(self.status_bar)

        original_right_layout.addLayout(desc_group)

        button_layout = QVBoxLayout()
        self.modify_button = QPushButton("修改游戏名称")
        self.modify_button.setFixedHeight(40)
        self.modify_button.clicked.connect(self.on_modify_name_clicked)
        button_layout.addWidget(self.modify_button)

        self.import_button = QPushButton("导入元数据")
        self.import_button.setFixedHeight(35)
        self.import_button.clicked.connect(self.import_metadata)
        button_layout.addWidget(self.import_button)

        self.modify_desc_button = QPushButton("修改游戏描述")
        self.modify_desc_button.setFixedHeight(35)
        self.modify_desc_button.clicked.connect(self.on_modify_desc_clicked)
        button_layout.addWidget(self.modify_desc_button)

        self.export_button = QPushButton("导出游戏列表")
        self.export_button.setFixedHeight(35)
        self.export_button.clicked.connect(self.export_game_list)
        button_layout.addWidget(self.export_button)

        export_dir_group = QGroupBox("导出目录")
        export_dir_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #4CAF50;
                border-radius: 4px;
                margin-top: 8px;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        export_dir_layout = QVBoxLayout(export_dir_group)

        self.dir_link_button = QToolButton()
        self.dir_link_button.setStyleSheet("""
            QToolButton {
                text-decoration: underline; 
                color: blue;
                text-align: left;
                padding: 2px;
                border: none;
                white-space: pre-wrap;
            }
        """)
        self.dir_link_button.setAutoRaise(True)
        self.dir_link_button.clicked.connect(self.open_export_dir)
        self.dir_link_button.setToolTip("点击打开文件所在目录")
        self.dir_link_button.setVisible(True)
        export_dir_layout.addWidget(self.dir_link_button)

        button_layout.addWidget(export_dir_group)

        # 新增视频播放控制组件
        self.video_control_group = QGroupBox("视频预览控制")
        self.video_control_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #4CAF50;
                border-radius: 4px;
                margin-top: 8px;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        video_control_layout = QVBoxLayout()

        self.enable_playback_check = QCheckBox("启用视频自动播放")
        self.enable_playback_check.setStyleSheet("""
            QCheckBox {
                spacing: 5px;
                color: #444;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
        """)
        self.enable_playback_check.stateChanged.connect(self.toggle_video_playback)
        video_control_layout.addWidget(self.enable_playback_check)

        self.playback_status = QLabel("当前状态：视频播放已禁用")
        self.playback_status.setStyleSheet("color: #666; font-size: 12px;")
        video_control_layout.addWidget(self.playback_status)

        self.video_control_group.setLayout(video_control_layout)
        button_layout.addWidget(self.video_control_group)

        button_layout.addItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Fixed))

        self.delete_button = QPushButton("删除游戏")
        self.delete_button.setFixedHeight(35)
        self.delete_button.setToolTip("支持多选，按住Ctrl键选择多个游戏")
        self.delete_button.clicked.connect(self.delete_game)
        button_layout.addWidget(self.delete_button)

        self.save_button = QPushButton("保存")
        self.save_button.setFixedHeight(70)
        self.save_button.clicked.connect(self.save_deletions)
        button_layout.addWidget(self.save_button)

        self.delete_warning = QLabel("删除游戏后需点击保存才能生效")
        self.delete_warning.setStyleSheet("color: red;")
        self.delete_warning.setWordWrap(True)
        self.delete_warning.setAlignment(Qt.AlignCenter)
        button_layout.addWidget(self.delete_warning)

        button_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        right_main_layout.addLayout(original_right_layout)
        right_main_layout.addLayout(button_layout)

        main_layout.addLayout(right_main_layout, stretch=1)

        self.setLayout(main_layout)
        self.setWindowTitle(f'隔壁老K游戏小组 - RetroBat 工具箱 测试版 v{CURRENT_VERSION}')
        self.setFixedSize(943, 809)
        self.center_window()
        self.show()

    def center_window(self):
        screen = QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) // 2,
                  (screen.height() - size.height()) // 2)

    def toggle_video_playback(self, state):
        """切换视频播放功能状态"""
        self.enable_video_playback = (state == Qt.Checked)
        status_text = "已启用" if self.enable_video_playback else "已禁用"
        self.playback_status.setText(f"当前状态：视频播放{status_text}")
        if not self.enable_video_playback and self.media_player:
            self.media_player.stop()
            self.status_signal.emit("已停止视频播放", False)

    def _handle_selection(self, row):
        if 0 <= row < len(self.sorted_results):
            path_part, name_part, desc_part, game_elem = self.sorted_results[row]
            video_path = self.name_video_mapping.get(path_part)

            if self.last_highlight is not None:
                old_item = self.result_list.item(self.last_highlight)
                if old_item is not None:
                    old_item.setBackground(QColor(255, 255, 255))
                    old_item.setForeground(QColor(0, 0, 0))

            current_item = self.result_list.item(row)
            current_item.setBackground(QColor(76, 175, 80))
            current_item.setForeground(QColor(255, 255, 255))
            self.last_highlight = row

            # 修改后的视频播放逻辑
            if self.enable_video_playback:
                if video_path and os.path.exists(video_path):
                    self.play_video(video_path)
                    self.status_signal.emit(f"正在播放：{name_part}", False)
                else:
                    self.status_signal.emit(f"未找到匹配视频：{path_part}", True)
            else:
                if video_path:
                    self.status_signal.emit("提示：请先启用视频预览功能", True)

            self.desc_text.setPlainText(desc_part if desc_part else "暂无游戏描述")

    # 其他方法保持原有实现
    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "选择最上级文件夹")
        if folder_path:
            self.category_list.clear()
            self.category_dirs = {}
            self.name_video_mapping = {}
            self.status_signal.emit("开始扫描目录...", False)
            threading.Thread(target=self.find_gamelist_xml, args=(folder_path,)).start()

    def find_gamelist_xml(self, folder_path):
        try:
            category_count = 0
            for root, dirs, files in os.walk(folder_path):
                depth = root[len(folder_path):].count(os.sep)
                if 1 <= depth <= 2 and 'gamelist.xml' in files:
                    category_count += 1
                    category_name = os.path.basename(root)
                    xml_path = os.path.join(root, 'gamelist.xml')
                    self.category_dirs[category_name] = xml_path

                    videos_dir = os.path.join(root, 'videos')
                    if os.path.exists(videos_dir):
                        for video_file in os.listdir(videos_dir):
                            video_path = os.path.join(videos_dir, video_file)
                            base_name = self.clean_filename(video_file)
                            with self.lock:
                                self.name_video_mapping[base_name] = video_path
                    QApplication.instance().postEvent(self, QListWidgetItemEvent(category_name))

            QApplication.instance().postEvent(self, QListWidgetItemEvent(f"COUNT_UPDATE|{category_count}"))
            self.status_signal.emit(f"扫描完成，共找到{category_count}个机种", False)
        except Exception as e:
            self.status_signal.emit(f"扫描过程中发生错误：{str(e)}", True)

    @pyqtSlot(str, bool)
    def _append_status(self, message, is_error):
        if message.startswith("游戏列表导出成功"):
            color = "#4CAF50"
        else:
            color = "#ff0000" if is_error else "#666"

        html = f'<div style="color:{color};margin:2px;">{message}</div>'
        self.status_bar.append(html)
        self.status_bar.moveCursor(QTextCursor.End)

    def clean_filename(self, filename):
        name = os.path.splitext(filename)[0].lower()
        video_exts = {'video', 'mp4', 'avi', 'mkv', 'mov', 'flv'}
        name = re.sub(r'-(?:' + '|'.join(video_exts) + r')$', '', name)
        return re.sub(r'[^\w\s]', '', name).strip().replace(' ', '_')

    def show_category_info(self, item):
        category_name = item.text()
        xml_path = self.category_dirs.get(category_name)
        if not xml_path:
            return

        try:
            parser = ET.XMLParser(remove_blank_text=True)
            self.current_tree = ET.parse(xml_path, parser)
            root = self.current_tree.getroot()
            self.current_xml_path = xml_path

            self.raw_results = []
            for game in root.findall('game'):
                path_element = game.find('path')
                path_text = self.clean_filename(
                    os.path.basename(path_element.text)
                ) if path_element is not None and path_element.text else ""

                name_element = game.find('name')
                name_text = name_element.text.strip() if name_element is not None and name_element.text else ""

                desc_element = game.find('desc')
                desc_text = desc_element.text.strip() if desc_element is not None and desc_element.text else ""

                self.raw_results.append((path_text, name_text, desc_text, game))

            self.sorted_results = self.raw_results.copy()
            self.update_display()
            self.status_signal.emit(f"已加载分类：{category_name}", False)
        except Exception as e:
            self.status_signal.emit(f"解析错误：{str(e)}", True)

    def update_display(self):
        self.result_list.clear()
        display_text = [name for _, name, _, _ in self.sorted_results]
        self.result_list.addItems(display_text)
        self.game_count_label.setText(f"当前列表拥有游戏：{len(self.sorted_results)}")

    def handle_item_click(self, item):
        row = self.result_list.row(item)
        self._handle_selection(row)

    def on_modify_name_clicked(self):
        current_row = self.result_list.currentRow()
        if current_row >= 0:
            _, old_name, _, game_elem = self.sorted_results[current_row]
            dialog = ModifyNameDialog(old_name, self)
            if dialog.exec_() == QDialog.Accepted:
                new_name = dialog.get_new_name()
                if new_name:
                    name_element = game_elem.find('name')
                    if name_element is not None:
                        name_element.text = new_name
                    else:
                        name_element = ET.SubElement(game_elem, 'name')
                        name_element.text = new_name

                    self.raw_results[current_row] = (
                        self.raw_results[current_row][0],
                        new_name,
                        self.raw_results[current_row][2],
                        game_elem
                    )
                    self.sorted_results[current_row] = (
                        self.sorted_results[current_row][0],
                        new_name,
                        self.sorted_results[current_row][2],
                        game_elem
                    )

                    self.save_xml()
                    self.update_display()
                    self.status_signal.emit(f"成功修改并保存：{old_name} → {new_name}", False)
                else:
                    QMessageBox.warning(self, "警告", "游戏名称不能为空！")
        else:
            QMessageBox.warning(self, "警告", "请先选择一个游戏！")

    def on_modify_desc_clicked(self):
        current_row = self.result_list.currentRow()
        if current_row >= 0:
            new_desc = self.desc_text.toPlainText().strip()
            _, old_name, old_desc, game_elem = self.sorted_results[current_row]

            if new_desc != old_desc:
                try:
                    desc_element = game_elem.find('desc')
                    if desc_element is not None:
                        desc_element.text = new_desc
                    else:
                        desc_element = ET.SubElement(game_elem, 'desc')
                        desc_element.text = new_desc

                    self.raw_results[current_row] = (
                        self.raw_results[current_row][0],
                        self.raw_results[current_row][1],
                        new_desc,
                        game_elem
                    )
                    self.sorted_results[current_row] = (
                        self.sorted_results[current_row][0],
                        self.sorted_results[current_row][1],
                        new_desc,
                        game_elem
                    )

                    self.save_xml()
                    self.status_signal.emit("游戏描述修改已保存", False)
                except Exception as e:
                    self.status_signal.emit(f"保存描述失败：{str(e)}", True)
            else:
                self.status_signal.emit("描述内容未修改", False)
        else:
            QMessageBox.warning(self, "警告", "请先选择一个游戏！")

    def save_xml(self):
        if self.current_tree is not None and self.current_xml_path:
            try:
                backup_dir = os.path.join(os.path.dirname(self.current_xml_path), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                base_name = os.path.basename(self.current_xml_path)

                backups = sorted(glob.glob(os.path.join(backup_dir, f"{base_name}.bak*")))
                while len(backups) >= 3:
                    os.remove(backups.pop(0))

                timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                shutil.copyfile(self.current_xml_path, 
                              os.path.join(backup_dir, f"{base_name}.bak{timestamp}"))

                self.current_tree.write(
                    self.current_xml_path,
                    encoding='utf-8',
                    xml_declaration=True,
                    pretty_print=True
                )
                self.status_signal.emit("配置文件保存成功（已创建备份）", False)
            except Exception as e:
                self.status_signal.emit(f"保存失败：{str(e)}", True)
        else:
            self.status_signal.emit("没有需要保存的配置文件", True)

    def play_video(self, video_path):
        if self.media_player:
            self.media_player.stop()
            self.media_player.deleteLater()

        self.media_player = QMediaPlayer(self, flags=QMediaPlayer.VideoSurface)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(video_path)))
        self.media_player.setVolume(50)
        self.media_player.play()

    def customEvent(self, event):
        if isinstance(event, QListWidgetItemEvent):
            if event.category_name.startswith("COUNT_UPDATE"):
                count = event.category_name.split("|")[1]
                self.category_count_label.setText(f"当前列表的机种数量：{count}")
            else:
                self.category_list.addItem(event.category_name)

    def closeEvent(self, event):
        if self.media_player:
            self.media_player.stop()
            self.media_player.deleteLater()
        event.accept()

    def export_game_list(self):
        if not self.sorted_results:
            self.status_signal.emit("当前没有游戏列表可供导出", True)
            return

    try:
        if self.current_xml_path:
            dir_path = os.path.dirname(self.current_xml_path)
            dir_path = os.path.normpath(dir_path)
            file_path = os.path.join(dir_path, "gamelist.txt")

            # 确保目录存在
            os.makedirs(dir_path, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as f:
                for _, name, _, _ in self.sorted_results:
                    f.write(name + '\n')

            # 加强空值检查
            if hasattr(self, 'dir_link_button') and self.dir_link_button is not None:
                self.dir_link_button.setText(f"导出目录：\n{dir_path}")
                self.dir_link_button.setVisible(True)
            else:
                self.status_signal.emit("导出路径显示控件未初始化", True)

            self.status_signal.emit(f"成功导出{len(self.sorted_results)}个游戏", False)
            self._last_export_path = dir_path

        else:
            self.status_signal.emit("无法确定XML文件路径", True)

    except Exception as e:
        self.status_signal.emit(f"导出失败：{str(e)}", True)
        
    else:
        self.status_signal.emit("没有找到当前XML文件路径，无法导出", True)

    def eventFilter(self, obj, event):
        if obj == self.status_bar and event.type() == QEvent.MouseButtonPress:
            anchor = self.status_bar.anchorAt(event.pos())
            if anchor:
                url = QUrl.fromUserInput(anchor)
                if url.isValid() and url.scheme() == 'file':
                    target_path = url.toLocalFile()
                    normalized_path = QDir(target_path).absolutePath()
                    self.open_export_dir(normalized_path)
                    return True
        return super().eventFilter(obj, event)

    def open_export_dir(self, file_path=None):
        target_path = file_path or self._last_export_path
        if not target_path or not os.path.exists(target_path):
            self.status_signal.emit("导出文件所在目录不存在，无法打开", True)
            return
        try:
            quoted_path = f'"{target_path}"' if ' ' in target_path else target_path
            subprocess.Popen(f'explorer {quoted_path}', shell=True)
        except Exception as e:
            self.status_signal.emit(f"打开目录失败：{str(e)}", True)

    def delete_matching_files(self, directory, base_name, depth=0):
        if depth > 2:
            return
        for entry in os.scandir(directory):
            try:
                if entry.is_dir():
                    if entry.name.startswith(base_name):
                        shutil.rmtree(entry.path)
                        self.status_signal.emit(f"已删除文件夹: {entry.path}", False)
                    else:
                        self.delete_matching_files(entry.path, base_name, depth + 1)
                else:
                    if entry.name.startswith(base_name):
                        os.remove(entry.path)
                        self.status_signal.emit(f"已删除文件: {entry.path}", False)
            except Exception as e:
                self.status_signal.emit(f"删除失败: {entry.path} - {str(e)}", True)

    def delete_game(self):
        selected_items = self.result_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要删除的游戏")
            return

        # 删除确认对话框代码已移除
        rows = sorted([self.result_list.row(item) for item in selected_items], reverse=True)
        for row in rows:
            if 0 <= row < len(self.sorted_results):
                _, name, _, game_elem = self.sorted_results[row]
                path_element = game_elem.find('path')
                if path_element is not None:
                    rom_path = path_element.text
                    base_name = os.path.splitext(os.path.basename(rom_path))[0]
                    base_name = re.sub(r'[^\w\s]', '', base_name).strip()
                    self.deleted_games.append((rom_path, base_name))

                    del self.sorted_results[row]
                    del self.raw_results[row]

        self.update_display()
        self.status_signal.emit(f"已标记删除 {len(rows)} 个游戏（点击保存生效）", False)

    def save_deletions(self):
        if not self.deleted_games:
            self.status_signal.emit("没有需要删除的游戏", True)
            return

        xml_dir = os.path.dirname(self.current_xml_path)
        success_count = 0
        error_count = 0

        for rom_path, base_name in self.deleted_games:
            try:
                full_rom_path = os.path.join(xml_dir, rom_path)
                if os.path.exists(full_rom_path):
                    os.remove(full_rom_path)
                    success_count += 1
                else:
                    self.status_signal.emit(f"文件不存在: {full_rom_path}", True)
                    error_count += 1

                self.delete_matching_files(xml_dir, base_name)

                video_path = os.path.join(xml_dir, 'videos', f"{base_name}.mp4")
                if self.media_player and os.path.exists(video_path):
                    current_media = self.media_player.media().canonicalUrl().toLocalFile()
                    if current_media == video_path:
                        self.media_player.stop()
                        self.media_player.deleteLater()
                        self.media_player = None
                        time.sleep(0.5)

                max_retries = 3
                for retry in range(max_retries):
                    try:
                        if os.path.exists(video_path):
                            os.remove(video_path)
                            success_count += 1
                            break
                    except Exception as e:
                        if retry < max_retries - 1:
                            time.sleep(0.2)
                        else:
                            self.status_signal.emit(f"删除视频失败: {video_path} - {str(e)}", True)
                            error_count += 1

                root = self.current_tree.getroot()
                for game in root.findall('game'):
                    if game.find('path').text == rom_path:
                        root.remove(game)
                        break
                self.current_tree.write(self.current_xml_path, encoding='utf-8', xml_declaration=True, pretty_print=True)

            except Exception as e:
                self.status_signal.emit(f"删除失败: {str(e)}", True)
                error_count += 1

        self.deleted_games = []
        self.status_signal.emit(f"操作完成: 成功删除{success_count}项，失败{error_count}项", False)
        self.show_category_info(self.category_list.currentItem())

    def on_search_text_changed(self):
        self.search_timer.start(300)

    def filter_games(self):
        keyword = self.search_box.text().lower()
        if not keyword:
            self.sorted_results = self.raw_results.copy()
        else:
            self.sorted_results = [
                item for item in self.raw_results 
                if keyword in item[1].lower() or keyword in item[2].lower()
            ]
        self.update_display()

    def import_metadata(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择导入文件", 
            "", "CSV文件 (*.csv);;Excel文件 (*.xlsx)"
        )

        if not file_path:
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[1:]:
                    parts = line.strip().split(',')
                    if len(parts) >= 3:
                        rom_name, game_name, description = parts[0], parts[1], parts[2]
                        for i, (r_name, _, _, elem) in enumerate(self.raw_results):
                            if r_name == rom_name:
                                if elem.find('name') is not None:
                                    elem.find('name').text = game_name
                                else:
                                    ET.SubElement(elem, 'name').text = game_name

                                if elem.find('desc') is not None:
                                    elem.find('desc').text = description
                                else:
                                    ET.SubElement(elem, 'desc').text = description

                                self.raw_results[i] = (r_name, game_name, description, elem)
            self.save_xml()
            self.filter_games()
            self.status_signal.emit("成功导入并更新游戏数据", False)
        except Exception as e:
            self.status_signal.emit(f"导入失败：{str(e)}", True)

    def check_update(self):
        def update_check_finished(reply):
            try:
                data = reply.readAll().data().decode()
                latest_ver = re.search(r'"tag_name":\s*"([\d.]+)"', data).group(1)
                if latest_ver > CURRENT_VERSION:
                    self.show_update_notification(latest_ver)
            except Exception as e:
                print(f"更新检查失败: {str(e)}")
            finally:
                reply.deleteLater()

        manager = QNetworkAccessManager()
        url = QUrl("https://api.github.com/repos/wincyd/retrobat-tool/releases/latest")
        request = QNetworkRequest(url)
        reply = manager.get(request)
        reply.finished.connect(lambda: update_check_finished(reply))

    def show_update_notification(self, new_version):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText(f"发现新版本 {new_version} 可用！")
        msg.setInformativeText("是否立即前往下载页面？")
        msg.setWindowTitle("软件更新")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        ret = msg.exec_()

        if ret == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl("https://your-download-page.com"))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = XMLNameExtractor()
    sys.exit(app.exec_())

