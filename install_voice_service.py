"""Install the reviewed Go2 systemd unit on the board; no pip or robot commands.

python3 install_voice_service.py --check
sudo python3 install_voice_service.py --enable
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path("/home/unitree/go2_voice")
SERVICE = "go2-voice.service"
DESTINATION = Path("/etc/systemd/system") / SERVICE


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", timeout=40)
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)
    if result.returncode:
        raise RuntimeError("命令失败: " + " ".join(command))
    return result.stdout


def preflight():
    if not sys.platform.startswith("linux") or not Path("/run/systemd/system").is_dir():
        raise RuntimeError("请在算力板 Ubuntu 系统中运行此脚本")
    if Path(__file__).resolve().parent != ROOT:
        raise RuntimeError("请先把文件放到 /home/unitree/go2_voice/ 再运行")
    for name in ("voice_control_board.py", "voice_control_board_runtime.py", SERVICE):
        if not (ROOT / name).is_file():
            raise RuntimeError("缺少文件: " + str(ROOT / name))
    import pwd
    import grp
    account = pwd.getpwnam("unitree")
    grp.getgrnam("audio")
    if not shutil.which("arecord"):
        raise RuntimeError("找不到 arecord")
    # 明确检查未来服务使用的解释器与普通用户包；不加载语音模型、不连接狗。
    probe = (
        "import importlib.util, sys; "
        "names=('aiortc','numpy','av','sherpa_onnx','unitree_webrtc_connect'); "
        "missing=[n for n in names if importlib.util.find_spec(n) is None]; "
        "print('Python:',sys.version.split()[0]); "
        "print('Missing:',missing); sys.exit(bool(missing))"
    )
    command = ["/usr/bin/python3", "-c", probe]
    if os.geteuid() == 0:
        command = ["runuser", "-u", "unitree", "--", "env", "HOME=/home/unitree"] + command
    elif os.geteuid() != account.pw_uid:
        raise RuntimeError("请使用 unitree 用户运行检查，或通过 sudo 安装")
    run(command)
    cards = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    if not any("[Device" in line and "USB" in line.upper() for line in cards.splitlines()):
        raise RuntimeError("未找到ID为 Device 的USB声卡；请检查 arecord -l，暂不安装")
    for directory in ("models/sensevoice", "models/melo_tts"):
        if not (ROOT / directory).is_dir():
            raise RuntimeError("缺少模型目录: " + str(ROOT / directory))
    # systemd 自己检查单元字段与 ExecStart，避免静态文本检查给出假成功。
    run(["systemd-analyze", "verify", str(ROOT / SERVICE)])
    print("检查通过：未连接狗、未启动服务。", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Go2 语音开机服务安装")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="仅检查文件、用户、依赖和USB设备")
    action.add_argument("--enable", action="store_true", help="安装、设置开机启动并立即运行；可能让狗起身")
    args = parser.parse_args(argv)
    if args.enable and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise RuntimeError("安装需要 sudo：sudo python3 ~/go2_voice/install_voice_service.py --enable")
    preflight()
    if args.check:
        return 0
    print("将启用免回车语音控制，音量10；连接后可能切 normal 并起身。", flush=True)
    source = ROOT / SERVICE
    if DESTINATION.exists() and DESTINATION.read_bytes() != source.read_bytes():
        import time
        backup = DESTINATION.with_name(SERVICE + ".backup-" + str(time.time_ns()))
        shutil.copy2(str(DESTINATION), str(backup))
        print("原配置已备份:", backup, flush=True)
    # 配置不变且服务已运行时，不主动重启并占用新的WebRTC会话。
    changed = not DESTINATION.exists() or DESTINATION.read_bytes() != source.read_bytes()
    shutil.copyfile(str(source), str(DESTINATION))
    os.chmod(str(DESTINATION), 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", SERVICE])
    if changed:
        active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
        if active:
            run(["systemctl", "stop", SERVICE])
            print("旧服务已停止；需要留出90秒释放机器人会话。", flush=True)
            # 当前活跃服务更新不自动快速重连，由用户稍后启动。
            print("配置已更新。90秒后执行：sudo systemctl start " + SERVICE, flush=True)
            return 0
    run(["systemctl", "start", SERVICE])
    print("服务已启动并设置开机运行。active 仅代表进程运行，请继续检查日志中的语音就绪提示。", flush=True)
    print("日志：journalctl -u go2-voice -n 60 --no-pager", flush=True)
    print("停止：sudo systemctl stop go2-voice", flush=True)
    print("取消自启：sudo systemctl disable --now go2-voice", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, KeyError, subprocess.SubprocessError) as error:
        print("安装/检查失败:", str(error), flush=True)
        sys.exit(1)
