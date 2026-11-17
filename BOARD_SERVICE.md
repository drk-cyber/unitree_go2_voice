# Go2 语音开机启动

## 当前验收

用户最新一轮 13:19:58–13:22:30：连接与模型正常，小狗唤醒后前进、右转、笔心匹配与播报正常，用户反馈“挺好的”。
一次 Ctrl+C 后显示停止、正常关闭与 disconnected、已退出，无第二次 Ctrl+C。
这轮支持开始验收后台服务；日志不单独证明具体移动距离或长期稳定性。

用户已在板上运行 `install_voice_service.py --check` 和 `sudo python3 ~/go2_voice/install_voice_service.py --enable`，安装、启用开机运行和首次启动成功。
后台服务日志已确认：WebRTC 连接与验证成功，ASR/TTS 加载完成，音量10/10，使用 `plughw:CARD=Device,DEV=0` 收到第一帧音频，背景音1972、门槛5030，最后提示“可以说话了”。
这证明首次后台启动的连接、模型和收音均已就绪；尚未收到后台模式实际语音指令、关闭SSH后继续工作、正常重新开机后自动恢复、断线恢复及长期运行的实测结果。
下一步先在后台模式说“小狗，打招呼”，再退出SSH重复一次；下次正常关机、开机时验收自动恢复，不必为此立即重启。
不要同时运行手动语音程序、PC 控制程序或手机 App。
服务每次启动会尝试连接，随后沿用当前程序切 normal，可能让狗起身；先留出活动空间。

本地56项检查通过（包含安装器模拟执行、断线取消控制、数据通道单独关闭、SIGTERM返回143、原有语音回归）。
运行文件和安装器通过Python3.8语法检查；板上 --check 已通过，服务已完成首次启动。

## 1. 上传（Windows PowerShell）

```powershell
scp C:\yushu\unitree_webrtc_connect\voice_control_board.py C:\yushu\unitree_webrtc_connect\voice_control_board_runtime.py C:\yushu\unitree_webrtc_connect\go2-voice.service C:\yushu\unitree_webrtc_connect\install_voice_service.py unitree@192.168.123.18:/home/unitree/go2_voice/
```

## 2. 只检查安装条件（板子 SSH 终端）

```bash
python3 ~/go2_voice/install_voice_service.py --check
```

检查程序文件、unitree 用户与 audio 组、系统 Python 可见的依赖、Device USB声卡、模型目录和 systemd 单元语法。
此步不加载模型、不连接狗、不执行动作、不安装或启动服务。
Windows 无法完成这些板上检查；需保留完整输出，不能仅依赖本地测试结果。

## 3. 安装、启用自启并立即运行（板子 SSH 终端）

确保手动程序已退出，并留出90秒机器人会话释放间隔。

```bash
sudo python3 ~/go2_voice/install_voice_service.py --enable
journalctl -u go2-voice -n 60 --no-pager
```

安装器将配置写入 /etc/systemd/system/go2-voice.service。已有不同配置会先保留备份。
服务使用普通 unitree 用户、/usr/bin/python3，读取现有用户安装包；免回车、唤醒词“小狗”、音量10、麦克风按 Device 名称选择。
不下载依赖、不修改模型、不使用 root 执行语音程序。
日志中应出现“板载语音控制就绪”“已收到连续录音的第一帧音频”“可以说话了”，然后可说“小狗，打招呼”。
systemctl 的 active 仅说明进程存在，不等于模型/机器人连接/麦克风均就绪。

若已有不同配置的服务正在运行，安装器会停下旧服务并提示90秒后执行 sudo systemctl start go2-voice，避免快速重连。

## 日常命令

```bash
# 查看是否设置开机启动、当前运行状态
systemctl is-enabled go2-voice
systemctl status go2-voice --no-pager

# 查看最近日志
journalctl -u go2-voice -n 60 --no-pager

# 实时看日志；Ctrl+C 只退出日志查看，不会停止后台语音程序
journalctl -u go2-voice -f

# 停止当前语音控制（仍保留下一次开机启动）
sudo systemctl stop go2-voice

# 停止并取消开机启动
sudo systemctl disable --now go2-voice
```

后台模式不能靠日志窗口 Ctrl+C 停服务。停服务后才能重新运行手动语音程序；停下后留出90秒再手动 start 或运行脚本，手动启动不会自动等待90秒。

## 恢复机制与限制

每次启动前先检查 /proc/asound/Device 是否存在；USB声卡未就绪时不运行语音控制，不切运动模式，90秒后重试。
网络初始化未及时就绪时，程序失败退出，systemd 在90秒后用新进程重试。
运行中 peer 不再 connected 时，新增连接监控会取消当前控制流程、断开并返回失败，交由服务90秒后重启。
手动停止不会自动重启；服务使用 SIGTERM 触发现有清理，20秒仍未结束由systemd收尾（KillMode=mixed，最终清理全部子进程）。
该监控不等于应用心跳诊断；网络黑洞尚未反映到 peer 状态时，检测仍可能有延迟。
播报期间暂停听取，语音停止不能当急停。服务初期先在有人观察时测试，再决定日常无人值守使用。

首次安装日志通过后，再安排正常关机/开机测试。不要仅为测试强制断电。
