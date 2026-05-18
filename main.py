# -*- coding: utf-8 -*-
"""
WaterMarkGenerator - GUI Main Application
照片水印生成器 - PyQt5 GUI主程序
"""

import sys
import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QSlider, QSpinBox, QCheckBox, QComboBox,
    QFileDialog, QMessageBox, QProgressBar, QGroupBox, QLineEdit,
    QSplitter, QScrollArea, QFrame, QDoubleSpinBox, QTextEdit,
    QSizePolicy, QTabWidget, QDialog, QTextBrowser,
    QColorDialog, QRadioButton, QButtonGroup
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QPoint, QTimer
from PyQt5.QtGui import QPixmap, QImage, QFont, QFontDatabase, QCursor, QIcon

from PIL import Image
from watermark_core import (
    get_exif_data, format_exif_for_watermark,
    add_watermark_to_image, generate_preview
)


def _get_resource_path(filename):
    """获取资源文件路径（兼容开发环境和 PyInstaller 打包环境）"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


class WatermarkLabel(QLabel):
    """支持拖动设置水印位置的自定义Label"""
    position_changed = pyqtSignal(float, float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ddd;")
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self.dragging = False
        self.watermark_rel_x = 0.05
        self.watermark_rel_y = 0.05
        self.current_image = None
        self.preview_pixmap = None
    
    def set_preview(self, pixmap, rel_x=0.05, rel_y=0.05):
        self.preview_pixmap = pixmap
        self.watermark_rel_x = rel_x
        self.watermark_rel_y = rel_y
        self._update_display()
    
    def _update_display(self):
        if self.preview_pixmap:
            scaled = self.preview_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.setPixmap(scaled)
        else:
            self.setText("请选择图片预览")
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.preview_pixmap:
            self.dragging = True
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            self._update_position_from_mouse(event.pos())
    
    def mouseMoveEvent(self, event):
        if self.dragging:
            self._update_position_from_mouse(event.pos())
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.setCursor(QCursor(Qt.OpenHandCursor))
    
    def _update_position_from_mouse(self, pos):
        if not self.pixmap():
            return
        # 计算在pixmap中的相对位置
        pixmap = self.pixmap()
        label_w = self.width()
        label_h = self.height()
        px = pixmap.width()
        py = pixmap.height()
        
        # 计算pixmap在label中的偏移（居中）
        offset_x = (label_w - px) // 2
        offset_y = (label_h - py) // 2
        
        rel_x = (pos.x() - offset_x) / px if px > 0 else 0
        rel_y = (pos.y() - offset_y) / py if py > 0 else 0
        
        # 限制在0-1范围内
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        
        self.watermark_rel_x = rel_x
        self.watermark_rel_y = rel_y
        self.position_changed.emit(rel_x, rel_y)


class BatchWorker(QThread):
    """批量处理后台线程"""
    progress = pyqtSignal(int, int, str)  # 当前, 总数, 当前文件名
    finished_signal = pyqtSignal(bool, str)  # 是否成功, 消息
    
    def __init__(self, file_list, output_dir, watermark_texts_list, params):
        super().__init__()
        self.file_list = file_list
        self.output_dir = output_dir
        self.watermark_texts_list = watermark_texts_list
        self.params = params
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def run(self):
        try:
            total = len(self.file_list)
            for i, (file_path, watermark_texts) in enumerate(zip(self.file_list, self.watermark_texts_list)):
                if self.cancelled:
                    self.finished_signal.emit(False, "已取消处理")
                    return
                
                self.progress.emit(i + 1, total, os.path.basename(file_path))
                
                # 生成输出路径
                ext = os.path.splitext(file_path)[1]
                out_name = f"{os.path.splitext(os.path.basename(file_path))[0]}_watermarked{ext}"
                out_path = os.path.join(self.output_dir, out_name)
                
                try:
                    add_watermark_to_image(file_path, out_path, watermark_texts, self.params)
                except Exception as e:
                    print(f"处理失败 {file_path}: {e}")
                    continue
            
            self.finished_signal.emit(True, f"成功处理 {total} 张图片")
        except Exception as e:
            self.finished_signal.emit(False, f"处理出错: {str(e)}")


class MainWindow(QMainWindow):
    def _set_window_icon(self):
        """设置窗口图标"""
        icon_path = _get_resource_path('app_icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def __init__(self):
        super().__init__()
        self.setWindowTitle("照片水印生成器")
        self._set_window_icon()
        self.setMinimumSize(1200, 800)
        self.resize(1600, 1050)
        
        # 数据
        self.current_files = []  # 选中的文件列表
        self.current_preview_file = None  # 当前预览的文件
        self.preview_params = {
            'font_path': None,
            'font_path_line1': None,
            'font_path_line2': None,
            'font_path_line3': None,
            'line1_size': 48,
            'line2_size': 20,
            'line3_size': 20,
            'opacity': 200,
            'italic': False,
            'italic_line1': False,
            'italic_line2': False,
            'italic_line3': False,
            'line_spacing': 10,
            'spacing_1_2': 10,
            'spacing_2_3': 10,
            'text_color': (255, 255, 255),
            'use_alpha_logo': False,
            'alpha_logo_color': 'white',
            'logo_scale': 1.0,
            'logo_opacity': 200,
            'position': (0.05, 0.05),
            'is_relative_pos': True,
            'padding': 20,
        }
        self.worker = None
        self.is_dragging = False
        
        # 延迟刷新定时器：参数变化后 250ms 才刷新预览，避免频繁重绘导致卡顿
        self.preview_timer = QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._refresh_preview)
        
        self._init_ui()
        self._scan_fonts()
    
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # === 左侧：预览区 ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # 预览标题
        preview_title = QLabel("水印预览（可拖动调整位置）")
        preview_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        left_layout.addWidget(preview_title)
        
        # 预览图
        self.preview_label = WatermarkLabel()
        self.preview_label.position_changed.connect(self._on_position_dragged)
        left_layout.addWidget(self.preview_label, stretch=1)
        
        # 当前文件信息
        self.file_info_label = QLabel("未选择文件")
        self.file_info_label.setStyleSheet("color: #666; padding: 5px;")
        left_layout.addWidget(self.file_info_label)
        
        # === 右侧：控制面板 ===
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMaximumWidth(520)
        right_scroll.setMinimumWidth(500)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        right_widget = QWidget()
        right_widget.setMinimumWidth(480)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setAlignment(Qt.AlignTop)
        
        # --- 文件选择区 ---
        file_group = QGroupBox("文件选择")
        file_layout = QVBoxLayout(file_group)
        
        file_btn_layout = QHBoxLayout()
        self.btn_select_files = QPushButton("选择图片")
        self.btn_select_files.setToolTip("选择一个或多个图片文件")
        self.btn_select_files.clicked.connect(self._select_files)
        
        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_select_folder.setToolTip("选择包含图片的文件夹")
        self.btn_select_folder.clicked.connect(self._select_folder)
        
        self.btn_clear_files = QPushButton("清空")
        self.btn_clear_files.clicked.connect(self._clear_files)
        
        file_btn_layout.addWidget(self.btn_select_files)
        file_btn_layout.addWidget(self.btn_select_folder)
        file_btn_layout.addWidget(self.btn_clear_files)
        file_layout.addLayout(file_btn_layout)
        
        self.file_list_display = QTextEdit()
        self.file_list_display.setMaximumHeight(80)
        self.file_list_display.setReadOnly(True)
        self.file_list_display.setPlaceholderText("已选择的文件将显示在这里...")
        file_layout.addWidget(self.file_list_display)
        
        self.file_count_label = QLabel("已选择: 0 个文件")
        file_layout.addWidget(self.file_count_label)
        
        right_layout.addWidget(file_group)
        
        # --- 水印内容预览 ---
        content_group = QGroupBox("水印内容预览")
        content_layout = QVBoxLayout(content_group)
        
        self.watermark_preview_text = QTextEdit()
        self.watermark_preview_text.setMinimumHeight(100)
        self.watermark_preview_text.setMaximumHeight(150)
        self.watermark_preview_text.setReadOnly(True)
        self.watermark_preview_text.setPlaceholderText("选择图片后显示EXIF水印内容...")
        self.watermark_preview_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.watermark_preview_text)
        
        right_layout.addWidget(content_group)
        
        # --- 字体设置 ---
        font_group = QGroupBox("字体设置（每行可独立设置）")
        font_layout = QVBoxLayout(font_group)
        font_layout.setSpacing(6)
        
        def create_font_row(layout, label_text):
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            combo = QComboBox()
            combo.setMinimumWidth(160)
            combo.currentTextChanged.connect(self._on_font_changed)
            chk = QCheckBox("斜体")
            chk.stateChanged.connect(self._on_param_changed)
            row.addWidget(combo, stretch=1)
            row.addWidget(chk)
            layout.addLayout(row)
            return combo, chk
        
        self.font_combo_line1, self.chk_italic_line1 = create_font_row(font_layout, "第一行:")
        self.font_combo_line2, self.chk_italic_line2 = create_font_row(font_layout, "第二行:")
        self.font_combo_line3, self.chk_italic_line3 = create_font_row(font_layout, "第三行:")
        
        browse_layout = QHBoxLayout()
        browse_layout.addStretch()
        self.btn_browse_font = QPushButton("浏览字体文件...")
        self.btn_browse_font.clicked.connect(self._browse_font)
        browse_layout.addWidget(self.btn_browse_font)
        font_layout.addLayout(browse_layout)
        
        right_layout.addWidget(font_group)
        
        # --- 字号设置 ---
        size_group = QGroupBox("字号设置")
        size_layout = QGridLayout(size_group)
        size_layout.setSpacing(8)
        
        def add_slider_spin_row(layout, row, label, min_val, max_val, default):
            layout.addWidget(QLabel(label), row, 0)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(min_val, max_val)
            slider.setValue(default)
            spin = QSpinBox()
            spin.setRange(min_val, max_val)
            spin.setValue(default)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            spin.valueChanged.connect(self._on_param_changed)
            slider.sliderPressed.connect(self._on_slider_pressed)
            slider.sliderReleased.connect(self._on_slider_released)
            layout.addWidget(slider, row, 1)
            layout.addWidget(spin, row, 2)
            return slider, spin
        
        self.slider_line1_size, self.spin_line1_size = add_slider_spin_row(
            size_layout, 0, "第一行(型号):", 8, 600, 48)
        self.slider_line2_size, self.spin_line2_size = add_slider_spin_row(
            size_layout, 1, "第二行(参数):", 8, 300, 20)
        self.slider_line3_size, self.spin_line3_size = add_slider_spin_row(
            size_layout, 2, "第三行(参数):", 8, 300, 20)
        self.slider_spacing_1_2, self.spin_spacing_1_2 = add_slider_spin_row(
            size_layout, 3, "第一二行间距:", 0, 500, 10)
        self.slider_spacing_2_3, self.spin_spacing_2_3 = add_slider_spin_row(
            size_layout, 4, "第二三行间距:", 0, 500, 10)
        self.slider_padding, self.spin_padding = add_slider_spin_row(
            size_layout, 5, "边距:", 0, 200, 20)
        
        right_layout.addWidget(size_group)
        
        # --- 样式设置 ---
        style_group = QGroupBox("样式设置")
        style_layout = QGridLayout(style_group)
        style_layout.setSpacing(8)
        
        style_layout.addWidget(QLabel("透明度:"), 0, 0)
        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(0, 255)
        self.slider_opacity.setValue(200)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        self.slider_opacity.sliderPressed.connect(self._on_slider_pressed)
        self.slider_opacity.sliderReleased.connect(self._on_slider_released)
        style_layout.addWidget(self.slider_opacity, 0, 1)
        self.lbl_opacity = QLabel("200")
        style_layout.addWidget(self.lbl_opacity, 0, 2)
        
        style_layout.addWidget(QLabel("水平位置:"), 1, 0)
        self.slider_pos_x = QSlider(Qt.Horizontal)
        self.slider_pos_x.setRange(0, 100)
        self.slider_pos_x.setValue(5)
        self.slider_pos_x.valueChanged.connect(self._on_pos_changed)
        self.slider_pos_x.sliderPressed.connect(self._on_slider_pressed)
        self.slider_pos_x.sliderReleased.connect(self._on_slider_released)
        style_layout.addWidget(self.slider_pos_x, 1, 1)
        self.lbl_pos_x = QLabel("5%")
        style_layout.addWidget(self.lbl_pos_x, 1, 2)
        
        style_layout.addWidget(QLabel("垂直位置:"), 2, 0)
        self.slider_pos_y = QSlider(Qt.Horizontal)
        self.slider_pos_y.setRange(0, 100)
        self.slider_pos_y.setValue(5)
        self.slider_pos_y.valueChanged.connect(self._on_pos_changed)
        self.slider_pos_y.sliderPressed.connect(self._on_slider_pressed)
        self.slider_pos_y.sliderReleased.connect(self._on_slider_released)
        style_layout.addWidget(self.slider_pos_y, 2, 1)
        self.lbl_pos_y = QLabel("5%")
        style_layout.addWidget(self.lbl_pos_y, 2, 2)
        
        style_layout.addWidget(QLabel("水印颜色:"), 3, 0)
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(50, 26)
        self.btn_color.setStyleSheet("background-color: #ffffff; border: 1px solid #888; border-radius: 4px;")
        self.btn_color.setToolTip("点击选择水印颜色")
        self.btn_color.clicked.connect(self._pick_color)
        style_layout.addWidget(self.btn_color, 3, 1)
        
        style_layout.addWidget(QLabel("α LOGO:"), 4, 0)
        logo_layout = QHBoxLayout()
        self.chk_alpha_logo = QCheckBox("启用")
        self.chk_alpha_logo.setToolTip("将 Sony α 中的 α 替换为官方矢量 LOGO")
        self.chk_alpha_logo.stateChanged.connect(self._on_param_changed)
        self.radio_logo_white = QRadioButton("白色")
        self.radio_logo_orange = QRadioButton("橙红")
        self.radio_logo_white.setChecked(True)
        self.logo_color_group = QButtonGroup()
        self.logo_color_group.addButton(self.radio_logo_white)
        self.logo_color_group.addButton(self.radio_logo_orange)
        self.radio_logo_white.toggled.connect(self._on_param_changed)
        self.radio_logo_orange.toggled.connect(self._on_param_changed)
        logo_layout.addWidget(self.chk_alpha_logo)
        logo_layout.addWidget(self.radio_logo_white)
        logo_layout.addWidget(self.radio_logo_orange)
        logo_layout.addStretch()
        style_layout.addLayout(logo_layout, 4, 1)
        
        style_layout.addWidget(QLabel("LOGO缩放:"), 5, 0)
        self.slider_logo_scale = QSlider(Qt.Horizontal)
        self.slider_logo_scale.setRange(20, 300)
        self.slider_logo_scale.setValue(100)
        self.slider_logo_scale.valueChanged.connect(self._on_logo_scale_changed)
        self.slider_logo_scale.sliderPressed.connect(self._on_slider_pressed)
        self.slider_logo_scale.sliderReleased.connect(self._on_slider_released)
        style_layout.addWidget(self.slider_logo_scale, 5, 1)
        self.lbl_logo_scale = QLabel("100%")
        style_layout.addWidget(self.lbl_logo_scale, 5, 2)
        
        style_layout.addWidget(QLabel("LOGO透明度:"), 6, 0)
        self.slider_logo_opacity = QSlider(Qt.Horizontal)
        self.slider_logo_opacity.setRange(0, 255)
        self.slider_logo_opacity.setValue(200)
        self.slider_logo_opacity.valueChanged.connect(self._on_logo_opacity_changed)
        self.slider_logo_opacity.sliderPressed.connect(self._on_slider_pressed)
        self.slider_logo_opacity.sliderReleased.connect(self._on_slider_released)
        style_layout.addWidget(self.slider_logo_opacity, 6, 1)
        self.lbl_logo_opacity = QLabel("200")
        style_layout.addWidget(self.lbl_logo_opacity, 6, 2)
        
        right_layout.addWidget(style_group)
        
        # --- 进度条 ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)
        
        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        right_layout.addWidget(self.progress_label)
        
        # --- 操作按钮 ---
        btn_layout = QHBoxLayout()
        
        self.btn_preview = QPushButton("刷新预览")
        self.btn_preview.clicked.connect(self._refresh_preview)
        self.btn_preview.setStyleSheet("padding: 8px 16px;")
        
        self.btn_process = QPushButton("开始处理")
        self.btn_process.clicked.connect(self._start_processing)
        self.btn_process.setStyleSheet(
            "padding: 8px 16px; background-color: #4CAF50; color: white; font-weight: bold;"
        )
        
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._cancel_processing)
        self.btn_cancel.setVisible(False)
        
        btn_layout.addWidget(self.btn_preview)
        btn_layout.addWidget(self.btn_process)
        btn_layout.addWidget(self.btn_cancel)
        right_layout.addLayout(btn_layout)
        
        # --- 使用说明快捷入口 ---
        help_layout = QHBoxLayout()
        self.btn_help = QPushButton("查看使用说明")
        self.btn_help.clicked.connect(self._show_help)
        help_layout.addWidget(self.btn_help)
        help_layout.addStretch()
        right_layout.addLayout(help_layout)
        
        right_scroll.setWidget(right_widget)
        
        # === 添加分割器 ===
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_scroll)
        splitter.setSizes([800, 400])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        main_layout.addWidget(splitter)
    
    def _scan_fonts(self):
        """扫描系统字体"""
        self.font_paths = {}
        
        # 常见中文字体
        common_fonts = [
            ("微软雅黑", "C:/Windows/Fonts/msyh.ttc"),
            ("微软雅黑粗体", "C:/Windows/Fonts/msyhbd.ttc"),
            ("黑体", "C:/Windows/Fonts/simhei.ttf"),
            ("宋体", "C:/Windows/Fonts/simsun.ttc"),
            ("Arial", "C:/Windows/Fonts/arial.ttf"),
            ("Arial Bold", "C:/Windows/Fonts/arialbd.ttf"),
            ("Calibri", "C:/Windows/Fonts/calibri.ttf"),
        ]
        
        for name, path in common_fonts:
            if os.path.exists(path):
                self.font_paths[name] = path
        
        # 使用 Qt 字体数据库扫描
        font_db = QFontDatabase()
        for family in font_db.families():
            if font_db.styles(family):
                if family not in self.font_paths:
                    self.font_paths[family] = None
        
        # 为三个字体下拉框填充数据
        for combo in [self.font_combo_line1, self.font_combo_line2, self.font_combo_line3]:
            combo.clear()
            for name in self.font_paths:
                combo.addItem(name)
            if combo.count() > 0:
                combo.setCurrentIndex(0)
        
        self._on_font_changed()
    
    def _on_slider_pressed(self):
        """开始拖动滑条：切换到低分辨率预览模式"""
        self.is_dragging = True
        self.preview_timer.stop()
    
    def _on_slider_released(self):
        """释放滑条：立即高分辨率刷新"""
        self.is_dragging = False
        self.preview_timer.stop()
        self._refresh_preview()
    
    def _delayed_refresh(self):
        """延迟刷新预览，拖动时限制约60fps"""
        self.preview_timer.stop()
        if self.is_dragging:
            self.preview_timer.start(17)   # ~60fps 低分辨率实时刷新
        else:
            self.preview_timer.start(250)  # 静止时250ms高分辨率刷新
    
    def _on_font_changed(self, font_name=None):
        """字体改变时更新参数"""
        self.preview_params['font_path_line1'] = self.font_paths.get(self.font_combo_line1.currentText(), None)
        self.preview_params['font_path_line2'] = self.font_paths.get(self.font_combo_line2.currentText(), None)
        self.preview_params['font_path_line3'] = self.font_paths.get(self.font_combo_line3.currentText(), None)
        self._delayed_refresh()
    
    def _browse_font(self):
        """浏览字体文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择字体文件", "", "字体文件 (*.ttf *.ttc *.otf)"
        )
        if file_path:
            name = os.path.basename(file_path)
            self.font_paths[name] = file_path
            for combo in [self.font_combo_line1, self.font_combo_line2, self.font_combo_line3]:
                combo.addItem(name)
    
    def _pick_color(self):
        """选择水印颜色"""
        current = self.preview_params.get('text_color', (255, 255, 255))
        initial = QColor(current[0], current[1], current[2])
        color = QColorDialog.getColor(initial, self, "选择水印颜色")
        if color.isValid():
            self.preview_params['text_color'] = (color.red(), color.green(), color.blue())
            self.btn_color.setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid #888; border-radius: 4px;"
            )
            self._delayed_refresh()
    
    def _on_logo_scale_changed(self, value):
        self.lbl_logo_scale.setText(f"{value}%")
        self.preview_params['logo_scale'] = value / 100.0
        self._delayed_refresh()
    
    def _on_logo_opacity_changed(self, value):
        self.lbl_logo_opacity.setText(str(value))
        self.preview_params['logo_opacity'] = value
        self._delayed_refresh()
    
    def _on_param_changed(self):
        """参数改变时更新"""
        self.preview_params['italic_line1'] = self.chk_italic_line1.isChecked()
        self.preview_params['italic_line2'] = self.chk_italic_line2.isChecked()
        self.preview_params['italic_line3'] = self.chk_italic_line3.isChecked()
        self.preview_params['line1_size'] = self.spin_line1_size.value()
        self.preview_params['line2_size'] = self.spin_line2_size.value()
        self.preview_params['line3_size'] = self.spin_line3_size.value()
        self.preview_params['spacing_1_2'] = self.spin_spacing_1_2.value()
        self.preview_params['spacing_2_3'] = self.spin_spacing_2_3.value()
        self.preview_params['padding'] = self.spin_padding.value()
        self.preview_params['use_alpha_logo'] = self.chk_alpha_logo.isChecked()
        self.preview_params['alpha_logo_color'] = 'orange' if self.radio_logo_orange.isChecked() else 'white'
        self.preview_params['logo_scale'] = self.slider_logo_scale.value() / 100.0
        self.preview_params['logo_opacity'] = self.slider_logo_opacity.value()
        self._delayed_refresh()
    
    def _on_opacity_changed(self, value):
        self.lbl_opacity.setText(str(value))
        self.preview_params['opacity'] = value
        self._delayed_refresh()
    
    def _on_pos_changed(self):
        x = self.slider_pos_x.value() / 100.0
        y = self.slider_pos_y.value() / 100.0
        self.lbl_pos_x.setText(f"{int(x * 100)}%")
        self.lbl_pos_y.setText(f"{int(y * 100)}%")
        self.preview_params['position'] = (x, y)
        self.preview_params['is_relative_pos'] = True
        self._delayed_refresh()
    
    def _on_position_dragged(self, rel_x, rel_y):
        """从预览图拖动更新位置"""
        self.slider_pos_x.blockSignals(True)
        self.slider_pos_y.blockSignals(True)
        self.slider_pos_x.setValue(int(rel_x * 100))
        self.slider_pos_y.setValue(int(rel_y * 100))
        self.slider_pos_x.blockSignals(False)
        self.slider_pos_y.blockSignals(False)
        self.lbl_pos_x.setText(f"{int(rel_x * 100)}%")
        self.lbl_pos_y.setText(f"{int(rel_y * 100)}%")
        self.preview_params['position'] = (rel_x, rel_y)
        self.preview_params['is_relative_pos'] = True
        self._refresh_preview()
    
    def _select_files(self):
        """选择图片文件"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp);;所有文件 (*.*)"
        )
        if files:
            self.current_files = files
            self._update_file_display()
            self.current_preview_file = files[0]
            self._refresh_preview()
    
    def _select_folder(self):
        """选择文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择包含图片的文件夹")
        if folder:
            exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}
            files = []
            for f in os.listdir(folder):
                if os.path.splitext(f.lower())[1] in exts:
                    files.append(os.path.join(folder, f))
            if files:
                self.current_files = sorted(files)
                self._update_file_display()
                self.current_preview_file = files[0]
                self._refresh_preview()
            else:
                QMessageBox.information(self, "提示", "所选文件夹中没有找到图片文件。")
    
    def _clear_files(self):
        """清空文件列表"""
        self.current_files = []
        self.current_preview_file = None
        self._update_file_display()
        self.preview_label.set_preview(None)
        self.file_info_label.setText("未选择文件")
        self.watermark_preview_text.clear()
    
    def _update_file_display(self):
        """更新文件列表显示"""
        text = "\n".join(self.current_files)
        self.file_list_display.setText(text)
        self.file_count_label.setText(f"已选择: {len(self.current_files)} 个文件")
    
    def _refresh_preview(self):
        """刷新预览"""
        if not self.current_preview_file or not os.path.exists(self.current_preview_file):
            return
        
        try:
            # 读取EXIF
            exif = get_exif_data(self.current_preview_file)
            watermark_texts = format_exif_for_watermark(exif)
            
            # 更新水印内容显示
            self.watermark_preview_text.setText(
                f"第一行: {watermark_texts[0]}\n"
                f"第二行: {watermark_texts[1]}\n"
                f"第三行: {watermark_texts[2]}"
            )
            
            # 更新文件信息
            img = Image.open(self.current_preview_file)
            self.file_info_label.setText(
                f"{os.path.basename(self.current_preview_file)} | "
                f"{img.size[0]}x{img.size[1]} | {img.format or 'Unknown'}"
            )
            
            # 生成预览（根据预览区尺寸生成足够大的预览图，避免模糊）
            if self.is_dragging:
                pw = min(self.preview_label.width(), 1280)
                ph = min(self.preview_label.height(), 1280)
            else:
                pw = min(self.preview_label.width() * 2, 3200)
                ph = min(self.preview_label.height() * 2, 3200)
            preview = generate_preview(self.current_preview_file, watermark_texts, self.preview_params, preview_size=(pw, ph))
            
            # 转换为 QPixmap
            if preview.mode == 'RGBA':
                data = preview.tobytes('raw', 'RGBA')
                qimage = QImage(data, preview.width, preview.height, QImage.Format_RGBA8888)
            else:
                preview_rgb = preview.convert('RGB')
                data = preview_rgb.tobytes('raw', 'RGB')
                qimage = QImage(data, preview.width, preview.height, QImage.Format_RGB888)
            
            pixmap = QPixmap.fromImage(qimage)
            
            # 更新预览标签
            x, y = self.preview_params['position']
            self.preview_label.set_preview(pixmap, x, y)
            
        except Exception as e:
            self.file_info_label.setText(f"预览失败: {str(e)}")
    
    def _start_processing(self):
        """开始批量处理"""
        if not self.current_files:
            QMessageBox.warning(self, "警告", "请先选择图片文件！")
            return
        
        # 选择输出目录
        output_dir = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if not output_dir:
            return
        
        # 准备水印文字列表
        watermark_texts_list = []
        for file_path in self.current_files:
            exif = get_exif_data(file_path)
            watermark_texts_list.append(format_exif_for_watermark(exif))
        
        # 禁用按钮
        self.btn_process.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_bar.setMaximum(len(self.current_files))
        self.progress_bar.setValue(0)
        
        # 启动后台线程
        self.worker = BatchWorker(self.current_files, output_dir, watermark_texts_list, self.preview_params)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()
    
    def _cancel_processing(self):
        """取消处理"""
        if self.worker:
            self.worker.cancel()
    
    def _on_progress(self, current, total, filename):
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"处理中: {current}/{total} - {filename}")
    
    def _on_finished(self, success, message):
        self.btn_process.setEnabled(True)
        self.btn_preview.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.warning(self, "提示", message)
    
    def _show_help(self):
        """显示使用说明（自定义矩形对话框）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("使用说明")
        dialog.setMinimumSize(700, 550)
        dialog.resize(800, 600)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        text_browser = QTextBrowser()
        text_browser.setOpenExternalLinks(True)
        text_browser.setHtml("""
        <h2>照片水印生成器 - 使用说明</h2>
        
        <h3>基本功能</h3>
        <p>本工具可以读取照片的EXIF信息，将相机型号、镜头型号和拍摄参数（光圈、快门、ISO、焦距）
        以优雅的文字水印形式添加到照片上。</p>
        
        <h3>使用步骤</h3>
        <ol>
        <li><b>选择图片</b>：点击"选择图片"按钮选择单个或多个文件，或点击"选择文件夹"批量导入。</li>
        <li><b>调整水印样式</b>：在右侧面板调节字体、大小、透明度、位置等参数。</li>
        <li><b>预览效果</b>：左侧预览区实时显示水印效果，也可以直接在预览图上拖动调整位置。</li>
        <li><b>开始处理</b>：点击"开始处理"按钮，选择输出文件夹，即可批量生成带水印的照片。</li>
        </ol>
        
        <h3>参数说明</h3>
        <ul>
        <li><b>字体</b>：可分别为三行文字设置不同的字体和斜体效果。</li>
        <li><b>字号</b>：分别设置三行水印文字的大小。第一行是相机型号，通常较大。</li>
        <li><b>行间距</b>：可分别调整第一二行、第二三行之间的间距。</li>
        <li><b>透明度</b>：0-255，值越大越不透明。建议 180-220。</li>
        <li><b>位置</b>：可用滑条调节，也可直接在预览图上拖动水印到想要的位置。</li>
        <li><b>边距</b>：水印文字到定位点的内边距。</li>
        </ul>
        
        <h3>注意事项</h3>
        <ul>
        <li>处理后的图片会保留原始EXIF信息。</li>
        <li>如果照片没有EXIF信息，水印将显示"Unknown Camera"。</li>
        <li>输出文件名会在原文件名后添加 "_watermarked" 后缀。</li>
        <li>支持 JPG、PNG、BMP、TIFF、WebP 等常见格式。</li>
        </ul>
        
        <h3>支持信息</h3>
        <p>程序使用 Python + PyQt5 + Pillow 开发，在 Windows 环境下运行。</p>
        """)
        layout.addWidget(text_browser, stretch=1)
        
        btn_ok = QPushButton("确定")
        btn_ok.setFixedWidth(100)
        btn_ok.clicked.connect(dialog.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        
        dialog.exec_()



def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # 设置全局样式
    app.setStyleSheet("""
        QMainWindow {
            background-color: #f5f5f5;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #cccccc;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 8px;
            padding: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QPushButton {
            background-color: #e0e0e0;
            border: 1px solid #aaaaaa;
            border-radius: 4px;
            padding: 5px 12px;
        }
        QPushButton:hover {
            background-color: #d0d0d0;
        }
        QPushButton:pressed {
            background-color: #c0c0c0;
        }
        QSlider::groove:horizontal {
            height: 10px;
            background: #ddd;
            border-radius: 5px;
        }
        QSlider::handle:horizontal {
            width: 24px;
            height: 24px;
            background: #4CAF50;
            border-radius: 12px;
            margin: -7px 0;
        }
        QSlider::handle:horizontal:hover {
            background: #45a049;
        }
        QSlider::sub-page:horizontal {
            background: #a5d6a7;
            border-radius: 5px;
        }
        QProgressBar {
            border: 1px solid #aaa;
            border-radius: 4px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #4CAF50;
            border-radius: 3px;
        }
    """)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
