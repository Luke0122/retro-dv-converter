# -*- coding: utf-8 -*-
"""00年代转换器 - 本地网页服务：Tornado + 顺序任务队列 + WebSocket 进度。"""
import argparse
import json
import os
from datetime import datetime
import logging
import queue
import shutil
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket

from processor import PRESETS, Cancelled, classify, process_photo, process_video, unique_path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
def _load_env():
    env_file = APP_DIR / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass


_load_env()

OUT_ROOT = Path(os.environ.get("RETRO00_OUT") or (APP_DIR / "输出"))
PHOTO_OUT = OUT_ROOT
VIDEO_OUT = OUT_ROOT
LOG_DIR = OUT_ROOT / "logs"
TMP_DIR = APP_DIR / "tmp"

APP_VERSION = "2026-08-16-v2"

MAIN_IOLOOP = None
ws_clients = set()
ws_lock = threading.Lock()
job_queue = queue.Queue()
jobs = {}
job_order = []
current_job = None

logger = logging.getLogger("retro00")


class Job:
    def __init__(self, file_name, kind, preset, quality, strength, tmp_path, watermark=None, crop4to3=True, orientation="h", output_dir=None, style="dv"):
        self.id = uuid.uuid4().hex[:12]
        self.file_name = file_name
        self.kind = kind
        self.preset = preset
        self.quality = quality
        self.strength = strength
        self.tmp_path = tmp_path
        self.watermark = watermark
        self.crop4to3 = crop4to3
        self.orientation = orientation
        self.output_dir = output_dir
        self.output_abs = None
        self.style = style
        self.status = "queued"
        self.percent = 0.0
        self.started_at = None
        self.eta_seconds = None
        self.output = None
        self.error = ""
        self.cancel_flag = threading.Event()

    def to_dict(self):
        return {
            "id": self.id,
            "file_name": self.file_name,
            "kind": self.kind,
            "preset": self.preset,
            "quality": self.quality,
            "strength": self.strength,
            "watermark": self.watermark,
            "crop4to3": self.crop4to3,
            "orientation": self.orientation,
            "status": self.status,
            "percent": round(self.percent, 1),
            "eta_seconds": (round(self.eta_seconds) if self.eta_seconds is not None else None),
            "output": self.output,
            "output_abs": self.output_abs,
            "style": self.style,
            "error": self.error,
        }


def broadcast(msg):
    if MAIN_IOLOOP is not None:
        MAIN_IOLOOP.add_callback(_send_all, msg)


def _send_all(msg):
    text = json.dumps(msg, ensure_ascii=False)
    dead = []
    for client in list(ws_clients):
        try:
            client.write_message(text)
        except Exception:
            dead.append(client)
    for client in dead:
        ws_clients.discard(client)


def worker_loop():
    global current_job
    while True:
        try:
            job = job_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if job.cancel_flag.is_set():
            try:
                Path(job.tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            job.status = "cancelled"
            broadcast({"type": "update", "job": job.to_dict()})
            continue
        current_job = job
        job.status = "running"
        job.started_at = time.time()
        broadcast({"type": "update", "job": job.to_dict()})
        dest = None
        try:
            folder = Path(job.output_dir) if job.output_dir else OUT_ROOT
            ext = ".jpg" if job.kind == "photo" else ".mp4"
            stem = Path(job.file_name).stem or "未命名"
            tag = "_00年代DV（较清晰）" if job.style == "1413" else "_00年代DV"
            dest = unique_path(folder, stem, ext, tag)

            def progress_cb(pct):
                job.percent = max(job.percent, pct)
                if job.started_at and pct > 3:
                    elapsed = time.time() - job.started_at
                    job.eta_seconds = elapsed * (100 - pct) / pct
                else:
                    job.eta_seconds = None
                broadcast({"type": "progress", "job": job.to_dict()})

            cancel_fn = lambda: job.cancel_flag.is_set()
            if job.kind == "photo":
                process_photo(job.tmp_path, dest, job.preset, job.quality, job.strength,
                              progress_cb, cancel_fn, job.watermark)
            else:
                process_video(job.tmp_path, dest, job.preset, job.quality, job.strength,
                              progress_cb, cancel_fn, job.watermark, job.crop4to3, job.orientation)
            if job.cancel_flag.is_set():
                job.status = "cancelled"
            else:
                job.status = "done"
                job.percent = 100.0
                job.output_abs = str(dest)
                job.output = dest.name
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            logger.exception("任务 %s 处理失败", job.id)
            job.status = "error"
            job.error = str(exc)
        finally:
            if job.status in ("error", "cancelled") and dest is not None:
                for target in (Path(dest), Path(dest).with_suffix(".fferr.log"),
                               Path(dest).with_name(dest.stem + "_osd.png")):
                    for _attempt in range(3):
                        try:
                            target.unlink(missing_ok=True)
                            break
                        except OSError:
                            time.sleep(0.4)
            try:
                Path(job.tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            current_job = None
            broadcast({"type": "update", "job": job.to_dict()})


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.write((STATIC_DIR / "index.html").read_bytes())


class OutputHandler(tornado.web.StaticFileHandler):
    def get_absolute_path(self, root, relative_path):
        return super().get_absolute_path(str(OUT_ROOT), relative_path)


class JobOutputHandler(tornado.web.StaticFileHandler):
    """按 job_id 提供输出文件预览（支持任意输出目录 + 断点/下载）。"""

    def get_absolute_path(self, root, relative_path):
        job = jobs.get(relative_path)
        if job is None or not job.output_abs:
            raise tornado.web.HTTPError(404, "未找到输出")
        path = Path(job.output_abs)
        if not path.exists():
            raise tornado.web.HTTPError(404, "输出文件不存在")
        return str(path)


@tornado.web.stream_request_body
class ConvertHandler(tornado.web.RequestHandler):
    def initialize(self):
        self._tmp_path = None
        self._tmp_file = None
        self._job = None
        self._received = 0
        self._expected = 0

    def prepare(self):
        filename = self.get_query_argument("filename", "未命名文件")
        preset = self.get_query_argument("preset", "dv")
        quality = max(0.0, min(100.0, float(self.get_query_argument("quality", "100"))))
        strength = max(0.0, min(100.0, float(self.get_query_argument("strength", "70"))))
        wm_on = self.get_query_argument("wm_on", "1")
        wm = self.get_query_argument("wm", "")
        c43 = self.get_query_argument("c4", "0")
        crop4to3 = c43 not in ("0", "false", "off", "no")
        ori = self.get_query_argument("ori", "h")
        orientation = "v" if ori == "v" else "h"
        if wm_on in ("0", "false", "off", "no"):
            watermark = None
        else:
            watermark = wm.strip() if wm.strip() else datetime.now().strftime("%Y/%m/%d %H:%M")
        kind = classify(filename)
        if kind is None:
            raise tornado.web.HTTPError(400, "不支持的文件类型，请用 jpg/png/bmp/webp/gif 或 mp4/mov/mkv/avi/webm")
        if preset not in PRESETS:
            raise tornado.web.HTTPError(400, "未知风格预设")
        try:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            self._tmp_path = TMP_DIR / (uuid.uuid4().hex + Path(filename).suffix.lower())
            self._tmp_file = open(self._tmp_path, "wb")
        except OSError as exc:
            raise tornado.web.HTTPError(500, f"无法写入临时文件: {exc}")
        style = self.get_query_argument("style", "dv")
        out = self.get_query_argument("out", "").strip()
        if out:
            out_dir = Path(out)
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                if not out_dir.is_dir():
                    raise OSError("目标不是目录")
            except OSError as exc:
                raise tornado.web.HTTPError(400, f"输出目录不可用: {exc}")
        else:
            out_dir = OUT_ROOT
        self._job = Job(filename, kind, preset, quality, strength, self._tmp_path, watermark, crop4to3, orientation, out_dir, style)
        self._expected = int(self.request.headers.get("Content-Length", "0") or 0)

    def data_received(self, chunk):
        if self._tmp_file is None:
            return
        try:
            self._tmp_file.write(chunk)
            self._received += len(chunk)
        except OSError as exc:
            logger.exception("上传写入失败")
            raise tornado.web.HTTPError(500, f"上传写入失败: {exc}")

    def post(self):
        if self._tmp_file is not None:
            self._tmp_file.close()
            self._tmp_file = None
        if self._expected and self._received < self._expected:
            if self._tmp_path is not None:
                try:
                    Path(self._tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise tornado.web.HTTPError(400, "上传不完整")
        jobs[self._job.id] = self._job
        job_order.append(self._job.id)
        job_queue.put(self._job)
        broadcast({"type": "queued", "job": self._job.to_dict()})
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write(json.dumps({"job_id": self._job.id}, ensure_ascii=False))

    def on_connection_close(self):
        if self._finished:
            return
        if self._tmp_file is not None:
            try:
                self._tmp_file.close()
            except OSError:
                pass
        if self._tmp_path is not None:
            try:
                Path(self._tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


class CancelHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except Exception:
            body = {}
        jid = body.get("job_id")
        target = jobs.get(jid) if jid else current_job
        if target is not None:
            target.cancel_flag.set()
        self.write(json.dumps({"ok": True}))


class DirsHandler(tornado.web.RequestHandler):
    def get(self):
        path = self.get_query_argument("path", "").strip()
        try:
            base = Path(path).expanduser() if path else Path.home()
            if not base.exists() or not base.is_dir():
                raise tornado.web.HTTPError(400, "目录不存在")
            dirs = sorted(
                [d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("$")],
                key=str.lower)
            parent = str(base.parent) if base.parent != base else None
            self.set_header("Content-Type", "application/json; charset=utf-8")
            self.write(json.dumps({"current": str(base), "parent": parent, "dirs": dirs[:300]},
                                  ensure_ascii=False))
        except OSError as exc:
            raise tornado.web.HTTPError(400, f"无法读取目录: {exc}")


class VersionHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write(json.dumps({"version": APP_VERSION, "default_out": str(OUT_ROOT)}))


class JobsHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write(json.dumps({"jobs": [jobs[j].to_dict() for j in job_order]}, ensure_ascii=False))


class ProgressWS(tornado.websocket.WebSocketHandler):
    def open(self):
        with ws_lock:
            ws_clients.add(self)
        self.write_message(json.dumps(
            {"type": "snapshot", "jobs": [jobs[j].to_dict() for j in job_order]}, ensure_ascii=False))

    def on_message(self, message):
        pass

    def on_close(self):
        with ws_lock:
            ws_clients.discard(self)


def find_port(preferred=8765):
    for p in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def main():
    ap = argparse.ArgumentParser(description="00年代转换器")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    for d in (OUT_ROOT, PHOTO_OUT, VIDEO_OUT, LOG_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for old in TMP_DIR.glob("*"):
        try:
            if old.is_file():
                old.unlink()
            else:
                shutil.rmtree(old)
        except OSError:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")],
        force=True,
    )
    logger.info("启动 00年代转换器，输出目录: %s", OUT_ROOT)

    global MAIN_IOLOOP
    MAIN_IOLOOP = tornado.ioloop.IOLoop.current()
    threading.Thread(target=worker_loop, daemon=True).start()

    port = args.port or find_port(8765)
    app = tornado.web.Application([
        (r"/", IndexHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": str(STATIC_DIR)}),
        (r"/output/(.*)", OutputHandler, {"path": str(OUT_ROOT)}),
        (r"/output2/([a-f0-9]+)", JobOutputHandler, {"path": ""}),
        (r"/api/convert", ConvertHandler),
        (r"/api/cancel", CancelHandler),
        (r"/api/jobs", JobsHandler),
        (r"/api/version", VersionHandler),
        (r"/api/config", VersionHandler),
        (r"/api/dirs", DirsHandler),
        (r"/ws/progress", ProgressWS),
    ])
    http_server = tornado.httpserver.HTTPServer(
        app, max_body_size=8 * 1024**3, max_buffer_size=8 * 1024**3, body_timeout=None)
    http_server.listen(port, address="127.0.0.1")
    url = f"http://127.0.0.1:{port}"
    logger.info("服务地址: %s", url)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
