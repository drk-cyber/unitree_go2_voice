"""Serve the voice transcript on this computer and optionally follow Go2 over SSH."""

import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
from threading import Lock, Thread


WEB = Path(__file__).resolve().parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
READY_MARKER = "GO2_VOICE_LOG_STREAM_READY"
REMOTE_COMMAND = (
    "systemctl is-active --quiet go2-voice || exit 3; "
    "test -n \"$(journalctl -q -u go2-voice -n 1 -o cat --no-pager)\" || exit 4; "
    f"printf '%s\\n' {READY_MARKER}; "
    "exec journalctl -q -u go2-voice -n 0 -f -o cat --no-pager"
)
REPLY = re.compile(r"^\[TTS\] 发送播报:\s*(.+)\s+\(\d+(?:\.\d+)?秒\)\s*$")


class Transcript:
    def __init__(self, mode):
        self.mode = mode
        self.status = "connecting" if mode == "live" else "demo"
        self.detail = "正在从终端建立 SSH 连接。" if mode == "live" else ""
        self.messages = deque(maxlen=120)
        self.lock = Lock()
        self.pending = None
        self.sequence = 0

    def update_status(self, status, detail=""):
        with self.lock:
            self.status, self.detail = status, detail
            if status != "connected":
                self.pending = None

    def accept_line(self, line):
        """Publish an utterance only when the voice service queues a response."""
        line = line.strip()
        if line == READY_MARKER:
            self.update_status("connected", "已接入语音服务日志。")
            return
        with self.lock:
            if self.status != "connected":
                return
            if line.startswith("识别结果:"):
                phrase = line.split(":", 1)[1].strip()
                self.pending = (phrase, self.timestamp()) if phrase and not phrase.startswith("(") else None
                return
            reply = REPLY.fullmatch(line)
            if reply and self.pending:
                phrase, timestamp = self.pending
                self.append("person", phrase, timestamp)
                self.append("robot", reply.group(1).strip(), self.timestamp())
                self.pending = None

    @staticmethod
    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

    def append(self, role, text, timestamp):
        self.sequence += 1
        self.messages.append({"id": self.sequence, "role": role, "text": text, "time": timestamp})

    def snapshot(self):
        with self.lock:
            return {"mode": self.mode, "status": self.status, "detail": self.detail,
                    "messages": list(self.messages)}


class TranscriptHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # This console is meant for the local browser only.
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(400)
            return
        if self.path == "/api/session":
            payload = json.dumps(self.server.transcript.snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_payload(payload, "application/json; charset=utf-8")
            return
        asset = STATIC.get(self.path)
        if asset is None:
            self.send_error(404)
            return
        name, mime = asset
        self.send_payload((WEB / name).read_bytes(), mime)

    def send_payload(self, data, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def follow_logs(process, transcript):
    with process.stdout:
        for line in process.stdout:
            transcript.accept_line(line)
    code = process.wait()
    if code == 3:
        detail = "板上的 go2-voice 服务未运行。请确认服务状态，再重新启动本页面的程序。"
    elif code == 4:
        detail = "SSH 已连接，但未读到语音服务日志。请在板上确认 journalctl 能查看 go2-voice 的日志。"
    else:
        detail = "语音日志连接已结束。请检查终端输出和连接，再重新启动本页面的程序。"
    transcript.update_status("offline", detail)


def ssh_target(value):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", value):
        raise argparse.ArgumentTypeError("请使用 用户名@地址，例如 unitree@192.168.123.18")
    return value


def main():
    parser = argparse.ArgumentParser(description="Go2 语音对话台")
    parser.add_argument("--live", action="store_true", help="读取算力板上的实际语音服务日志")
    parser.add_argument("--target", type=ssh_target, default="unitree@192.168.123.18",
                        help="SSH 用户与算力板地址")
    parser.add_argument("--port", type=int, default=8765, help="电脑上的网页端口")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")

    transcript = Transcript("live" if args.live else "demo")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), TranscriptHandler)
    except OSError as error:
        parser.exit(1, f"网页启动失败，检查端口是否被占用：{error}\n")
    server.transcript = transcript
    process = None
    with server:
        print(f"浏览器打开 http://127.0.0.1:{args.port}/  ，Ctrl+C 退出。", flush=True)
        if args.live:
            print(f"正在连接 {args.target}。如果 SSH 要求确认身份或输入密码，在这个终端操作。", flush=True)
            try:
                process = subprocess.Popen(
                    ["ssh", "-T", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
                     "-o", "ServerAliveCountMax=2", args.target, REMOTE_COMMAND],
                    stdout=subprocess.PIPE, encoding="utf-8", errors="replace",
                )
            except OSError as error:
                transcript.update_status("offline", "无法启动 SSH。请检查电脑上的 OpenSSH 客户端。")
                print(error, flush=True)
            if process is not None:
                Thread(target=follow_logs, args=(process, transcript), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n对话台已关闭。", flush=True)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
