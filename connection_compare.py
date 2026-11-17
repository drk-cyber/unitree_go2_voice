"""Run isolated, read-only connection comparisons on the board.

Uses the existing connection_probe.py and voice_control_board.py in this folder.
No shell commands, model changes, motion requests or automatic control startup.
"""
import argparse
import asyncio
import importlib
import logging
from pathlib import Path
import subprocess
import sys
import time


CASES = ("baseline", "early-lock", "import-main", "startup", "baseline-again")


def setup_logging():
    logger = logging.getLogger("aiortc.rtcdtlstransport")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[DTLS] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def child(case):
    setup_logging()
    if case == "early-lock":
        # One added variable compared with baseline. Python 3.8 binds this
        # lock to a pre-run loop; retain it for the lifetime of this process.
        early_lock = asyncio.Lock()
        print("[单变量对照] 已在 asyncio.run 之前创建异步锁", flush=True)
    if case in ("import-main", "startup"):
        board = importlib.import_module("voice_control_board")
        print("[主程序文件]", board.__file__, flush=True)
    import unitree_webrtc_connect.webrtc_driver as driver
    from OpenSSL import SSL
    print("[通信库文件]", driver.__file__, flush=True)
    print("[加密库]", SSL.SSLeay_version(SSL.SSLEAY_VERSION).decode(), flush=True)
    if case == "startup":
        # Use the original handshake for a controlled comparison; no v4 patch.
        sys.argv = [board.__file__, "--check-startup", "--legacy-handshake"]
        asyncio.run(board.main())
        return 0  # Parent checks the explicit startup-success marker as well.

    from connection_probe import probe
    base = driver.UnitreeWebRTCConnection

    class InspectedConnection(base):
        async def connect(self):
            try:
                return await super().connect()
            finally:
                pc = self.pc
                if pc is not None:
                    stats = await pc.getStats()
                    for stat in stats.values():
                        if stat.type == "transport":
                            print("[传输计数] DTLS=%s 收包=%s 发包=%s 收字节=%s 发字节=%s" % (
                                getattr(stat, "dtlsState", "?"), getattr(stat, "packetsReceived", "?"),
                                getattr(stat, "packetsSent", "?"), getattr(stat, "bytesReceived", "?"),
                                getattr(stat, "bytesSent", "?")), flush=True)

    return asyncio.run(probe(InspectedConnection, driver.WebRTCConnectionMethod.LocalSTA,
                             "192.168.123.161"))


def verdict(case, returncode, output):
    marker = "[启动检查] 连接和模型均就绪" if case == "startup" else "[结果] 连接及验证成功"
    return returncode == 0 and marker in output


def main():
    parser = argparse.ArgumentParser(description="自动连接对照：不切模式、不发动作")
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case:
        raise SystemExit(child(args.case))
    root = Path(__file__).resolve().parent
    for name in ("connection_probe.py", "voice_control_board.py"):
        if not (root / name).is_file():
            raise SystemExit("缺少同目录文件: " + name)
    # Check for the safe startup mode before dispatching any child.
    import ast
    source = ast.parse((root / "voice_control_board.py").read_text(encoding="utf-8"))
    if not any(isinstance(node, ast.Constant) and node.value == "--check-startup"
               for node in ast.walk(source)):
        raise SystemExit("主程序版本过旧，缺少 --check-startup；未开始连接测试")
    logs = root / "logs" / ("connection-compare-" + time.strftime("%Y%m%d-%H%M%S"))
    logs.mkdir(parents=True, exist_ok=False)
    print("开始5项对照，约4分钟。每项结束后间隔30秒。Ctrl+C 可退出。", flush=True)
    print("[日志目录]", logs, flush=True)
    results = []
    try:
        for index, case in enumerate(CASES):
            if index:
                print("[间隔] 等待30秒释放会话...", flush=True)
                time.sleep(30)
            print("\n[对照 %d/%d] %s" % (index + 1, len(CASES), case), flush=True)
            command = [sys.executable, "-u", str(Path(__file__).resolve()), "--case", case]
            try:
                completed = subprocess.run(command, cwd=str(root), stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=120, encoding="utf-8",
                                           errors="replace")
                output, code = completed.stdout, completed.returncode
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                output += "\n[对照] 子进程超过120秒，已结束。\n"
                code = 124
            (logs / (case + ".log")).write_text(output, encoding="utf-8")
            print(output, flush=True)
            results.append((case, verdict(case, code, output)))
    finally:
        summary = "\n".join("%s: %s" % (case, "成功" if ok else "失败") for case, ok in results)
        (logs / "summary.txt").write_text(summary, encoding="utf-8")
        print("[对照结果]\n" + summary, flush=True)
        print("[日志已保存]", logs, flush=True)


if __name__ == "__main__":
    main()
