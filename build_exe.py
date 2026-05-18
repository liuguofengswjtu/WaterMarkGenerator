# -*- coding: utf-8 -*-
"""
打包工具：将照片水印生成器打包为 Windows 可执行文件 (.exe)
"""

import os
import sys
import subprocess


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
    
    # 资源文件列表（需要打包进 exe 的文件）
    resource_files = [
        "app_icon.ico",
        "new_sony_logo_centeraligned.svg",
        "sony-alpha-logo.svg",
    ]
    
    # 构建 --add-data 参数
    add_data_args = []
    for f in resource_files:
        if os.path.exists(f):
            # Windows 下分隔符为分号
            add_data_args.append(f"--add-data={f};.")
        else:
            print(f"警告: 资源文件 {f} 不存在，将跳过")
    
    # 构建命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "-F",               # 单文件模式
        "-w",               # 无控制台窗口
        "-n", "照片水印生成器",  # 输出名称
        "--clean",          # 清理临时文件
        "--noconfirm",      # 不询问确认
        "--icon=app_icon.ico",  # 程序图标
    ] + add_data_args + [
        "main.py"
    ]
    
    print("开始打包...")
    print(f"命令: {' '.join(cmd)}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("-" * 60)
        print("打包成功！")
        exe_path = os.path.join(base_dir, "dist", "照片水印生成器.exe")
        print(f"可执行文件位置: {exe_path}")
        print(f"文件大小: {os.path.getsize(exe_path) / (1024*1024):.1f} MB")
    else:
        print("-" * 60)
        print("打包失败，请检查上方错误信息")
        sys.exit(1)


if __name__ == "__main__":
    build()
