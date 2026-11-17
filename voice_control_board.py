"""Go2 板载语音入口：建立连接前不导入语音程序。

和 board_test 相同，先完成 WebRTC 连接与验证，再导入语音运行模块。
运行本文件；voice_control_board_runtime.py 必须放在同一个目录。
"""
import asyncio
import sys


ROBOT_IP = "192.168.123.161"


def import_runtime():
    import voice_control_board_runtime
    return voice_control_board_runtime


async def in_thread(function):
    # Python 3.8 没有 asyncio.to_thread。
    return await asyncio.get_running_loop().run_in_executor(None, function)


async def disconnect(conn):
    """先结束此 peer 尚在运行的握手任务，再关闭它的 ICE transport。

    aiortc 1.9 的 SDP setter 不保存 __connect task；close() 也不等待它。
    这里只在退出时取消属于此 pc 的任务，不改变正常握手调度。
    """
    pc = getattr(conn, "pc", None)
    jobs = []
    for task in asyncio.all_tasks():
        if task is asyncio.current_task():
            continue
        coroutine = task.get_coro()
        frame = getattr(coroutine, "cr_frame", None)
        code = getattr(coroutine, "cr_code", None)
        if (frame is not None and code is not None
                and code.co_name == "__connect"
                and frame.f_globals.get("__name__") == "aiortc.rtcpeerconnection"
                and frame.f_locals.get("self") is pc):
            task.cancel()
            jobs.append(task)
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
    await conn.disconnect()


async def run_connected(conn, operation, poll_interval=0.5):
    """连接丢失时退出当前控制流程，让服务用干净进程重试。"""
    async def monitor():
        while True:
            state = getattr(getattr(conn, "pc", None), "connectionState", "closed")
            if state != "connected":
                raise ConnectionError("机器人连接已断开: " + str(state))
            channel = getattr(getattr(conn, "datachannel", None), "channel", None)
            if channel is not None and channel.readyState != "open":
                raise ConnectionError("机器人数据通道已关闭: " + str(channel.readyState))
            await asyncio.sleep(poll_interval)
    control = asyncio.create_task(operation)
    watcher = asyncio.create_task(monitor())
    try:
        await asyncio.wait((control, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher.done():
            return await watcher
        return await control
    finally:
        for task in (control, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(control, watcher, return_exceptions=True)


async def main(argv=None):
    import argparse
    def volume_value(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("音量必须是 0 到 10 的整数")
        if not 0 <= number <= 10:
            raise argparse.ArgumentTypeError("音量必须在 0 到 10 之间")
        return number

    def wake_word_value(value):
        if len("".join(char for char in value if char.isalnum())) < 2:
            raise argparse.ArgumentTypeError("唤醒词去掉空白和标点后至少需要两个字")
        return value

    def vad_value(value):
        try:
            number = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError("录音阈值必须是 1 到 30000 的数字")
        if not 1 <= number <= 30000:
            raise argparse.ArgumentTypeError("录音阈值必须在 1 到 30000 之间")
        return number

    parser = argparse.ArgumentParser(description="Go2 板载离线语音控制（延迟导入语音模块）")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-models", action="store_true", help="只加载模型，不连接狗")
    modes.add_argument("--self-test", action="store_true", help="离线合成、转换和识别，不连接狗")
    modes.add_argument("--transcribe-wav", metavar="PATH", help="只识别已有 WAV，不连接狗")
    modes.add_argument("--check-connection", action="store_true", help="只检查连接，不导入语音模块、不发动作")
    modes.add_argument("--check-startup", action="store_true", help="连接后加载模型并断开，不切模式、不发动作")
    modes.add_argument("--check-connection-with-models", action="store_true",
                       help="与 --check-startup 相同：先连接，再加载模型")
    modes.add_argument("--listen-only", action="store_true",
                       help="只测试麦克风、识别与唤醒，不连接狗、不发动作")
    parser.add_argument("--volume", type=volume_value, default=8, metavar="0-10",
                        help="狗扬声器音量，默认 8，最大 10")
    parser.add_argument("--hands-free", action="store_true", help="免回车，先说唤醒词再下指令")
    parser.add_argument("--wake-word", type=wake_word_value, default="小狗", metavar="词语",
                        help="免回车模式的唤醒词，默认：小狗")
    parser.add_argument("--mic-device", default="plughw:CARD=Device,DEV=0", metavar="设备",
                        help="USB 麦克风设备，默认：plughw:CARD=Device,DEV=0（按名称选择）")
    parser.add_argument("--vad-threshold", type=vad_value, default=500.0, metavar="数值",
                        help="免回车录音音量阈值，默认 500")
    # 兼容此前只读对照脚本的参数。新版不启用 private handshake patch。
    parser.add_argument("--legacy-handshake", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ordered-handshake", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.ordered_handshake:
        parser.error("新版已停用实验性握手补丁，请去掉 --ordered-handshake")

    if args.listen_only:
        try:
            runtime = await in_thread(import_runtime)
            await runtime.run_listen_only(args)
            return 0
        except Exception as error:
            print("麦克风检查失败:", type(error).__name__, str(error), flush=True)
            return 1

    if args.check_models or args.self_test or args.transcribe_wav:
        try:
            runtime = await in_thread(import_runtime)
            await runtime.run_offline(args)
            return 0
        except Exception as error:
            print("离线检查失败:", type(error).__name__, str(error), flush=True)
            return 1

    conn = None
    result = 0
    try:
        print("[启动 v8] 先完成 WebRTC 连接，再导入语音模块和加载模型。", flush=True)
        from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
        conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
        # 不在同一进程里重复失败的握手，也不改 aiortc 的私有连接实现。
        await conn.connect()
        print("[连接] 已连接并验证通过。", flush=True)
        if args.check_connection:
            print("[连接诊断] 已连接并验证通过；未导入语音模块、未发送动作。", flush=True)
        else:
            print("[启动] 正在导入语音模块（连接已建立）...", flush=True)
            runtime = await in_thread(import_runtime)
            print("[启动] 加载识别模型...", flush=True)
            await in_thread(runtime.load_asr)
            print("[启动] 加载合成模型...", flush=True)
            await in_thread(runtime.load_tts)
            if conn.pc.connectionState != "connected":
                raise RuntimeError("语音模块加载后连接已断开")
            if args.check_startup or args.check_connection_with_models:
                print("[启动检查] 连接和模型均就绪；未切模式、未设置音量、未发送动作。", flush=True)
            else:
                await run_connected(conn, runtime.run_control(
                    conn, volume=args.volume, hands_free=args.hands_free,
                    wake_word=args.wake_word, mic_device=args.mic_device,
                    vad_threshold=args.vad_threshold))
    except Exception as error:
        result = 1
        print("启动/运行失败:", type(error).__name__, str(error), flush=True)
        print("请保留以上完整输出；网络和 SSH 可以保持连接。", flush=True)
    finally:
        if conn is not None:
            try:
                await disconnect(conn)
            except Exception as error:
                result = 1
                print("连接清理失败:", type(error).__name__, str(error), flush=True)
    return result


async def run_with_signals(argv=None):
    """先让主流程回收麦克风和连接，再让 asyncio 清理其他后台任务。"""
    import signal
    loop = asyncio.get_running_loop()
    application = asyncio.create_task(main(argv))
    stopping = False
    exit_code = 0
    installed = []
    previous = {}

    def request_stop(number):
        nonlocal stopping, exit_code
        if stopping or application.done():
            return
        stopping = True
        exit_code = 128 + number
        print("\n正在停止录音、归零移动并断开连接，请稍候...", flush=True)
        application.cancel()

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.getsignal(number)
            try:
                loop.add_signal_handler(number, request_stop, number)
                installed.append(number)
            except NotImplementedError:
                signal.signal(number, lambda signum, frame: loop.call_soon_threadsafe(request_stop, signum))
        try:
            return await application
        except asyncio.CancelledError:
            if not stopping:
                raise
            print("已退出。", flush=True)
            return exit_code
    finally:
        for number in installed:
            loop.remove_signal_handler(number)
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run_with_signals()))
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)
        sys.exit(130)
