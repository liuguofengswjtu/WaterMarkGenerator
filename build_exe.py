# -*- coding: utf-8 -*-
"""
打包工具：将照片水印生成器打包为可执行文件
- Windows: 输出 .exe（单文件）
- macOS: 输出 .app 应用包
"""

import os
import sys
import subprocess
import platform


def check_install_pyinstaller():
    """检查并安装 PyInstaller"""
    try:
        import PyInstaller
        print("PyInstaller 已安装")
        return True
    except ImportError:
        print("PyInstaller 未安装，正在安装...")
        result = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"安装失败: {result.stderr}")
            return False
        print("PyInstaller 安装成功")
        return True


def build():
    """执行打包"""
    if not check_install_pyinstaller():
        print("错误：无法安装 PyInstaller，请手动运行 `pip install pyinstaller` 后重试")
        sys.exit(1)
    
    # 项目根目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    
    is_mac = sys.platform == 'darwin'
    is_win = sys.platform == 'win32'
    
    # 资源文件列表（需要打包进可执行文件的文件）
    resource_files = [
        "new_sony_logo_centeraligned.svg",
        "sony-alpha-logo.svg",
    ]
    
    # 图标文件（平台特定）
    if is_mac:
        icon_file = "app_icon.icns"
        if not os.path.exists(icon_file):
            print(f"警告: macOS 图标 {icon_file} 不存在，将尝试使用 app_icon.ico 或跳过图标")
            icon_file = "app_icon.ico" if os.path.exists("app_icon.ico") else None
    else:
        icon_file = "app_icon.ico"
    
    # 构建 --add-data 参数（分隔符平台特定）
    sep = ':' if is_mac else ';'
    add_data_args = []
    for f in resource_files:
        if os.path.exists(f):
            add_data_args.append(f"--add-data={f}{sep}.")
        else:
            print(f"警告: 资源文件 {f} 不存在，将跳过")
    
    # 基础命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",          # 清理临时文件
        "--noconfirm",      # 不询问确认
    ]
    
    if is_mac:
        # macOS: 生成 .app 应用包（不使用 -F 单文件，标准 .app bundle 体验更好）
        app_name = "照片水印生成器"
        cmd.extend(["--windowed", "-n", app_name])
        if icon_file and os.path.exists(icon_file):
            cmd.append(f"--icon={icon_file}")
        print("目标平台: macOS，将生成 .app 应用包")
    elif is_win:
        # Windows: 单文件 .exe
        cmd.extend(["-F", "-w", "-n", "照片水印生成器"])
        if icon_file and os.path.exists(icon_file):
            cmd.append(f"--icon={icon_file}")
        print("目标平台: Windows，将生成单文件 .exe")
    else:
        # Linux 或其他平台
        cmd.extend(["-F", "-n", "照片水印生成器"])
        if icon_file and os.path.exists(icon_file):
            cmd.append(f"--icon={icon_file}")
        print(f"目标平台: {sys.platform}")
    
    cmd.extend(add_data_args)
    cmd.append("main.py")
    
    print("开始打包...")
    print(f"命令: {' '.join(cmd)}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("-" * 60)
        print("打包成功！")
        if is_mac:
            app_path = os.path.join(base_dir, "dist", "照片水印生成器.app")
            print(f"应用包位置: {app_path}")
        else:
            exe_path = os.path.join(base_dir, "dist", "照片水印生成器.exe")
            if os.path.exists(exe_path):
                print(f"可执行文件位置: {exe_path}")
                print(f"文件大小: {os.path.getsize(exe_path) / (1024*1024):.1f} MB")
            else:
                print(f"请查看 dist/ 目录下的输出文件")
    else:
        print("-" * 60)
        print("打包失败，请检查上方错误信息")
        sys.exit(1)


if __name__ == "__main__":
    build()
