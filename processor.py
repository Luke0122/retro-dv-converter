# -*- coding: utf-8 -*-
"""00年代风格 照片/视频处理管线：照片走 Pillow+numpy，视频走 ffmpeg。"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

FFMPEG = os.environ.get("FFMPEG_PATH") or (shutil.which("ffmpeg") or r"D:\Program Files\ffmpeg\bin\ffmpeg.exe")
FFPROBE = os.environ.get("FFPROBE_PATH") or (shutil.which("ffprobe") or r"D:\Program Files\ffmpeg\bin\ffprobe.exe")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif",
             ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw",
             ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".ts", ".flv", ".mpeg", ".mpg"}

RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw"}

# 画幅：横版 4:3 = 960x720，竖版 4:3 = 720x960；不裁切时适配 1280x720
H_W, H_H = 960, 720
V_W, V_H = 720, 960
FIT_W, FIT_H = 1280, 720
VIDEO_FPS = 30

PRESETS = {
    "dv": {
        "name": "复古DV（家用磁带）",
        "photo": {
            "rgb_gain": (1.03, 1.06, 0.95),
            "saturation": 0.90,
            "contrast": 0.92,
            "brightness": 1.05,
            "grain_base": 3.0,
            "grain_per": 6.0,
            "vignette": 0.15,
            "scanline": 0.0,
            "chroma_shift": 2,
            "soften": 0.8,
            "jpeg_bias": -4,
        },
        "video": {
            "eq": "contrast=0.88:saturation=0.92:brightness=0.05:gamma=1.06",
            "colorbalance": "rs=0.06:gs=0.09:bs=-0.075",
            "curves": "all='0/0.11 0.5/0.53 1/0.80'",
            "noise": "chroma",
            "vignette": "vignette=angle=PI/9",
            "extra": ("chromashift=cbh=1:crh=-0.5,"
                      "gblur=sigma=0.8,"
                      "drawbox=x='random(1)*(iw-100)':y='random(2)*(ih-2)':"
                      "w='15+random(3)*60':h=1:color=white@0.045:"
                      "t=fill:enable='lt(random(5),0.04)',"
                      "drawbox=x='random(6)*(iw-100)':y='random(7)*(ih-2)':"
                      "w='20+random(8)*80':h=1:color=black@0.055:"
                      "t=fill:enable='lt(random(10),0.05)'"),
            "crf_offset": 1,
        },
    },
    "ccd": {
        "name": "千禧CCD",
        "photo": {
            "rgb_gain": (1.045, 1.005, 0.985),
            "saturation": 1.16,
            "contrast": 1.12,
            "brightness": 1.00,
            "grain_base": 2.0,
            "grain_per": 5.0,
            "vignette": 0.42,
            "scanline": 0.0,
            "chroma_shift": 0,
            "soften": 0.5,
            "jpeg_bias": 0,
        },
        "video": {
            "eq": "contrast=1.12:saturation=1.16:brightness=0.008",
            "colorbalance": "rs=0.06:bs=-0.04:rm=0.02:rh=0.015",
            "curves": "all='0/0.03 0.5/0.5 1/0.97'",
            "noise": (4, 5),
            "vignette": "vignette=PI/5",
            "extra": "gblur=sigma=0.4",
            "crf_offset": 0,
        },
    },
}


class Cancelled(Exception):
    """任务被用户取消。"""


def classify(filename):
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def target_width(orig_w, strength):
    max_w = 1280
    min_w = 720
    s = max(0.0, min(1.0, float(strength)))
    w = int(round(max_w - (max_w - min_w) * s))
    w = max(min_w, w)
    if orig_w and orig_w < w:
        w = orig_w
    if w % 2:
        w -= 1
    return w


def unique_path(folder, stem, ext, tag="_DV"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"{stem}{tag}{ext}"
    if not base.exists():
        return base
    i = 1
    while True:
        p = folder / f"{stem}{tag}({i}){ext}"
        if not p.exists():
            return p
        i += 1


def _sanitize_watermark(text):
    """清洗用户输入，限制长度。"""
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isprintable()).strip()[:60]


def _osd_layer(text, pixel=3, box_alpha=110, fg=(235, 220, 80, 235)):
    """用 Pillow 默认位图字体渲染点阵 OSD，再最近邻放大成像素块。"""
    font = ImageFont.load_default()
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 3
    small = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ds = ImageDraw.Draw(small)
    ds.rectangle([0, 0, small.width - 1, small.height - 1], fill=(0, 0, 0, box_alpha))
    ds.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=fg)
    big = small.resize((small.width * pixel, small.height * pixel), Image.NEAREST)
    big = big.filter(ImageFilter.GaussianBlur(0.7))
    return big


def _draw_photo_watermark(img, text):
    """照片右下角点阵 OSD 日期水印（像素字体、黑底、微微发虚）。"""
    if not text:
        return img
    w, h = img.size
    pixel = max(2, min(5, int(round(min(w, h) / 180))))
    layer = _osd_layer(text, pixel=pixel)
    base = img.convert("RGBA")
    x = w - layer.width - max(10, int(w * 0.02))
    y = h - layer.height - max(8, int(h * 0.02))
    base.alpha_composite(layer, (x, y))
    return base.convert("RGB")


def _make_osd_png(text, out_path):
    """生成视频叠加用的点阵 OSD PNG。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _osd_layer(text, pixel=3).save(out_path)
    return out_path


def _load_image(path):
    """读取图片；RAW/DNG 走 rawpy 解码，普通格式走 Pillow。"""
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTS:
        import rawpy
        raw = rawpy.imread(str(path))
        try:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        finally:
            raw.close()
        return Image.fromarray(rgb)
    img = Image.open(path)
    if ext in (".heic", ".heif", ".jpg", ".jpeg", ".png"):
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
    return img


def process_photo(in_path, out_path, preset_key, quality, strength, progress=None, cancel=None,
                  watermark=None):
    preset = PRESETS[preset_key]["photo"]
    if cancel and cancel():
        raise Cancelled()
    img = _load_image(in_path)
    if progress:
        progress(10)
    if getattr(img, "is_animated", False):
        img.seek(0)
    img = img.convert("RGB")
    orig_w, orig_h = img.size
    tw = target_width(orig_w, strength)
    if tw and tw != orig_w:
        ratio = tw / orig_w
        th = max(2, int(round(orig_h * ratio / 2) * 2))
        img = img.resize((tw, th), Image.LANCZOS)
    if cancel and cancel():
        raise Cancelled()
    arr = np.asarray(img, dtype=np.float32) / 255.0
    gain = np.array(preset["rgb_gain"], dtype=np.float32)
    arr = np.clip(arr * gain.reshape(1, 1, 3), 0, 1)
    pil = Image.fromarray((arr * 255).astype(np.uint8))
    if preset["saturation"] != 1.0:
        pil = ImageEnhance.Color(pil).enhance(preset["saturation"])
    if preset["contrast"] != 1.0:
        pil = ImageEnhance.Contrast(pil).enhance(preset["contrast"])
    if preset["brightness"] != 1.0:
        pil = ImageEnhance.Brightness(pil).enhance(preset["brightness"])
    if preset["soften"] > 0:
        pil = pil.filter(ImageFilter.GaussianBlur(preset["soften"]))
    if cancel and cancel():
        raise Cancelled()
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    sigma = preset["grain_base"] + preset["grain_per"] * max(0.0, min(1.0, float(strength)))
    if sigma > 0:
        noise = np.random.normal(0, sigma / 255.0, (h, w, 3)).astype(np.float32)
        arr = np.clip(arr + noise, 0, 1)
    cs = preset["chroma_shift"]
    if cs:
        arr[..., 0] = np.roll(arr[..., 0], cs, axis=1)
        arr[..., 2] = np.roll(arr[..., 2], -cs, axis=1)
    v = preset["vignette"]
    if v > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt(((xx - w / 2) / max(w / 2, 1)) ** 2 + ((yy - h / 2) / max(h / 2, 1)) ** 2)
        m = np.clip((d - 0.30) / 0.70, 0, 1) ** 1.7
        arr *= (1.0 - v * m).astype(np.float32)[..., None]
    arr = np.clip(arr, 0, 1)
    sl = preset["scanline"]
    if sl > 0:
        arr[0::2] *= (1.0 - sl)
    if cancel and cancel():
        raise Cancelled()
    out_img = Image.fromarray((arr * 255).astype(np.uint8))
    if watermark:
        out_img = _draw_photo_watermark(out_img.convert("RGBA"), watermark)
    q = max(5, min(92, int(round(8 + 0.82 * float(quality))) + preset["jpeg_bias"]))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path, "JPEG", quality=q, subsampling=1, optimize=False)
    if progress:
        progress(100)


def _probe_size(in_path):
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(in_path)],
            capture_output=True, text=True, timeout=60)
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return None


def _video_duration(in_path):
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(in_path)],
            capture_output=True, text=True, timeout=120)
        return float(r.stdout.strip())
    except Exception:
        return None


def process_video(in_path, out_path, preset_key, quality, strength, progress=None, cancel=None,
                  watermark=None, crop_4to3=False, orientation="h"):
    preset = PRESETS[preset_key]["video"]
    if cancel and cancel():
        raise Cancelled()
    s = max(0.0, min(1.0, float(strength)))
    parts = []
    if crop_4to3:
        if orientation == "v":
            parts.append(f"scale={V_W}:{V_H}:force_original_aspect_ratio=increase")
            parts.append(f"crop={V_W}:{V_H}")
        else:
            parts.append(f"scale={H_W}:{H_H}:force_original_aspect_ratio=increase")
            parts.append(f"crop={H_W}:{H_H}")
    else:
        parts.append(f"scale={FIT_W}:{FIT_H}:force_original_aspect_ratio=decrease")
        parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if preset_key == "dv":
        parts.append("crop=iw:ih-6:x=0:y='2+2*sin(n/7)',scale=iw:ih+6")
    parts.append("format=yuv420p")
    if preset["eq"]:
        parts.append(f"eq={preset['eq']}")
    if preset["colorbalance"]:
        parts.append(f"colorbalance={preset['colorbalance']}")
    if preset["curves"]:
        parts.append(f"curves={preset['curves']}")
    if preset_key == "dv":
        n_chroma = int(round(8 + 7 * s))
        noise = f"c0s=4:c0f=t:c1s={n_chroma}:c1f=t:c2s={n_chroma}:c2f=t"
    else:
        n_base, n_per = preset["noise"]
        noise = f"alls={int(round(n_base + n_per * s))}:allf=t"
    parts.append(f"noise={noise}")
    if preset["vignette"]:
        parts.append(preset["vignette"])
    if preset["extra"]:
        parts.append(preset["extra"])
    parts.append(f"fps={VIDEO_FPS}")
    if preset_key == "dv":
        parts.append("scale=iw*0.5:ih*0.5,scale=iw*2:ih*2:flags=neighbor,gblur=sigma=0.7")
    vf = ",".join(parts)
    crf = int(round(45 - 0.22 * float(quality) + preset["crf_offset"]))
    crf = max(18, min(51, crf))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_log = out_path.with_suffix(".fferr.log")
    osd_png = None
    if watermark:
        wm = _sanitize_watermark(watermark)
        if wm:
            osd_png = _make_osd_png(wm, out_path.parent / (out_path.stem + "_osd.png"))
    if osd_png is not None:
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(in_path),
            "-i", str(osd_png),
            "-filter_complex",
            f"[0:v]{vf}[base];[base][1:v]overlay=W-w-16:H-h-12[vout]",
            "-map", "[vout]", "-map", "0:a:0?", "-sn",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            "-progress", "pipe:1", "-nostats",
            str(out_path),
        ]
    else:
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(in_path),
            "-vf", vf,
            "-map", "0:v:0", "-map", "0:a:0?", "-sn",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            "-progress", "pipe:1", "-nostats",
            str(out_path),
        ]
    duration = _video_duration(in_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=open(err_log, "wb"), text=True,
                            encoding="utf-8", errors="replace")
    last_pct = -1.0
    try:
        for line in proc.stdout:
            if cancel and cancel():
                proc.kill()
                proc.wait()
                raise Cancelled()
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if duration and duration > 0:
                    pct = max(0.0, min(99.0, us / 1_000_000 / duration * 100))
                    if progress and pct - last_pct >= 0.5:
                        last_pct = pct
                        progress(pct)
        proc.wait()
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
    if proc.returncode != 0:
        err_text = ""
        try:
            if err_log.exists():
                err_text = err_log.read_text(encoding="utf-8", errors="replace").strip()[:500]
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg 转换失败（退出码 {proc.returncode}）" + (f"：{err_text}" if err_text else ""))
    try:
        err_log.unlink(missing_ok=True)
    except OSError:
        pass
    if osd_png is not None:
        try:
            osd_png.unlink(missing_ok=True)
        except OSError:
            pass
    if progress:
        progress(100)


def selftest(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src_img = out / "测试图.png"
    src_vid = out / "测试视频.mp4"
    img = Image.new("RGB", (900, 600), (200, 190, 180))
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 400, 350], fill=(120, 60, 180))
    d.ellipse([450, 120, 800, 480], fill=(220, 120, 60))
    for i in range(0, 900, 30):
        d.line([i, 0, i + 200, 600], fill=(40, 90, 160), width=2)
    arr = np.asarray(img, dtype=np.int16)
    arr += np.random.randint(0, 60, arr.shape[:2], dtype=np.int16)[..., None]
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img.save(src_img, "PNG")
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=1280x720:rate=30",
        "-vf", "noise=alls=10:allf=t",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "14", str(src_vid)],
        check=True)
    wm = "2003/08/16 14:25"
    print(f"输入: 照片 {src_img.stat().st_size/1024:.0f}KB 视频 {src_vid.stat().st_size/1024:.0f}KB")
    all_ok = True
    p_out = out / "照片_dv.jpg"
    process_photo(src_img, p_out, "dv", 40, 0.7, watermark=wm)
    w, h = Image.open(p_out).size
    ok = p_out.stat().st_size < src_img.stat().st_size
    print(f"[照片:dv] {p_out.name} {w}x{h} 压缩OK={ok}")
    all_ok = all_ok and ok
    v_out = out / "视频_dv.mp4"
    process_video(src_vid, v_out, "dv", 40, 0.7, watermark=wm, crop_4to3=True)
    vw, vh = _probe_size(v_out) or (0, 0)
    vok = (vw, vh) == (H_W, H_H) and v_out.stat().st_size < src_vid.stat().st_size
    print(f"[视频:横版4:3] {v_out.name} {vw}x{vh} OK={vok}")
    all_ok = all_ok and vok
    p_out = out / "照片_ccd.jpg"
    process_photo(src_img, p_out, "ccd", 40, 0.7, watermark=wm)
    w, h = Image.open(p_out).size
    okc = p_out.stat().st_size < src_img.stat().st_size
    print(f"[照片:ccd] {p_out.name} {w}x{h} 压缩OK={okc}")
    all_ok = all_ok and okc
    v_out = out / "视频_ccd.mp4"
    process_video(src_vid, v_out, "ccd", 40, 0.7, watermark=wm, crop_4to3=True)
    vw, vh = _probe_size(v_out) or (0, 0)
    vokc = (vw, vh) == (H_W, H_H) and v_out.stat().st_size < src_vid.stat().st_size
    print(f"[视频:ccd] {v_out.name} {vw}x{vh} 4:3OK={vokc}")
    all_ok = all_ok and vokc
    v16 = out / "视频_dv_16x9.mp4"
    process_video(src_vid, v16, "dv", 40, 0.7, watermark=wm, crop_4to3=False)
    vw, vh = _probe_size(v16) or (0, 0)
    ok16 = (vw, vh) == (1280, 720)
    print(f"[视频:不裁切] {v16.name} {vw}x{vh} 16:9OK={ok16}")
    all_ok = all_ok and ok16
    vv = out / "视频_dv_竖版.mp4"
    process_video(src_vid, vv, "dv", 40, 0.7, watermark=wm, crop_4to3=True, orientation="v")
    vw, vh = _probe_size(vv) or (0, 0)
    okv = (vw, vh) == (V_W, V_H)
    print(f"[视频:竖版4:3] {vv.name} {vw}x{vh} OK={okv}")
    all_ok = all_ok and okv
    print("自测通过" if all_ok else "自测存在失败项")
    return 0 if all_ok else 1


def main():
    ap = argparse.ArgumentParser(description="00年代风格转换管线")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default=str(Path.cwd()))
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest(args.out))
    ap.print_help()


if __name__ == "__main__":
    main()
