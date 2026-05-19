# -*- coding: utf-8 -*-
"""
生成应用程序图标 (ICO)
"""

from PIL import Image, ImageDraw
import os


def draw_gradient(draw, size, color_top, color_bottom):
    """垂直渐变填充"""
    for y in range(size):
        ratio = y / (size - 1)
        r = int(color_top[0] * (1 - ratio) + color_bottom[0] * ratio)
        g = int(color_top[1] * (1 - ratio) + color_bottom[1] * ratio)
        b = int(color_top[2] * (1 - ratio) + color_bottom[2] * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b))


def draw_rounded_rect(draw, xy, radius, fill=None):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    r = radius
    # 四个角的扇形
    draw.pieslice([x1, y1, x1 + r * 2, y1 + r * 2], 180, 270, fill=fill)
    draw.pieslice([x2 - r * 2, y1, x2, y1 + r * 2], 270, 360, fill=fill)
    draw.pieslice([x1, y2 - r * 2, x1 + r * 2, y2], 90, 180, fill=fill)
    draw.pieslice([x2 - r * 2, y2 - r * 2, x2, y2], 0, 90, fill=fill)
    # 矩形主体
    draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
    draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)


def create_icon(size):
    """创建指定尺寸的图标"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    s = size
    
    # 背景：深蓝到青色渐变圆角矩形
    bg = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    draw_gradient(bg_draw, s, (26, 35, 126), (0, 188, 212))
    
    # 圆角蒙版
    mask = Image.new('L', (s, s), 0)
    mask_draw = ImageDraw.Draw(mask)
    r = max(1, int(s * 0.18))
    draw_rounded_rect(mask_draw, [0, 0, s, s], r, fill=255)
    
    img = Image.composite(bg, img, mask)
    draw = ImageDraw.Draw(img)
    
    # 相机机身（白色圆角矩形）
    body_margin = int(s * 0.22)
    body_y1 = int(s * 0.32)
    body_y2 = int(s * 0.72)
    body_r = max(1, int(s * 0.08))
    draw_rounded_rect(draw, 
        [body_margin, body_y1, s - body_margin, body_y2], 
        body_r, fill=(255, 255, 255, 230))
    
    # 取景器凸起（小矩形）
    view_w = int(s * 0.18)
    view_h = max(1, int(s * 0.06))
    view_x = (s - view_w) // 2
    view_y = body_y1 - view_h + max(1, int(s * 0.008))
    draw_rounded_rect(draw, 
        [view_x, view_y, view_x + view_w, view_y + view_h + max(1, int(s * 0.016))], 
        max(1, int(s * 0.03)), fill=(255, 255, 255, 230))
    
    # 镜头外圈（深色圆环）
    cx, cy = s // 2, (body_y1 + body_y2) // 2
    lens_r = int(s * 0.14)
    if lens_r > 2:
        draw.ellipse([cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r], 
            fill=(30, 40, 60, 240), outline=(255, 255, 255, 180), 
            width=max(1, int(s * 0.01)))
        
        # 镜头内圈（浅蓝）
        inner_r = int(lens_r * 0.65)
        if inner_r > 1:
            draw.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], 
                fill=(0, 188, 212, 200))
            
            # 镜头高光
            highlight_r = max(1, int(inner_r * 0.35))
            highlight_x = cx - int(inner_r * 0.25)
            highlight_y = cy - int(inner_r * 0.25)
            draw.ellipse([highlight_x - highlight_r, highlight_y - highlight_r,
                          highlight_x + highlight_r, highlight_y + highlight_r], 
                fill=(255, 255, 255, 150))
    
    # 水印标记：右下角小橙点
    drop_r = max(1, int(s * 0.05))
    drop_x = s - body_margin - int(s * 0.08)
    drop_y = body_y2 - int(s * 0.08)
    draw.ellipse([drop_x - drop_r, drop_y - drop_r, drop_x + drop_r, drop_y + drop_r], 
        fill=(255, 152, 0, 230))
    
    return img


def main():
    # 项目根目录（scripts/ 的上一级）
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resources_dir = os.path.join(base_dir, 'resources')
    os.makedirs(resources_dir, exist_ok=True)
    
    # 生成各尺寸图像
    sizes = [16, 32, 48, 64, 128, 256]
    images = [create_icon(s) for s in sizes]
    
    # 保存为 ICO (Pillow 方式：第一个图像 save，其余 append)
    ico_path = os.path.join(resources_dir, 'app_icon.ico')
    
    # 转成 RGB 模式（ICO 格式对 RGBA 支持有限，转成 RGB 更可靠）
    rgb_images = []
    for img in images:
        # 创建白色背景，然后合成
        bg = Image.new('RGB', img.size, (240, 240, 240))
        bg.paste(img, mask=img.split()[3])  # 使用 alpha 通道
        rgb_images.append(bg)
    
    # 保存为 ICO
    rgb_images[0].save(
        ico_path, 
        format='ICO',
        append_images=rgb_images[1:]
    )
    print(f"Windows 图标已生成: {ico_path}")
    
    # 验证
    ico = Image.open(ico_path)
    print(f"ICO 包含尺寸: {ico.size}")
    
    # ===== 生成 macOS ICNS =====
    icns_path = os.path.join(resources_dir, 'app_icon.icns')
    # 生成 1024x1024 以支持 Retina 屏幕
    mac_icon = create_icon(1024)
    mac_icon.save(icns_path, format='ICNS')
    print(f"macOS 图标已生成: {icns_path}")
    
    # 验证
    icns = Image.open(icns_path)
    print(f"ICNS 包含尺寸: {icns.size}")


if __name__ == '__main__':
    main()
