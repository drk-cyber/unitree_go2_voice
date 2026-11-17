# Go2 EDU 板载离线语音控制

在宇树 Go2 EDU 的 Jetson 算力板上实现中文语音指令控制：说“小狗，打招呼”，机器狗执行动作并开口回答。

**USB 麦克风 → SenseVoice 语音识别 → 唤醒与指令匹配 → WebRTC 控制动作 → MeloTTS 合成回答 → 狗的扬声器。**

语音处理在算力板本地完成。模型和依赖准备好后，无需外网，也不需要电脑持续运行程序；算力板与狗主控之间的内部网络必须保持连接。

## 当前成果

- 手动回车录音和免回车连续听取两种模式。
- 默认唤醒词“小狗”，支持“小狗，打招呼”，或先唤醒、等回应后再说指令。
- 停止、站立、趴下、前进、后退、左右转、跳舞、打招呼、比心等命令映射。
- SenseVoice 本地识别、MeloTTS 本地合成，使用 sherpa-onnx 推理。
- USB 麦克风按 ALSA 名称选择，启动时测量背景音，按声音停顿切分语句。
- 常驻 WebRTC 音频轨道、播报期间暂停听取、移动结束/取消时归零、退出回收录音进程。
- systemd 后台服务、开机自启配置、失败后等待90秒以新进程重试。

用户已实测前台唤醒、前进转向、打招呼、比心和一次 Ctrl+C 正常退出；后台首次连接、模型加载与收音就绪已确认。正常重新开机恢复、实机断线恢复、长期稳定性与识别准确率尚未完成系统验收。

当前使用固定指令和固定回答，未接入大模型自由对话。唤醒由识别后的文字规则判断，不是专用关键词唤醒模型；项目没有自行训练语音模型或开发底层步态控制器。

## 从哪里开始看

| 文件 | 用途 |
|---|---|
| [PROJECT_OVERVIEW_ZH.md](PROJECT_OVERVIEW_ZH.md) | 完整项目介绍、文件导航、已验证成果与复用方式 |
| [voice_control_board.py](voice_control_board.py) | 当前板载主入口，先连接验证，再导入语音模块 |
| [voice_control_board_runtime.py](voice_control_board_runtime.py) | 录音、识别、唤醒、指令、动作与语音播报的核心逻辑 |
| [BOARD_V8.md](BOARD_V8.md) | 免回车、麦克风、唤醒与音量使用说明 |
| [go2-voice.service](go2-voice.service) | 后台服务配置 |
| [install_voice_service.py](install_voice_service.py) | 服务安装前检查与安装器 |
| [BOARD_SERVICE.md](BOARD_SERVICE.md) | 后台运行、日志与开机自启管理 |
| [BOARD_V7.md](BOARD_V7.md) | 先连接再导入的启动修复说明 |
| [UPSTREAM_README.md](UPSTREAM_README.md) | 原始开源通信库说明 |

`voice_control.py` 是早期 PC 原型，使用电脑麦克风、faster-whisper 和不同的 TTS 方案，不是当前板载入口。`connection_*.py` 等是历史诊断工具，日常部署无需逐一运行。`test_board_*.py` 等是回归检查。

## 环境与复用前提

已观察到的板载环境：Jetson Orin NX、Ubuntu20.04、Python3.8.10、aiortc1.9.0、av12.3.0、numpy1.24.4、sherpa_onnx2.0.1，以及 unitree_webrtc_connect 通信库和 Linux arecord。

当前应用按以下设备与路径配置：

- 狗主控 IP：192.168.123.161，在入口的 ROBOT_IP 中配置。
- 算力板项目目录：/home/unitree/go2_voice/。
- USB 麦克风：plughw:CARD=Device,DEV=0，复用前用 arecord -l 核对，可用 --mic-device 修改。
- 识别模型目录：models/sensevoice/，包含 model.int8.onnx 和 tokens.txt。
- 合成模型目录：models/melo_tts/，包含真实 model.onnx、lexicon.txt、tokens.txt、dict/ 和配套归一化文件。

**本仓库不包含大型模型、离线安装包、个人录音与生成的语音缓存。** 克隆源码不等于已经安装完整运行环境。服务安装器只检查现有依赖和目录，不下载模型或安装依赖。新设备需要准备匹配的模型与依赖，并适配用户、路径、IP和声卡。

注意 MeloTTS 包中的 model.int8.onnx 可能只是 Git LFS 指针文本。当前程序会跳过指针并选择实际模型；已实测使用170429550字节的 model.onnx。

## 板上常用命令

以下命令在算力板的终端执行。两个板载 Python 文件必须放在同一个目录；后台服务和手动程序不要同时运行。

```bash
# 只检查模型，不连接机器人
python3 ~/go2_voice/voice_control_board.py --check-models

# 离线合成、转换、识别自检，不连接机器人
python3 ~/go2_voice/voice_control_board.py --self-test

# 只测试麦克风、识别与唤醒，不连接机器人、不播报、不发动作
python3 ~/go2_voice/voice_control_board.py --listen-only

# 检查连接和模型，不切模式、不发动作
python3 ~/go2_voice/voice_control_board.py --check-startup

# 正式免回车运行；后台服务需先停止
python3 ~/go2_voice/voice_control_board.py --hands-free --volume 10
```

完整运行会查询运动模式，并在需要时切到 normal，可能让狗起身。停止程序不会自动切回原模式。移动每次持续2秒，播报期间不支持语音打断；语音“停”不能替代硬件急停。

## 后台服务

安装步骤见 [BOARD_SERVICE.md](BOARD_SERVICE.md)。已有服务的设备可以用：

```bash
# 停止并取消开机自启
sudo systemctl disable --now go2-voice

# 只设置下次开机自启，现在不启动
sudo systemctl enable go2-voice

# 设置自启并立即运行
sudo systemctl enable --now go2-voice

# 查看自启设置、运行状态和日志
systemctl is-enabled go2-voice
systemctl status go2-voice --no-pager
journalctl -u go2-voice -n 60 --no-pager
```

刚停止连接后，留出90秒再手动启动下一次连接。手动启动不会自动等待服务的重试间隔。

## 测试与历史资料

本地已有七个测试文件合计56项通过的记录，覆盖导入顺序、唤醒规则、音频处理、退出清理、服务配置等。部分检查依赖仓库外的 board_wheels 中指定版本的 wheel，PC 原型测试还依赖其单独环境；源码不包含这些测试材料，不能假定克隆后所有历史测试直接可跑。

[DEPLOY.md](DEPLOY.md)、[OFFLINE_CHECKS.md](OFFLINE_CHECKS.md) 与 [go2-voice-handoff-v3.md](go2-voice-handoff-v3.md) 保留了早期过程、失败方案和待验证假设。阅读时先看顶部最新补充。旧连接问题的具体底层原因未完全确认，不把有效绕开方式等同于已经证明根因。

## 开源来源

本项目在 [legion1581/unitree_webrtc_connect](https://github.com/legion1581/unitree_webrtc_connect) 基础上完成板载语音集成与适配。基础通信库的原始介绍保留在 [UPSTREAM_README.md](UPSTREAM_README.md)。

原项目采用 [MIT License](LICENSE)，版权署名为 Copyright (c) 2025 Konstantin Severov。分发相关代码时保留原许可证。SenseVoice、MeloTTS及其模型应分别遵守各自许可。
