# Go2 EDU 板载离线语音控制：项目说明与文件导航

整理日期：2026-11-17。根据当前本地源码和用户提供的板上运行日志整理；不代表重新进行了实机测试。

## 1. 这个项目完成了什么

本项目基于开源 `unitree_webrtc_connect` 通信库，在宇树 Go2 EDU 的 Jetson 算力板上实现中文离线语音指令控制：

**USB 麦克风收音 → SenseVoice 识别 → 唤醒与指令匹配 → WebRTC 控制机器狗 → MeloTTS 合成回答 → 狗的扬声器播放。**

用户可以说“小狗，打招呼”，也可以先说“小狗”，等回答“我在，请说”后再说一条指令。支持停止、站起来、趴下、前进、后退、左转、右转、跳舞、打招呼、比心等命令。

语音识别、指令判断和语音合成都在算力板本地运行。部署好模型与依赖后，正常运行不依赖外网，也不需要电脑持续运行程序；算力板和狗主控之间的内部网络仍然需要保持连接。

这是预训练语音模型与机器人控制的集成项目：没有自行训练语音模型，也没有重新编写四足机器人的底层平衡、步态或电机控制器。当前采用固定命令和固定回答，尚未接入大语言模型自由对话、视觉导航或自主路径规划。

### 可以直接向别人这样介绍

> 我基于开源 Unitree WebRTC 通信库，在 Go2 EDU 的 Jetson 算力板上完成了中文离线语音控制。USB 麦克风采集声音，SenseVoice 把语音转成文字，程序判断唤醒词和动作指令，再通过 WebRTC 调用机器狗现有的运动与动作接口，同时用 MeloTTS 合成回答并由狗的扬声器播放。目前已实测免回车唤醒、前进转向、打招呼和比心等功能，并完成后台服务部署。整个语音处理在板上运行，模型准备好后无需外网。项目主要完成的是语音交互和部署集成，底层机器人控制与语音模型使用现有方案。

## 2. 电脑、算力板、狗主控各做什么

| 部分 | 本项目中的职责 |
|---|---|
| Windows 电脑 | 编辑程序、保存文件、上传文件、通过 SSH 查看板上日志；最终运行时可以不连接电脑 |
| Jetson Orin NX 算力板 | 运行 Python 主程序、录音、语音识别、唤醒判断、动作指令匹配、语音合成 |
| Go2 运动主控 | 接收高层控制请求，执行官方已有的站立、行走、转向和动作控制 |
| USB 麦克风 | 当前正式运行方案的输入设备，插在算力板上 |
| 狗的扬声器 | 播放算力板合成后通过 WebRTC 发送的声音 |

当前 Windows 源码目录为 `C:\yushu\unitree_webrtc_connect`，板上应用目录为 `/home/unitree/go2_voice/`。
板上算力板地址为 `192.168.123.18`，程序连接的狗主控地址为 `192.168.123.161`。这是当前这台设备的配置，别人复用时需要核对自己的地址。

## 3. 最重要的四个文件

| 文件 | 作用 | 是否平时直接运行 |
|---|---|---|
| `voice_control_board.py` | 当前板载主入口：解析参数，先连接并验证 WebRTC，再导入语音模块、加载模型，处理连接监控与退出清理 | 是，平时运行它 |
| `voice_control_board_runtime.py` | 真正的语音和控制实现：ASR、TTS、录音分句、唤醒、命令表、动作发送、音频播放 | 否，由主入口导入；必须与入口放在同一目录 |
| `go2-voice.service` | systemd 后台服务配置：以 unitree 用户运行免回车语音程序，音量10，指定 USB 麦克风，失败后等待90秒重启 | 由系统服务管理 |
| `install_voice_service.py` | 检查板上安装条件，安装服务并设置开机启动；不会替你下载模型或安装 Python 依赖 | 部署服务时运行 |

其中前两个文件就组成当前应用本体。后两个文件用于后台运行和开机启动，不是另一套语音程序。

### 为什么把主程序拆成两个文件

旧单文件程序在板上出现过“基础连接测试成功，但完整语音程序在 ICE 成功后 DTLS 握手超时”的问题。
当前处理方式是：入口保持轻量，**先完成 WebRTC 连接和验证，再导入语音模块并加载模型**。这个调整已经获得板上成功日志及后续语音实测。

这说明当前绕开方式有效，但具体是哪条提前导入的语句触发旧问题、是否涉及某种底层竞争，尚未证实。不要向别人描述为“已经修复 aiortc 内核”或“已经确定某个语音库有问题”。

### runtime 里面怎样分工

| 代码位置或对象 | 职责 |
|---|---|
| `COMMANDS` | 指令词、动作名称、回答台词；如“比心”映射到 `FingerHeart`，加入“笔心”等已观察到的识别别名 |
| `load_asr()`、`transcribe_*()` | 使用 sherpa-onnx 加载 SenseVoice，把16kHz单声道录音识别为文字 |
| `ContinuousMicrophone` | 通过 Linux 的 arecord 连续收音、检查音频是否到达、管理录音子进程 |
| `SpeechSegmenter` | 按音量和静音间隔切出一句话；启动时测背景音，再设置阈值 |
| `WakeGate`、`exact_voice_command()` | 检查“小狗”唤醒词和完整短指令，管理一次唤醒和8秒指令窗口 |
| `load_tts()`、`dog_say()` | 用 MeloTTS 离线合成回答，缓存语音并交给播放队列 |
| `QueueAudioTrack` | 将语音转换为 WebRTC 使用的48kHz双声道音频，维持常驻音频轨道 |
| `_joystick_walk()`、`stop_walk()` | 发送移动摇杆输入，并在定时结束或任务取消时发送归零 |
| `run_control()` | 把声音、命令、模式检查、音量设置和动作执行串成完整流程 |

免回车模式使用识别后的文字判断唤醒，尚未使用专用关键词唤醒模型。播报期间暂停接收下一条指令，避免把自己的回答识别成命令，当前不支持边播报边语音打断。

移动通过无线手柄话题以约50Hz发送摇杆输入，每次持续2秒后归零；这里的摇杆数值不是米每秒。站立、趴下、比心等通过运动服务的动作接口发送。程序调用狗现有的运动能力，没有实现底层步态算法。

## 4. 文件夹其余内容怎么分

### 原有开源通信库和例子

| 文件或目录 | 用途 |
|---|---|
| `unitree_webrtc_connect/` | 基础通信库，处理 WebRTC 连接、认证、收发数据、音视频与雷达等能力 |
| `unitree_webrtc_connect/webrtc_driver.py` | 连接对象的主要入口，提供 connect、disconnect 等操作 |
| `unitree_webrtc_connect/webrtc_datachannel.py`、`msgs/` | 数据通道、消息请求与应答、心跳和验证等基础通信 |
| `unitree_webrtc_connect/webrtc_audio.py`、`webrtc_video.py` | 音频与视频通道支持 |
| `unitree_webrtc_connect/constants.py` | 话题名称、动作 API 编号等常量 |
| `unitree_webrtc_connect/lidar/` | 雷达解码；不属于本轮语音功能的核心 |
| `examples/` | 上游通信示例，包括不同机器人型号的动作、音视频和状态读取；不是语音主程序 |
| `README.md` | 当前 GitHub 仓库首页：介绍板载语音项目、主程序和使用入口 |
| `UPSTREAM_README.md` | 保留的上游通信项目原始说明，主要介绍通信库 |
| `pyproject.toml`、`MANIFEST.in` | 基础通信库的打包配置；其中依赖不等于完整板载语音环境清单 |
| `LICENSE` | 上游 MIT 许可证，复制分发相关代码时保留 |
| `images/`、`.github/` | 上游图片和项目配置；本语音项目不上传原有 PyPI 发布工作流 |

本地元数据记录通信库名称为 `unitree_webrtc_connect`、版本2.2.0、作者账号 legion1581，来源为 <https://github.com/legion1581/unitree_webrtc_connect>；LICENSE 署名 Konstantin Severov。
这些基础能力不能全部算成本轮语音开发原创。语音模型的使用与分发还需遵守各模型自身许可。

### PC 原型与早期单项测试

| 文件 | 用途 |
|---|---|
| `voice_control.py` | 早期 PC 语音闭环原型：电脑麦克风、faster-whisper 识别，优先 edge-tts 在线合成并尝试 Windows 离线回退，再控制狗；与当前板载离线版本分开看 |
| `record_test.py` | 电脑麦克风录音与回放测试 |
| `whisper_test.py` | 电脑端录音与 Whisper 识别测试 |
| `speaker_test.py` | 连接狗、设置音量并播放示例音频，使用 examples 内的狗叫音频测试扬声器 |
| `tts_speaker_test.py` | 用在线 edge-tts 生成语音，测试狗的扬声器 |
| `tts_offline.py` | 使用 Windows 系统 TTS 的离线播报实验，不是当前板载 MeloTTS 实现 |
| `audio_check.py` | 接收狗自带麦克风的 WebRTC 音频并观察音量、保存录音；区别于当前 USB 麦克风输入方案 |
| `safe_test.py` | 早期站立、趴下、移动等动作串联测试；会实际发送动作，不能因为文件名带 safe 就随意运行 |
| `move_test.py`、`move_test2.py` | 排查早期 Move 指令和无线手柄通道的运动效果；会连接并控制机器人 |

PC 原型使用的在线合成和当前板载离线合成不同，因此不能笼统说“整个文件夹里的所有程序都离线”。

### 连接故障排查工具

| 文件 | 当时解决的问题 |
|---|---|
| `board_test.py` | 最小板载连接测试：连接主控并读取当前模式，不发送运动指令 |
| `connection_probe.py` | 输出依赖版本、ICE/DTLS阶段和连接结果，支持语音库导入顺序对照 |
| `connection_compare.py` | 把不同导入条件、启动条件放到独立子进程中比较 |
| `slot_timing_test.py` | 比较长间隔和短间隔重连，区分会话释放/信令限流与握手问题 |
| `import_connection_bisect.py` | 对旧主程序顶层语句构造前缀并分组/二分，查找可能的导入边界，保留各次日志 |

这些工具记录了排查过程，不是部署主程序前必须逐一运行的步骤。部分工具针对的是旧单文件结构；当前程序已经拆分，旧实验的含义随源码变化，不能照搬旧结论。当前正常使用直接从主入口的检查参数开始。

### 自动检查与回归测试

| 文件 | 覆盖范围 |
|---|---|
| `test_board_startup.py` | 延迟导入边界、启动顺序、连接失败与断线处理、退出清理 |
| `test_board_asr_api.py` | SenseVoice 构造 API 与 TTS 模型指针跳过 |
| `test_board_offline.py` | 音频转换、录音格式、异步锁和移动任务归零 |
| `test_board_hands_free.py` | 唤醒门控、完整指令匹配、音量分句、播报期间丢弃录音、麦克风进程回收 |
| `test_board_cli.py` | 命令行参数、默认值及只试听模式的行为边界 |
| `test_board_service.py` | 安装器、普通用户运行、服务配置、重启间隔和 Python3.8 语法兼容 |
| `test_voice_control.py` | 早期 PC 原型的音频与移动清理回归 |

已有本地记录为上述七个测试文件合计56项通过。它们主要验证程序逻辑，不等于56项实机运动实验，也不能证明长期稳定或机械状态正常。本次只整理文档，没有重新执行测试。

### 运行生成物与编辑器目录

`*.wav`、`tts_cache_*`、部分 `*.mp3`、`*.audio` 是录音、语音缓存或历史实验输出；有的旧缓存长度为0，不是必需的模型文件。
`__pycache__/` 是 Python 缓存，`.mimosa/` 是编辑工具相关目录，均不是语音控制应用的核心。
分享应用时通常不需要附带这些生成物；个人录音也不必放入分享包。缓存缺失时可由程序重新合成。

## 5. 文档先看哪份

| 文档 | 建议用途 |
|---|---|
| 本文 `PROJECT_OVERVIEW_ZH.md` | 先看：项目目标、架构、成果、文件用途和复用范围 |
| `BOARD_V8.md` | 免回车唤醒、试听、麦克风、音量参数的使用说明 |
| `BOARD_SERVICE.md` | 当前后台服务、安装、停止、开机自启和日志查看 |
| `BOARD_V7.md` | 解释为何拆分入口与运行模块，以及启动故障的处理 |
| `OFFLINE_CHECKS.md` | 模型自检、音频测试与历史排查；部分状态描述属于旧阶段 |
| `DEPLOY.md` | 环境、早期部署和变更记录；旧段落可能已经被后续版本替代 |
| `go2-voice-handoff-v3.md` | 详细历史交接材料，保留了失败方案与假设；从顶部最新补充开始读 |

文档里“尚未完成”的历史描述可能已被后来日志覆盖。当前状态以本文、`BOARD_SERVICE.md` 的最新验收记录以及实际运行结果为准，不要把长交接文档中间的旧结论当成当前状态。

## 6. 本轮主要解决了哪些工程问题

1. **识别模型接口不匹配**：改用当前 sherpa-onnx 包支持的 `OfflineRecognizer.from_sense_voice()`。
2. **把 Git LFS 指针误当模型**：MeloTTS 的 `model.int8.onnx` 实际是133字节占位文本，程序识别并跳过，使用170429550字节的真正 `model.onnx`。
3. **板载完整程序握手失败**：拆成轻量入口和语音运行模块，连接验证后才导入语音依赖；保留退出时的握手任务清理，不再使用失败的实验性握手调度补丁。
4. **Python3.8 兼容**：使用 executor 在线程中执行阻塞工作，避免依赖较新版本才有的 `asyncio.to_thread()`；播报锁延后创建。
5. **连续录音无数据**：实际问题是旧数字卡号指向了 Jetson 内部 APE，改用 USB 设备名 `plughw:CARD=Device,DEV=0` 后收音成功。
6. **免回车与唤醒**：加入自动分句、背景音校准、一次唤醒一条指令、完整指令匹配和播报期间暂停听取。
7. **音频与退出管理**：复用常驻音轨，移动结束/取消时归零，停止录音后回收子进程，一次 Ctrl+C 完成清理。
8. **后台部署**：新增 systemd 服务、安装前检查和失败后以新进程重试的配置，已经完成首次服务启动。

这些是本项目从“能在电脑上试一下”走到“能在板上持续等待语音指令”的主要集成工作。

## 7. 已经证明的成果和仍未证明的部分

### 已有用户实机记录

- ASR/TTS 模型加载与离线合成、转换、识别自检通过。
- 当前分离入口方案的 WebRTC 启动、加载模型和正常断开通过。
- 手动模式下多条语音指令的识别、动作与播报闭环得到用户正面反馈。
- 免回车模式下“小狗”唤醒、前进、右转、打招呼、比心及“笔心”别名匹配得到用户实测反馈。
- 播报后恢复听取、一次 Ctrl+C 清理退出通过。
- systemd 安装与启用成功，首次后台日志确认连接、模型、音量10、USB第一帧音频和“可以说话了”。

### 仍需按实际设备验收

- 当前对话未收到退出 SSH/拔掉电脑网线后继续语音工作的明确实测结果；架构上不依赖电脑常驻。
- 已设置过开机自启，但未收到正常重新开机后的实际自动启动验收。之后讨论过关闭自启，当前是否开启需在板上查询，不能由历史安装日志推断。
- 断线重启机制已经实现并做本地逻辑检查，尚未完成实机断线恢复和长时间运行验收。
- 没有做全部列出动作逐项成功、识别准确率、端到端延迟或长时步态稳定性的系统测量。
- 用户近期反馈遥控行走也有不稳；原因未确认，不能把语音功能完成等同于机器人机械与运动状态验收通过。

## 8. 别人复用时需要带走什么

### 应用代码和说明

至少提供 `voice_control_board.py`、`voice_control_board_runtime.py`，以及本文和相关使用说明。
需要后台或开机运行时，再提供 `go2-voice.service`、`install_voice_service.py`。建议附带自动测试和匹配的依赖版本说明，便于以后修改验证。

还需要安装或提供当前匹配的 `unitree_webrtc_connect` 基础通信库。只复制两个 Python 文件到一台新电脑/新板子，并不能保证立即运行。

### 模型和依赖不全在这个文件夹里

板上实际模型位于：

```text
/home/unitree/go2_voice/models/
├── sensevoice/
│   ├── model.int8.onnx
│   └── tokens.txt
└── melo_tts/
    ├── model.onnx
    ├── lexicon.txt
    ├── tokens.txt
    ├── dict/
    └── 模型包配套的文本归一化文件（如 *.fst）
```

MeloTTS 应保留完整匹配的模型包内容，不要仅拿一个 ONNX。当前代码会跳过 Git LFS 指针，但不意味着自动验证了任意文件都是兼容模型。

Windows 的相关材料在本项目目录之外：

- `C:\yushu\board_wheels\`：板载离线依赖安装材料。
- `C:\yushu\sensevoice_model.tar.bz2`：识别模型压缩包。
- `C:\yushu\melo_tts_model.tar.bz2`：语音合成模型压缩包。
- `C:\yushu\asr_model\`：其他 ASR 模型材料；不要与板载当前使用的 SenseVoice 路径混淆。

新板子还需要 Linux 音频工具 arecord，以及 numpy、av、sherpa_onnx、aiortc 和基础通信库等依赖。`install_voice_service.py` 只检查现有环境，不自动补齐缺失项。

已观察到的板上环境为 Jetson Orin NX、Ubuntu20.04、Python3.8.10，aiortc1.9.0、av12.3.0、numpy1.24.4、sherpa_onnx2.0.1。Windows 本地检查使用 Python3.11 与 aiortc1.15，不能据此假定两边版本完全一致。

### 别人需要改什么

| 配置 | 位置或检查方式 |
|---|---|
| 狗主控 IP | 当前实际连接使用 `voice_control_board.py` 中 `ROBOT_IP` |
| 模型、录音、TTS缓存路径 | runtime 中 `MODELS`、`REC_WAV` 及 `dog_say()` 的缓存目录；不全是一个配置变量 |
| 麦克风 | 用 `arecord -l` 核对，运行时通过 `--mic-device` 指定；当前设备名不保证适用于别人的USB声卡 |
| 音量、唤醒词、录音门槛 | `--volume`、`--wake-word`、`--vad-threshold` |
| 支持词、动作、回答、移动时间 | runtime 中 `COMMANDS`、`WALK_SECONDS` |
| 后台运行用户和路径 | service 中的 User、Group、HOME、WorkingDirectory、ExecStart、ExecStartPre；安装器也固定了路径和用户检查，需同步适配 |

完整运行会尝试切到 normal 模式，可能让狗起身；停止服务不会自动切回此前模式。语音“停”有分句、识别和处理延迟，且播报中不听下一条指令，不能当作硬件急停。

## 9. 常用运行和管理命令

以下均在板子 SSH 终端执行。服务与手动程序二选一，避免同时占用连接和麦克风。

```bash
# 只加载模型，不连接狗
python3 ~/go2_voice/voice_control_board.py --check-models

# 离线合成、音频转换和识别自检，不连接狗
python3 ~/go2_voice/voice_control_board.py --self-test

# 仅麦克风、识别、唤醒与命令匹配；不连接狗、不播报、不做动作
python3 ~/go2_voice/voice_control_board.py --listen-only

# 检查连接和模型启动；不切模式、不发动作
python3 ~/go2_voice/voice_control_board.py --check-startup

# 手动运行免回车控制；前提是后台服务已停止
python3 ~/go2_voice/voice_control_board.py --hands-free --volume 10

# 仅停止服务，保留下次开机自启设置
sudo systemctl stop go2-voice

# 停止服务，并取消开机自启
sudo systemctl disable --now go2-voice

# 只恢复下次开机自启，现在不启动
sudo systemctl enable go2-voice

# 恢复开机自启，并立即启动（仅限已安装服务的设备）
sudo systemctl enable --now go2-voice

# 查看自启设置、运行状态和最近日志
systemctl is-enabled go2-voice
systemctl status go2-voice --no-pager
journalctl -u go2-voice -n 60 --no-pager
```

刚停止连接后，留出90秒再手动启动或跑下一个连接测试。手动 start/enable --now 不会自动替你等 RestartSec。新设备首次安装请按 `BOARD_SERVICE.md` 操作。

默认不带 `--hands-free` 运行入口仍是“回车后录3秒”的调试方式；后台服务明确带了该参数，因此不需要回车。

## 10. 分享时建议讲清楚的价值

可以介绍为：**基于现有机器人控制能力和离线语音模型，实现了 Go2 EDU 板载中文语音交互闭环，并解决了模型兼容、连接启动、USB收音、唤醒门控、音频播放和后台部署等工程问题。**

适合别人借鉴的是整体链路、模块划分、实机排查经验及可复用代码。复用者仍需匹配自己的机器人固件、网络、声卡和模型环境，先做只识别和启动检查，再进行动作实测。
