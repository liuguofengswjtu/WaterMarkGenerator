# -*- coding: utf-8 -*-
"""
WaterMarkGenerator - Core Module
照片水印生成器核心模块
"""

import os
import re
from fractions import Fraction
from io import BytesIO
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ExifTags
from PIL.ExifTags import TAGS, GPSTAGS
import piexif

# 全局缓存
_LOGO_CACHE = {}  # key: (target_height, color_mode) -> PIL Image

# 尝试导入 Qt SVG 模块（用于渲染索尼 α LOGO）
try:
    from PyQt5.QtSvg import QSvgRenderer
    from PyQt5.QtCore import QByteArray, QBuffer
    from PyQt5.QtGui import QImage, QPainter
    _HAS_QT_SVG = True
except Exception:
    _HAS_QT_SVG = False


# EXIF标签映射
EXIF_TAGS = {
    'Make': 0x010F,
    'Model': 0x0110,
    'LensModel': 0xA434,
    'FNumber': 0x829D,
    'ExposureTime': 0x829A,
    'ISOSpeedRatings': 0x8827,
    'PhotographicSensitivity': 0x8832,
    'FocalLength': 0x920A,
    'FocalLengthIn35mmFilm': 0xA405,
    'DateTimeOriginal': 0x9003,
}


def get_exif_data(image_path):
    """
    读取图片的EXIF信息
    返回字典，包含相机型号、镜头型号、拍摄参数等
    """
    try:
        img = Image.open(image_path)
        exif = img._getexif()
        if not exif:
            return None
        
        data = {}
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            data[tag] = value
        
        # 获取关键信息
        result = {}
        
        # 相机厂商
        make = data.get('Make', '')
        if isinstance(make, bytes):
            make = make.decode('utf-8', errors='ignore').strip('\x00').strip()
        result['make'] = make
        
        # 相机型号
        model = data.get('Model', '')
        if isinstance(model, bytes):
            model = model.decode('utf-8', errors='ignore').strip('\x00').strip()
        result['model'] = model
        
        # 镜头型号
        lens_model = data.get('LensModel', '')
        if isinstance(lens_model, bytes):
            lens_model = lens_model.decode('utf-8', errors='ignore').strip('\x00').strip()
        result['lens_model'] = lens_model
        
        # 光圈值
        fnumber = data.get('FNumber', None)
        if fnumber and isinstance(fnumber, tuple) and len(fnumber) == 2:
            result['aperture'] = fnumber[0] / fnumber[1] if fnumber[1] != 0 else fnumber[0]
        elif fnumber:
            result['aperture'] = float(fnumber)
        else:
            result['aperture'] = None
        
        # 快门速度（格式化为常用分数表示）
        exposure = data.get('ExposureTime', None)
        result['shutter'] = _format_shutter_value(exposure)
        
        # ISO
        iso = data.get('ISOSpeedRatings', None) or data.get('PhotographicSensitivity', None)
        if iso:
            result['iso'] = int(iso) if isinstance(iso, (int, float)) else str(iso)
        else:
            result['iso'] = None
        
        # 焦距
        focal = data.get('FocalLength', None)
        focal_35 = data.get('FocalLengthIn35mmFilm', None)
        if focal and isinstance(focal, tuple) and len(focal) == 2:
            result['focal_length'] = int(focal[0] / focal[1]) if focal[1] != 0 else focal[0]
        elif focal:
            result['focal_length'] = int(focal)
        else:
            result['focal_length'] = None
        
        if focal_35:
            result['focal_length_35'] = int(focal_35)
        else:
            result['focal_length_35'] = None
        
        return result
    except Exception as e:
        print(f"读取EXIF失败: {e}")
        return None


def _format_shutter_value(exposure):
    """将快门速度格式化为常用分数表示，如 1/250s、2s、1/3s"""
    if not exposure:
        return None
    
    try:
        if isinstance(exposure, tuple) and len(exposure) == 2:
            num, den = exposure
            if den == 0:
                return None
            frac = Fraction(num, den)
        else:
            val = float(exposure)
            if val >= 1:
                if val == int(val):
                    return f"{int(val)}s"
                return f"{val:.2f}s".rstrip('0').rstrip('.') + "s"
            frac = Fraction(val).limit_denominator(10000)
        
        # 统一格式化
        if frac.numerator == 1:
            return f"1/{frac.denominator}s"
        elif frac.denominator == 1:
            return f"{frac.numerator}s"
        else:
            # 尝试简化为接近的 1/n 形式（如果误差很小）
            decimal_val = float(frac)
            if decimal_val < 1:
                approx = Fraction(decimal_val).limit_denominator(1000)
                if approx.numerator == 1:
                    return f"1/{approx.denominator}s"
            return f"{frac.numerator}/{frac.denominator}s"
    except (ValueError, TypeError, ZeroDivisionError):
        return f"{exposure}s" if exposure else None


def format_camera_name(make, model):
    """格式化相机名称"""
    # 清理厂商名
    make_map = {
        'SONY': 'Sony',
        'Canon': 'Canon',
        'NIKON CORPORATION': 'Nikon',
        'NIKON': 'Nikon',
        'FUJIFILM': 'Fujifilm',
        'Panasonic': 'Panasonic',
        'OLYMPUS CORPORATION': 'Olympus',
        'OLYMPUS IMAGING CORP.': 'Olympus',
    }
    
    clean_make = make_map.get(make, make)
    
    # 特殊处理 Sony Alpha 系列
    if 'Sony' in clean_make or 'SONY' in make:
        if 'ILCE' in model or 'α' in model:
            # 尝试提取型号
            match = re.search(r'ILCE-(\d+)([A-Za-z]*)', model)
            if match:
                num = match.group(1)
                suffix = match.group(2)
                if suffix:
                    return f"α{num} {suffix}"
                return f"α{num}"
    
    # 返回完整名称
    if clean_make and model:
        return f"{clean_make} {model}"
    return model or clean_make or "Unknown Camera"


# =============================================================================
# 镜头官方短名称映射表
# key: 规范化后的名称（小写、去掉空格/mm/厂商前缀等）
# value: 官方短名称
# =============================================================================
_LENS_NAME_MAP = {
    # === Sony FE 变焦 ===
    '2470f28gm': 'SEL2470GM',
    '2470f28gmii': 'SEL2470GM2',
    '2470f28gmossii': 'SEL2470GM2',
    '2470f28gm2': 'SEL2470GM2',
    '24105f4goss': 'SEL24105G',
    '1635f28gm': 'SEL1635GM',
    '1635f28gmii': 'SEL1635GM2',
    '1635f28gm2': 'SEL1635GM2',
    '1224f28gm': 'SEL1224GM',
    '70200f28gmoss': 'SEL70200GM',
    '70200f28gmossii': 'SEL70200GM2',
    '70200f28gmoss2': 'SEL70200GM2',
    '70200f28gmii': 'SEL70200GM2',
    '70200f28gm2': 'SEL70200GM2',
    '100400f4556gmoss': 'SEL100400GM',
    '100400f4556gm': 'SEL100400GM',
    '200600f5663goss': 'SEL200600G',
    '200600f5663g': 'SEL200600G',
    '400800f638goss': 'SEL400800G',
    '400800f638g': 'SEL400800G',
    '2860f456': 'SEL2860',
    '2860f4556': 'SEL2860',
    '2860f4556oss': 'SEL2860',
    '2860f4oss': 'SEL2860',
    # === Sony E 变焦 (APS-C) ===
    '1650f3556osspz': 'SELP1650',
    '1650f3556pz': 'SELP1650',
    '18135f3556oss': 'SEL18135',
    '55210f4563oss': 'SEL55210',
    '55210f4563': 'SEL55210',
    '1018f4oss': 'SEL1018',
    '1020f4g': 'SELP1020G',
    '1020f4gpz': 'SELP1020G',
    '18105f4goss': 'SEL18105G',
    '1670f4zoss': 'SEL1670Z',
    '1670f4oss': 'SEL1670Z',
    # === Sony FE 定焦 ===
    '50f14gm': 'SEL50F14GM',
    '50f12gm': 'SEL50F12GM',
    '85f14gm': 'SEL85F14GM',
    '85f14gmii': 'SEL85F14GM2',
    '85f14gm2': 'SEL85F14GM2',
    '135f18gm': 'SEL135F18GM',
    '35f14gm': 'SEL35F14GM',
    '24f14gm': 'SEL24F14GM',
    '20f18g': 'SEL20F18G',
    '40f25g': 'SEL40F25G',
    '50f25g': 'SEL50F25G',
    '24f28g': 'SEL24F28G',
    '14f18gm': 'SEL14F18GM',
    '600f4gmoss': 'SEL600F40GM',
    '400f28gmoss': 'SEL400F28GM',
    '300f28gmoss': 'SEL300F28GM',
    '35f18g': 'SEL35F18G',
    '20f18': 'SEL20F18',
    # === Sony E 定焦 (APS-C) ===
    '50f18oss': 'SEL50F18',
    '35f18oss': 'SEL35F18',
    '24f18': 'SEL24F18',
    '30f35': 'SEL30F35',
    '16f28': 'SEL16F28',
    # === Canon RF ===
    'rf2470f28lisusm': 'RF24-70L',
    'rf2470f28l': 'RF24-70L',
    'rf70200f28lisusm': 'RF70-200L',
    'rf70200f28l': 'RF70-200L',
    'rf100500f4571lisusm': 'RF100-500L',
    'rf50f12lusm': 'RF50L',
    'rf50f14lusm': 'RF50F1.4L',
    'rf85f12lusm': 'RF85L',
    'rf85f14lusm': 'RF85F1.4L',
    'rf85f14l': 'RF85F1.4L',
    'rf1535f28lisusm': 'RF15-35L',
    'rf24105f4lisusm': 'RF24-105L',
    'rf100400f5671lisusm': 'RF100-400L',
    'rf2870f2lusm': 'RF28-70L',
    'rf2870f2l': 'RF28-70L',
    'rf1535f28l': 'RF15-35L',
    'rf24105f4l': 'RF24-105L',
    'rf100400f5671l': 'RF100-400L',
    'rf100500f4571l': 'RF100-500L',
    # === Canon EF ===
    'ef2470f28liiusm': 'EF24-70L II',
    'ef2470f28lii': 'EF24-70L II',
    'ef70200f28liiiusm': 'EF70-200L III',
    'ef70200f28liii': 'EF70-200L III',
    'ef70200f28liiusm': 'EF70-200L II',
    'ef70200f28lii': 'EF70-200L II',
    'ef1635f28liiiusm': 'EF16-35L III',
    'ef1635f28liii': 'EF16-35L III',
    'ef100400f4556liiusm': 'EF100-400L II',
    'ef100400f4556lii': 'EF100-400L II',
    'ef50f12lusm': 'EF50L',
    'ef85f12liiusm': 'EF85L II',
    'ef85f12lii': 'EF85L II',
    'ef35f14liiusm': 'EF35L II',
    'ef35f14lii': 'EF35L II',
    'ef2470f4lisusm': 'EF24-70/4L',
    'ef24105f4lisusm': 'EF24-105/4L',
    # === Nikon Z ===
    'z2470f28s': 'Z24-70/2.8S',
    'z2470f4s': 'Z24-70/4S',
    'z70200f28vrs': 'Z70-200/2.8S',
    'z1430f4s': 'Z14-30/4S',
    'z50f18s': 'Z50/1.8S',
    'z85f18s': 'Z85/1.8S',
    'z35f18s': 'Z35/1.8S',
    'z24120f4s': 'Z24-120/4S',
    'z100400f4556vrs': 'Z100-400/4.5-5.6S',
    'z400f45vrs': 'Z400/4.5S',
    'z600f4vrs': 'Z600/4S',
    'z800f63vrs': 'Z800/6.3S',
    'z1424f28s': 'Z14-24/2.8S',
    'z58f95s': 'Z58/0.95S',
    # === Nikon F ===
    # Nikon F (with AF-S prefix)
    'afs2470f28eed': '24-70/2.8E',
    'afs2470f28ged': '24-70/2.8G',
    'afs70200f28efledvr': '70-200/2.8E FL',
    'afs70200f28efled': '70-200/2.8E FL',
    'afs1424f28ged': '14-24/2.8G',
    'afs105f14eed': '105/1.4E',
    'afs85f14ged': '85/1.4G',
    'afs58f14ged': '58/1.4G',
    'afs50f14ged': '50/1.4G',
    'afs35f14ged': '35/1.4G',
    'afs200500f56eedvr': '200-500/5.6E',
    'afs200500f56eed': '200-500/5.6E',
    'afs80400f4556gedvr': '80-400/4.5-5.6G VR',
    # Nikon F (without AF-S prefix, for EXIF that already stripped it)
    '2470f28eed': '24-70/2.8E',
    '2470f28eedvr': '24-70/2.8E',
    '2470f28ged': '24-70/2.8G',
    '70200f28efledvr': '70-200/2.8E FL',
    '70200f28efled': '70-200/2.8E FL',
    '1424f28ged': '14-24/2.8G',
    '105f14eed': '105/1.4E',
    '105f14eedvr': '105/1.4E',
    '85f14ged': '85/1.4G',
    '85f14gedvr': '85/1.4G',
    '85f14g': '85/1.4G',
    '58f14ged': '58/1.4G',
    '58f14g': '58/1.4G',
    '50f14ged': '50/1.4G',
    '50f14gedvr': '50/1.4G',
    '50f14g': '50/1.4G',
    '35f14ged': '35/1.4G',
    '35f14gedvr': '35/1.4G',
    '35f14g': '35/1.4G',
    '200500f56eedvr': '200-500/5.6E',
    '200500f56eed': '200-500/5.6E',
    '80400f4556gedvr': '80-400/4.5-5.6G VR',
    # === Fujifilm ===
    'xf1655f28rlmwr': 'XF16-55/2.8',
    'xf50140f28rlmoiswr': 'XF50-140/2.8',
    'xf100400f4556rlmoiswr': 'XF100-400/4.5-5.6',
    'xf200mmf2rlmoiswr': 'XF200/2',
    'xf200f2rlmoiswr': 'XF200/2',
    'xf56f12rwr': 'XF56/1.2',
    'xf35f14r': 'XF35/1.4',
    'xf23f14r': 'XF23/1.4',
    'xf16f14rwr': 'XF16/1.4',
    'xf33f14rlmwr': 'XF33/1.4',
    'xf90f2rlmwr': 'XF90/2',
    'xf18f14rwr': 'XF18/1.4',
    'xf27f28rwr': 'XF27/2.8',
    'xf35f2rwr': 'XF35/2',
    'xf23f2rwr': 'XF23/2',
    # === Panasonic L ===
    'lumixspro2470f28': 'S Pro 24-70/2.8',
    'lumixspro70200f28ois': 'S Pro 70-200/2.8',
    'lumixspro50f14': 'S Pro 50/1.4',
    'lumixs24105f4macroois': 'S 24-105/4',
    'lumixs20-60f3556': 'S 20-60/3.5-5.6',
    'lumixs85f18': 'S 85/1.8',
    # Panasonic (without LUMIX prefix)
    'spro2470f28': 'S Pro 24-70/2.8',
    'spro70200f28ois': 'S Pro 70-200/2.8',
    'spro50f14': 'S Pro 50/1.4',
    's24105f4macroois': 'S 24-105/4',
    's20-60f3556': 'S 20-60/3.5-5.6',
    's85f18': 'S 85/1.8',
    # === Sigma ===
    '24-70mmf28dgdnart': '24-70/2.8 DG DN Art',
    '2470f28dgdnart': '24-70/2.8 DG DN Art',
    '85mmf14dgdnart': '85/1.4 DG DN Art',
    '85f14dgdnart': '85/1.4 DG DN Art',
    '35mmf14dgdnart': '35/1.4 DG DN Art',
    '35f14dgdnart': '35/1.4 DG DN Art',
    '14-24mmf28dgdnart': '14-24/2.8 DG DN Art',
    '1424f28dgdnart': '14-24/2.8 DG DN Art',
    '100-400mmf5-63dgdnoscontemporary': '100-400/5-6.3 DG DN OS',
    '100400f563dgdnos': '100-400/5-6.3 DG DN OS',
    '150-600mmf5-63dgdnossports': '150-600/5-6.3 DG DN OS S',
    '150600f563dgdnoss': '150-600/5-6.3 DG DN OS S',
    '28-70mmf28dgdncontemporary': '28-70/2.8 DG DN C',
    '2870f28dgdn': '28-70/2.8 DG DN C',
    # === Tamron ===
    '28-75mmf28diiiivxdg2': '28-75/2.8 G2',
    '2875f28diiiivxdg2': '28-75/2.8 G2',
    '2875f28g2': '28-75/2.8 G2',
    '70-180mmf28diiiivxd': '70-180/2.8',
    '70180f28diiiivxd': '70-180/2.8',
    '70180f28': '70-180/2.8',
    '35-150mmf2-28diiiivxd': '35-150/2-2.8',
    '35150f2-28diiiivxd': '35-150/2-2.8',
    '35150f228': '35-150/2-2.8',
    '17-28mmf28diiiirxd': '17-28/2.8',
    '1728f28diiiirxd': '17-28/2.8',
    '1728f28': '17-28/2.8',
    '28-200mmf28-56diiiirxd': '28-200/2.8-5.6',
    '28200f28-56diiiirxd': '28-200/2.8-5.6',
    '28200f2856': '28-200/2.8-5.6',
    '150-500mmf5-67diiiivcvxd': '150-500/5-6.7',
    '150500f567diiiivcvxd': '150-500/5-6.7',
    '150500f567': '150-500/5-6.7',
}


def _normalize_lens_name(name):
    """规范化镜头名称用于匹配映射表"""
    if not name:
        return ''
    n = name.lower().strip()
    # 先去掉自动对焦类型前缀，否则 AF-S NIKKOR 中的 NIKKOR 不会被识别
    n = re.sub(r'^(af-s|af-p|af)\s+', '', n)
    # 去掉厂商前缀（包括 Nikon NIKKOR、Panasonic LUMIX 等）
    n = re.sub(r'^(sony|canon|nikon|nikkor|panasonic|olympus|fujifilm|sigma|tamron|lumix)\s+', '', n)
    # 只去掉 Sony 的 mount 标识（FE / E），保留其他品牌 mount 前缀以便区分
    n = re.sub(r'^(fe|e)\s+', '', n)
    # 去掉 "mm" 单位
    n = n.replace('mm', '')
    # 去掉空格和特殊符号（保留字母数字）
    n = re.sub(r'[\s\-/\.]', '', n)
    # 去掉竖线（如 Sigma " | Art"）
    n = n.replace('|', '')
    return n


def _try_sony_short_name(name):
    """尝试从 EXIF 名称构造索尼官方 SEL 短名称"""
    n = name.lower().strip()
    n = re.sub(r'^sony\s+', '', n)

    # 提取焦距
    focal_match = re.search(r'(\d+(?:-\d+)?)\s*mm', n)
    if not focal_match:
        return None
    focal = focal_match.group(1).replace('-', '')

    # 提取光圈（仅定焦放入型号）
    aperture_match = re.search(r'[f/]\s*(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?', n)
    aperture = ''
    if aperture_match:
        a1 = aperture_match.group(1).replace('.', '')
        a2 = aperture_match.group(2)
        if not a2:  # 定焦
            aperture = f'F{a1}'

    # 提取特性后缀
    suffix = ''
    if re.search(r'gm\s*(ii|2|ⅱ)', n):
        suffix = 'GM2'
    elif 'gm' in n:
        suffix = 'GM'
    elif 'g' in n and 'gm' not in n:
        suffix = 'G'

    if 'pz' in n:
        return f'SELP{focal}{suffix}'.upper()

    return f'SEL{focal}{aperture}{suffix}'.upper()


def _simplify_lens_name(name):
    """通用简化：去掉厂商名和 mount 类型，保留核心信息"""
    if not name:
        return ''
    n = name.strip()
    # 去掉厂商前缀
    n = re.sub(r'^(Sony|Canon|Nikon|Panasonic|Olympus|Fujifilm|Sigma|Tamron)\s+', '', n, flags=re.I)
    # 去掉 mount 前缀
    n = re.sub(r'^(FE|E|EF|EF-S|RF|AF-S|AF-P|Z|XF|GF|M\.Zuiko|LUMIX\s*S)\s+', '', n, flags=re.I)
    # 去掉 "mm" 但保留前后空格以便阅读
    n = n.replace('mm', '')
    # 清理多余空格
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def format_lens_name(lens_model, make=''):
    """
    将 EXIF 中的镜头长名称转换为官方短名称。
    优先使用映射表匹配，其次尝试厂商规则化，最后回退到通用简化。
    """
    if not lens_model:
        return ''

    original = lens_model.strip()

    # 如果已经是短名称格式（如 SEL2470GM、RF50L），直接返回
    # 真正的短名称不含 "mm" 单位，避免 RF70-200mm 这类长名称被误判
    if 'mm' not in original.lower() and re.match(r'^[A-Z]{2,4}\d', original):
        return original

    # 映射表匹配
    normalized = _normalize_lens_name(original)
    if normalized in _LENS_NAME_MAP:
        return _LENS_NAME_MAP[normalized]

    # 索尼规则化
    make_upper = (make or '').upper()
    if 'SONY' in make_upper or not make:
        sony_short = _try_sony_short_name(original)
        if sony_short:
            return sony_short

    # 通用简化
    return _simplify_lens_name(original)


def format_exif_for_watermark(exif_data):
    """
    将EXIF数据格式化为水印文字
    返回 (line1, line2, line3) 三元组
    """
    if not exif_data:
        return ("Unknown Camera", "", "")
    
    make = exif_data.get('make', '')
    model = exif_data.get('model', '')
    lens = exif_data.get('lens_model', '')
    lens_short = format_lens_name(lens, make)
    aperture = exif_data.get('aperture', None)
    shutter = exif_data.get('shutter', None)
    iso = exif_data.get('iso', None)
    focal = exif_data.get('focal_length', None)
    focal_35 = exif_data.get('focal_length_35', None)
    
    # 第一行：相机型号（品牌化）
    line1 = format_camera_name(make, model)
    
    # 第二行：相机型号 / 镜头官方短名称
    parts = []
    if model:
        parts.append(model)
    if lens_short:
        parts.append(lens_short)
    line2 = " / ".join(parts) if parts else ""
    
    # 第三行：ISO / 快门 / 光圈 / 焦距
    param_parts = []
    if iso:
        param_parts.append(f"ISO{iso}")
    if shutter:
        param_parts.append(shutter)
    if aperture:
        param_parts.append(f"F{aperture:.1f}" if aperture != int(aperture) else f"F{int(aperture)}")
    if focal:
        focal_str = f"{focal}mm"
        if focal_35 and focal_35 != focal:
            focal_str += f" (eq. {focal_35}mm)"
        param_parts.append(focal_str)
    
    line3 = " / ".join(param_parts) if param_parts else ""
    
    return (line1, line2, line3)


def _draw_text_with_shadow(draw, x, y, text, font, text_color, shadow_color, shadow_offset=2):
    """绘制带阴影的文字"""
    # 绘制阴影
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)
    # 绘制主文字
    draw.text((x, y), text, font=font, fill=text_color)


def render_sony_alpha_logo(target_height, color_mode='white'):
    """
    渲染索尼 α LOGO 为 PIL Image (RGBA)
    color_mode: 'white' 或 'orange'
    结果按 (target_height, color_mode) 缓存
    """
    cache_key = (target_height, color_mode)
    if cache_key in _LOGO_CACHE:
        return _LOGO_CACHE[cache_key]
    
    if not _HAS_QT_SVG:
        return None
    
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'new_sony_logo_centeraligned.svg')
    if not os.path.exists(svg_path):
        return None
    
    try:
        with open(svg_path, 'r', encoding='utf-8') as f:
            svg_content = f.read()
        
        # 替换颜色
        if color_mode == 'white':
            svg_content = svg_content.replace('fill="#e94e09"', 'fill="#ffffff"')
        # 橙红色保持原样 (#e94e09)
        
        renderer = QSvgRenderer(QByteArray(svg_content.encode('utf-8')))
        default_size = renderer.defaultSize()
        if default_size.height() <= 0:
            return None
        
        scale = target_height / default_size.height()
        width = int(default_size.width() * scale)
        
        image = QImage(width, target_height, QImage.Format_ARGB32)
        image.fill(0)  # 透明背景
        
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        
        # QImage 转 PIL Image (通过 QBuffer)
        qt_buffer = QBuffer()
        qt_buffer.open(QBuffer.ReadWrite)
        image.save(qt_buffer, 'PNG')
        qt_buffer.seek(0)
        data = qt_buffer.data().data()
        qt_buffer.close()
        logo_img = Image.open(BytesIO(data))
        _LOGO_CACHE[cache_key] = logo_img
        return logo_img
    except Exception as e:
        print(f"渲染 α LOGO 失败: {e}")
        return None


def _draw_line1_with_logo(overlay, draw, x, y, line1, font1, logo_img, text_color, shadow_color, line1_size=48, logo_scale=1.0, logo_base_size=100, logo_opacity=255):
    """绘制第一行，将 α 替换为 SVG LOGO，返回实际占用高度
    line1_size: 第一行字号（仅用于文字和基线计算）
    logo_scale: LOGO 缩放比例（0.5~3.0）
    logo_base_size: LOGO 基准像素高度（与字号独立）
    logo_opacity: LOGO 透明度 (0-255)
    """
    # 先测量整行文字的边界框（作为基准高度）
    full_bbox = draw.textbbox((x, y), line1, font=font1)
    text_top = full_bbox[1]
    text_bottom = full_bbox[3]
    
    if logo_img is None or 'α' not in line1:
        _draw_text_with_shadow(draw, x, y, line1, font1, text_color, shadow_color)
        return text_bottom - text_top
    
    parts = line1.split('α', 1)
    left_text = parts[0]
    right_text = parts[1] if len(parts) > 1 else ""
    
    current_x = x
    
    # 绘制左侧文字
    if left_text:
        _draw_text_with_shadow(draw, current_x, y, left_text, font1, text_color, shadow_color)
        bbox = draw.textbbox((x, y), left_text, font=font1)
        current_x += bbox[2] - bbox[0]
    
    # 计算 LOGO 尺寸：基于独立的 logo_base_size，与字号完全脱钩
    logo_h = int(logo_base_size * logo_scale)
    logo_w = int(logo_img.width * logo_h / logo_img.height)
    logo_resized = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
    
    # 调整 LOGO 透明度
    if logo_opacity < 255:
        logo_rgba = logo_resized.convert('RGBA')
        r, g, b, a = logo_rgba.split()
        a = a.point(lambda p: int(p * logo_opacity / 255))
        logo_resized = Image.merge('RGBA', (r, g, b, a))
    
    # 居中对齐：LOGO 中心与文字中心在同一水平线
    text_center_y = (text_top + text_bottom) / 2
    logo_y = int(text_center_y - logo_h / 2)
    
    # 粘贴 LOGO（使用 alpha 通道）
    overlay.paste(logo_resized, (current_x, logo_y), logo_resized)
    current_x += logo_w
    
    # 绘制右侧文字
    if right_text:
        _draw_text_with_shadow(draw, current_x, y, right_text, font1, text_color, shadow_color)
    
    # 计算实际占用高度：取文字区域和 LOGO 区域的最外边界
    logo_bottom = logo_y + logo_h
    actual_bottom = max(text_bottom, logo_bottom)
    actual_top = min(text_top, logo_y)
    return actual_bottom - actual_top


def apply_custom_watermark(overlay, watermark_img, pos, scale, opacity, canvas_width, canvas_height):
    """将自定义图片水印粘贴到 overlay 上
    pos: (rel_x, rel_y) 相对坐标 (0.0~1.0)
    scale: 缩放比例
    opacity: 透明度 0-255
    """
    if watermark_img is None:
        return
    
    w = int(watermark_img.width * scale)
    h = int(watermark_img.height * scale)
    if w <= 0 or h <= 0:
        return
    
    resized = watermark_img.resize((w, h), Image.LANCZOS)
    
    # 调整透明度
    if opacity < 255:
        rgba = resized.convert('RGBA')
        r, g, b, a = rgba.split()
        a = a.point(lambda p: int(p * opacity / 255))
        resized = Image.merge('RGBA', (r, g, b, a))
    
    x = int(canvas_width * pos[0])
    y = int(canvas_height * pos[1])
    overlay.paste(resized, (x, y), resized)


@lru_cache(maxsize=128)
def _load_font_cached(font_path_tuple, size, italic):
    """缓存字体加载。font_path_tuple 为 (path,) 或 (None,)"""
    font_path = font_path_tuple[0] if font_path_tuple else None
    candidates = []
    if font_path and os.path.exists(font_path):
        candidates.append(font_path)
    
    if italic:
        candidates.extend([
            "C:/Windows/Fonts/msyhl.ttc",
            "C:/Windows/Fonts/calibrii.ttf",
            "C:/Windows/Fonts/ariali.ttf",
        ])
    
    candidates.extend([
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ])
    
    for f in candidates:
        try:
            if os.path.exists(f):
                return ImageFont.truetype(f, size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_font(font_path, size, italic=False):
    """加载字体（带缓存），如果不存在则使用默认字体"""
    return _load_font_cached((font_path,), size, italic)


def add_watermark_to_image(image_path, output_path, watermark_texts, params):
    """
    给图片添加水印
    
    参数:
        image_path: 输入图片路径
        output_path: 输出图片路径
        watermark_texts: (line1, line2, line3) 水印文字
        params: 参数字典，包含:
            - font_path: 字体路径
            - line1_size: 第一行字体大小
            - line2_size: 第二行字体大小
            - line3_size: 第三行字体大小
            - opacity: 透明度 (0-255)
            - italic: 是否斜体
            - line_spacing: 行间距
            - position: (x, y) 位置，相对坐标 (0.0-1.0) 或绝对像素
            - is_relative_pos: 位置是否为相对坐标
            - padding: 边距
    """
    img = Image.open(image_path)
    original_format = img.format
    original_mode = img.mode
    
    # 转换为RGBA以支持透明度
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    width, height = img.size
    
    # 创建透明图层用于绘制水印
    overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    line1, line2, line3 = watermark_texts
    
    # 参数解析（支持每行独立字体、斜体、间距）
    # 兼容旧参数
    default_font = params.get('font_path', None)
    default_italic = params.get('italic', False)
    
    font_path1 = params.get('font_path_line1', default_font)
    font_path2 = params.get('font_path_line2', default_font)
    font_path3 = params.get('font_path_line3', default_font)
    
    italic1 = params.get('italic_line1', default_italic)
    italic2 = params.get('italic_line2', default_italic)
    italic3 = params.get('italic_line3', default_italic)
    
    line1_size = params.get('line1_size', int(height * 0.04))
    line2_size = params.get('line2_size', int(height * 0.018))
    line3_size = params.get('line3_size', int(height * 0.018))
    opacity = params.get('opacity', 180)
    
    # 间距：优先使用独立间距，回退到旧参数
    spacing_1_2 = params.get('spacing_1_2', params.get('line_spacing', int(height * 0.008)))
    spacing_2_3 = params.get('spacing_2_3', params.get('line_spacing', int(height * 0.008)))
    
    padding = params.get('padding', int(height * 0.02))
    
    # 计算位置
    pos = params.get('position', (0.05, 0.05))
    is_relative = params.get('is_relative_pos', True)
    
    if is_relative:
        base_x = int(width * pos[0])
        base_y = int(height * pos[1])
    else:
        base_x, base_y = int(pos[0]), int(pos[1])
    
    # 加载字体（每行独立）
    font1 = get_font(font_path1, line1_size, italic1)
    font2 = get_font(font_path2, line2_size, italic2)
    font3 = get_font(font_path3, line3_size, italic3)
    
    # 文字颜色
    color_rgb = params.get('text_color', (255, 255, 255))
    text_color = (*color_rgb, opacity)
    shadow_color = (0, 0, 0, int(opacity * 0.6))
    
    # α LOGO（嵌入第一行文字）
    use_logo = params.get('use_alpha_logo', False)
    logo_color = params.get('alpha_logo_color', 'white')
    logo_scale = params.get('logo_scale', 1.0)
    logo_opacity = params.get('logo_opacity', opacity)
    logo_base = int(min(width, height) * 0.035)
    logo_img = None
    if use_logo and 'α' in line1:
        logo_img = render_sony_alpha_logo(logo_base, logo_color)
    
    # 自定义水印
    custom_wm_enabled = params.get('custom_watermark_enabled', False)
    custom_wm_img = params.get('custom_watermark_img', None)
    custom_wm_path = params.get('custom_watermark_path', None)
    if custom_wm_enabled and custom_wm_img is None and custom_wm_path and os.path.exists(custom_wm_path):
        try:
            custom_wm_img = Image.open(custom_wm_path).convert('RGBA')
        except Exception:
            custom_wm_img = None
    custom_wm_scale = params.get('custom_watermark_scale', 1.0)
    custom_wm_opacity = params.get('custom_watermark_opacity', 200)
    custom_wm_pos = params.get('custom_watermark_pos', (0.5, 0.5))
    
    # 绘制每一行
    current_y = base_y + padding
    
    # 第一行（α LOGO 嵌入文字）
    if line1:
        x = base_x + padding
        y = current_y
        rendered_h = _draw_line1_with_logo(overlay, draw, x, y, line1, font1, logo_img, text_color, shadow_color, line1_size, logo_scale, logo_base, logo_opacity)
        current_y += rendered_h + spacing_1_2
    
    # 第二行
    if line2:
        bbox = draw.textbbox((0, 0), line2, font=font2)
        text_h = bbox[3] - bbox[1]
        x = base_x + padding
        y = current_y
        _draw_text_with_shadow(draw, x, y, line2, font2, text_color, shadow_color)
        current_y += text_h + spacing_2_3
    
    # 第三行
    if line3:
        bbox = draw.textbbox((0, 0), line3, font=font3)
        text_h = bbox[3] - bbox[1]
        x = base_x + padding
        y = current_y
        _draw_text_with_shadow(draw, x, y, line3, font3, text_color, shadow_color)
    
    # 绘制自定义水印
    if custom_wm_enabled and custom_wm_img is not None:
        apply_custom_watermark(overlay, custom_wm_img, custom_wm_pos, custom_wm_scale, custom_wm_opacity, width, height)
    
    # 合并图层
    result = Image.alpha_composite(img, overlay)
    
    # 保存图片，尽量保持原有格式和EXIF
    if original_mode != 'RGBA' and original_format != 'PNG':
        result = result.convert(original_mode)
    
    # 尝试保留EXIF
    try:
        original_exif = piexif.load(image_path)
        exif_bytes = piexif.dump(original_exif)
        result.save(output_path, format=original_format or 'JPEG', exif=exif_bytes, quality=95)
    except Exception:
        result.save(output_path, format=original_format or 'JPEG', quality=95)
    
    return output_path


def generate_preview(image_path, watermark_texts, params, preview_size=(1600, 1600), img=None, original_size=None):
    """
    生成预览图
    参数:
        image_path: 图片路径（当 img 为 None 时用于加载）
        watermark_texts: 水印文字三元组
        params: 参数字典
        preview_size: 预览最大尺寸
        img: 可选，已加载的 PIL Image（避免重复打开原图）
        original_size: 可选，原图尺寸 (width, height)（用于计算 scale）
    返回 PIL Image
    """
    if img is None:
        img = Image.open(image_path)
        img.thumbnail(preview_size, Image.LANCZOS)
    
    # 临时修改参数以适应预览尺寸
    preview_params = params.copy()
    if original_size:
        scale = img.size[1] / original_size[1]
    else:
        scale = img.size[1] / Image.open(image_path).size[1]
    
    preview_params['line1_size'] = int(params.get('line1_size', 40) * scale)
    preview_params['line2_size'] = int(params.get('line2_size', 18) * scale)
    preview_params['line3_size'] = int(params.get('line3_size', 18) * scale)
    preview_params['spacing_1_2'] = int(params.get('spacing_1_2', params.get('line_spacing', 8)) * scale)
    preview_params['spacing_2_3'] = int(params.get('spacing_2_3', params.get('line_spacing', 8)) * scale)
    preview_params['padding'] = int(params.get('padding', 20) * scale)
    
    # 在缩略图上绘制水印
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    line1, line2, line3 = watermark_texts
    
    default_font = params.get('font_path', None)
    default_italic = params.get('italic', False)
    font_path1 = params.get('font_path_line1', default_font)
    font_path2 = params.get('font_path_line2', default_font)
    font_path3 = params.get('font_path_line3', default_font)
    italic1 = params.get('italic_line1', default_italic)
    italic2 = params.get('italic_line2', default_italic)
    italic3 = params.get('italic_line3', default_italic)
    
    opacity = params.get('opacity', 180)
    padding = preview_params['padding']
    spacing_1_2 = preview_params['spacing_1_2']
    spacing_2_3 = preview_params['spacing_2_3']
    
    pos = params.get('position', (0.05, 0.05))
    is_relative = params.get('is_relative_pos', True)
    
    if is_relative:
        base_x = int(img.size[0] * pos[0])
        base_y = int(img.size[1] * pos[1])
    else:
        base_x, base_y = int(pos[0] * scale), int(pos[1] * scale)
    
    font1 = get_font(font_path1, preview_params['line1_size'], italic1)
    font2 = get_font(font_path2, preview_params['line2_size'], italic2)
    font3 = get_font(font_path3, preview_params['line3_size'], italic3)
    
    color_rgb = params.get('text_color', (255, 255, 255))
    text_color = (*color_rgb, opacity)
    shadow_color = (0, 0, 0, int(opacity * 0.6))
    
    # α LOGO（嵌入第一行文字）
    use_logo = params.get('use_alpha_logo', False)
    logo_color = params.get('alpha_logo_color', 'white')
    logo_scale = params.get('logo_scale', 1.0)
    logo_opacity = params.get('logo_opacity', opacity)
    logo_base = int(min(img.size[0], img.size[1]) * 0.035)
    logo_img = None
    if use_logo and 'α' in line1:
        logo_img = render_sony_alpha_logo(logo_base, logo_color)
    
    # 自定义水印
    custom_wm_enabled = params.get('custom_watermark_enabled', False)
    custom_wm_img = params.get('custom_watermark_img', None)
    custom_wm_path = params.get('custom_watermark_path', None)
    if custom_wm_enabled and custom_wm_img is None and custom_wm_path and os.path.exists(custom_wm_path):
        try:
            custom_wm_img = Image.open(custom_wm_path).convert('RGBA')
        except Exception:
            custom_wm_img = None
    custom_wm_scale = params.get('custom_watermark_scale', 1.0)
    custom_wm_opacity = params.get('custom_watermark_opacity', 200)
    custom_wm_pos = params.get('custom_watermark_pos', (0.5, 0.5))
    
    current_y = base_y + padding
    
    # 第一行（α LOGO 嵌入文字）
    if line1:
        rendered_h = _draw_line1_with_logo(overlay, draw, base_x + padding, current_y, line1, font1, logo_img, text_color, shadow_color, preview_params['line1_size'], logo_scale, logo_base, logo_opacity)
        current_y += rendered_h + spacing_1_2
    
    if line2:
        bbox = draw.textbbox((0, 0), line2, font=font2)
        text_h = bbox[3] - bbox[1]
        _draw_text_with_shadow(draw, base_x + padding, current_y, line2, font2, text_color, shadow_color)
        current_y += text_h + spacing_2_3
    
    if line3:
        bbox = draw.textbbox((0, 0), line3, font=font3)
        text_h = bbox[3] - bbox[1]
        _draw_text_with_shadow(draw, base_x + padding, current_y, line3, font3, text_color, shadow_color)
    
    # 绘制自定义水印
    if custom_wm_enabled and custom_wm_img is not None:
        apply_custom_watermark(overlay, custom_wm_img, custom_wm_pos, custom_wm_scale, custom_wm_opacity, img.size[0], img.size[1])
    
    result = Image.alpha_composite(img, overlay)
    return result
