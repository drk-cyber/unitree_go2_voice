"""Go2 会话释放时序对照：判定"导入主程序"与"重连过快"哪个才是握手超时的根因。

背景：connection_compare.py 五项对照中，只有"导入了主程序"的项失败。
但各项之间只隔30秒，而狗释放上一个 WebRTC 会话可能需要30-60秒——
失败可能只是"重连太快"，导入只是背锅。本实验用足够长的间隔(90秒)
把两个变量拆开验证。

六项均为只读子进程（不切模式、不发动作）；最后一项运行主程序的
--check-startup（连接并加载模型后断开，同样不发动作）。

用法(板上): python3 ~/go2_voice/slot_timing_test.py
开始前：确认最近2分钟内没有任何程序连过狗（手机App、PC脚本都算）。
总时长约9-11分钟。
"""
import argparse
import asyncio
import importlib
import logging
from pathlib import Path
import subprocess
import sys
import time

ROBOT_IP = "192.168.123.161"

# (case, 前置等待秒, 说明)
PLAN = (
    ("A1-idle-probe",   0, "基线：狗空闲2分钟后的首次连接，应当成功"),
    ("A2-wait90-probe", 90, "验证：干净断开后等90秒再连；若失败说明90秒还不够"),
    ("B-import-probe",  90, "关键项：导入主程序后立刻连接（同样隔了90秒）。成功=导入无罪"),
    ("C1-wait90-probe", 90, "再做一次90秒间隔基线，给C2做对照"),
    ("C2-fast5s-probe", 5,  "复现项：断开后只等5秒重连。若出现同样的DTLS超时=失败与导入无关"),
    ("D-startup-check", 90, "实战：完整启动主程序(--check-startup, 含自动重试)，不发动作"),
)


def setup_logging():
    logger = logging.getLogger("aiortc.rtcdtlstransport")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[DTLS] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def child(case):
    setup_logging()
    board = None
    if case in ("B-import-probe", "D-startup-check"):
        board = importlib.import_module("voice_control_board")
        print("[主程序文件]", board.__file__, flush=True)
    import unitree_webrtc_connect.webrtc_driver as driver
    from OpenSSL import SSL
    print("[通信库文件]", driver.__file__, flush=True)
    print("[加密库]", SSL.SSLeay_version(SSL.SSLEAY_VERSION).decode(), flush=True)

    if case == "D-startup-check":
        sys.argv = [board.__file__, "--check-startup"]
        asyncio.run(board.main())
        return 0

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

    return asyncio.run(probe(InspectedConnection, driver.WebRTCConnectionMethod.LocalSTA, ROBOT_IP))


def succeeded(case, code, output):
    if case == "D-startup-check":
        return code == 0 and "[启动检查] 连接和模型均就绪" in output
    return code == 0 and "[结果] 连接及验证成功" in output


def main():
    parser = argparse.ArgumentParser(description="会话释放时序对照：只读，不发动作")
    parser.add_argument("--case", choices=[c for c, _, _ in PLAN], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case:
        raise SystemExit(child(args.case))

    root = Path(__file__).resolve().parent
    for name in ("connection_probe.py", "voice_control_board.py"):
        if not (root / name).is_file():
            raise SystemExit("缺少同目录文件: " + name)

    logs = root / "logs" / ("slot-timing-" + time.strftime("%Y%m%d-%H%M%S"))
    logs.mkdir(parents=True, exist_ok=False)
    print("开始6项时序对照，总时长约9-11分钟。", flush=True)
    print("开始前请确认：最近2分钟没有任何程序连过狗（手机App、PC脚本都算）。", flush=True)
    print("[日志目录]", logs, flush=True)

    results = {}
    try:
        for index, (case, gap, purpose) in enumerate(PLAN):
            if gap:
                print("[间隔] 等待%d秒..." % gap, flush=True)
                time.sleep(gap)
            print("\n[对照 %d/%d] %s\n[目的] %s" % (index + 1, len(PLAN), case, purpose), flush=True)
            timeout = 300 if case == "D-startup-check" else 120
            command = [sys.executable, "-u", str(Path(__file__).resolve()), "--case", case]
            try:
                completed = subprocess.run(command, cwd=str(root), stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=timeout,
                                           encoding="utf-8", errors="replace")
                output, code = completed.stdout, completed.returncode
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                output += "\n[对照] 子进程超过%d秒，已结束。\n" % timeout
                code = 124
            (logs / (case + ".log")).write_text(output, encoding="utf-8")
            print(output, flush=True)
            results[case] = succeeded(case, code, output)
    finally:
        summary = "\n".join("%s: %s" % (case, "成功" if results[case] else "失败")
                            for case, _, _ in PLAN if case in results)
        (logs / "summary.txt").write_text(summary, encoding="utf-8")
        print("\n[对照结果]\n" + summary, flush=True)

        a1 = results.get("A1-idle-probe")
        a2 = results.get("A2-wait90-probe")
        b = results.get("B-import-probe")
        c1 = results.get("C1-wait90-probe")
        c2 = results.get("C2-fast5s-probe")
        d = results.get("D-startup-check")
        print("\n[判定]", flush=True)
        if a1 is False:
            print("A1就失败：开始测试前狗就被占用。等2分钟后重跑本脚本。")
        elif a2 is False:
            print("A2失败：断开后等90秒仍连不上，释放窗口比预期长。把本输出发给AI分析。")
        elif b is True and c2 is False:
            print("导入主程序不破坏连接(B成功)；只隔5秒重连复现了同样的超时(C2失败)。")
            if c1 is False:
                print("(注意: C1也失败了，C2的失败也可能延续自C1；但B成功已足以洗清导入嫌疑)")
            print("=> 结论：根因是狗释放会话需要时间，与导入无关。主程序v6的重试就是对策。")
        elif b is False:
            print("B失败：隔了90秒、前面的基线都成功，导入主程序后仍连不上。")
            print("=> 结论：导入确实有副作用。下一步运行: python3 ~/go2_voice/import_connection_bisect.py")
        elif b is None or c2 is None:
            print("测试未完成(Ctrl+C?)。已完成的项见上；完整跑一轮再判定。")
        else:
            print("B和C2都成功：导入无罪，且5秒快连也未复现失败。")
            print("=> 此前失败另有原因（可能就是30秒间隔的运气）。把本输出整段发给AI分析。")
        if d:
            print("D成功：完整主程序(含自动重试)能正常启动——实际目标已达成。")
        print("[日志已保存]", logs, flush=True)


if __name__ == "__main__":
    main()
