import struct
import io
from PIL import Image


def _image_to_ico_dib(img):
    """将 PIL Image 转为 ICO 用的 BMP DIB 数据（含 AND mask）"""
    width, height = img.size
    
    # 用 Pillow 生成 BMP，再去掉 14 字节文件头得到 DIB
    buf = io.BytesIO()
    if img.mode == 'RGBA':
        # 保持 32-bit
        img.save(buf, format='BMP')
    else:
        img.convert('RGB').save(buf, format='BMP')
    bmp_data = buf.getvalue()
    dib = bytearray(bmp_data[14:])  # 去掉 BMP 文件头
    
    # 修改 biHeight 为实际高度的 2 倍（ICO 规范要求 XOR + AND）
    original_height = struct.unpack('<i', dib[8:12])[0]
    if original_height < 0:
        original_height = -original_height
    dib[8:12] = struct.pack('<i', original_height * 2)
    
    # biSizeImage 对于 BI_RGB 可以设为 0
    dib[20:24] = struct.pack('<I', 0)
    
    # 计算 AND mask 大小：每行按 4 字节对齐
    and_row_size = ((width + 31) // 32) * 4
    and_mask = bytes(and_row_size * height)
    
    return bytes(dib) + and_mask


def save_ico(images, output_path):
    """
    手动构建多分辨率 ICO 文件
    images: PIL Image 列表，建议从大到小排列（Windows 偏好）
    """
    count = len(images)
    icondir = struct.pack('<HHH', 0, 1, count)
    
    image_data_list = []
    for img in images:
        image_data_list.append(_image_to_ico_dib(img))
    
    offset = 6 + 16 * count
    entries = b''
    all_data = b''
    
    for img, data in zip(images, image_data_list):
        w, h = img.size
        width_byte = 0 if w >= 256 else w
        height_byte = 0 if h >= 256 else h
        size = len(data)
        entry = struct.pack('<BBBBHHII',
            width_byte, height_byte, 0, 0,
            1, 32 if img.mode == 'RGBA' else 24,
            size, offset
        )
        entries += entry
        all_data += data
        offset += size
    
    with open(output_path, 'wb') as f:
        f.write(icondir + entries + all_data)
