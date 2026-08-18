# Retro DV Converter（00年代时光机）

把照片/视频一键转换成 2000 年代复古 DV 磁带风格：黄绿灰雾、彩噪、磁带抖动、柔化、点阵日期水印。
Convert photos & videos to a 2000s-era retro DV camcorder look: yellow-green haze, chroma noise, tape jitter, softness, and a pixel OSD date stamp.

## 功能 Features
- 照片与视频统一处理，输出 MP4 / JPG
- 两种风格：DV（复古DV磁带）、CCD（千禧CCD），输出后缀分别带 DV / CCD
- 可选 4:3 横版/竖版裁切，或保持原始比例
- 可自定义输出目录
- 点阵 OSD 日期水印（默认自动使用处理文件时的时间）
- 实时进度与预计剩余时间
- 支持 jpg/png/webp/gif/bmp、DNG 等相机 RAW、HEIC/HEIF
- 视频统一 30fps

## 依赖 Dependencies
- Python 3.11+
- ffmpeg（需在 PATH 中，或用环境变量 `FFMPEG_PATH` 指定）
- 安装依赖：`pip install -r requirements.txt`

## 运行 Run
- Windows：双击 `启动retro-dv-converter.bat`（自动打开浏览器 http://127.0.0.1:8765）
- 命令行：`python server.py`

## 环境变量 Environment Variables
- `RETRO00_OUT`：默认输出目录（未设置时使用程序目录下的 `输出`）
- `FFMPEG_PATH` / `FFPROBE_PATH`：ffmpeg / ffprobe 可执行文件路径
- `PYTHONW`：启动脚本使用的 pythonw 路径（可选）
- 程序目录下的 `.env` 文件（已被 .gitignore 忽略）：本地配置，可放任意环境变量，例如 `RETRO00_OUT=D:\我的输出目录`

## 使用说明
详细中文使用说明见 `使用说明.txt`。

## 许可证 License
MIT
