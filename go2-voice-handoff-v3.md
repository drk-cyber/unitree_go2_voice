> 2026-11-17 最新验收：板上 `install_voice_service.py --check` 与 `sudo python3 ~/go2_voice/install_voice_service.py --enable` 成功，go2-voice.service 已安装、启用开机运行并完成首次启动。后台日志确认 WebRTC 验证、ASR/TTS 加载、音量10/10、USB麦克风第一帧音频与“可以说话了”（背景音1972、门槛5030）。后台实际语音指令、退出SSH后工作、正常重新开机后自动恢复、断线恢复和长期运行仍待验证，见 [BOARD_SERVICE.md](BOARD_SERVICE.md)。此前用户13:19:58–13:22:30前台运行中前进/右转/笔心匹配、播报后听取和一次Ctrl+C退出正常，反馈“挺好的”。
>
> 2026-11-17 v7 更新：当前入口已拆分为 voice_control_board.py + voice_control_board_runtime.py。
> 连接验证成功后才导入语音模块，并非仅延迟加载模型。最新操作和验证范围见 [BOARD_V7.md](BOARD_V7.md)。
> 本地检查通过；用户板上 v7 --check-startup 已成功：11:24:33 连接验证通过，ASR/TTS 加载完成，11:24:41 正常断开，无此前清理异常。完整语音控制仍待验收。以下旧版本、二分脚本及根因推测作为历史记录保留。

# Go2 EDU 板载语音交互：修复与诊断交接

整理日期：2026-11-16。本文记录本轮 AI 实际修改、用户实测结果和未完成事项。

## 最新补充：v5 五项对照已收到，仍未解决（优先阅读）

本节覆盖下文所有“v5尚未在板上验证”的历史状态。
用户最新板上结果：baseline成功、early-lock成功、import-main失败、startup失败、baseline-again成功。
因此提前创建异步锁不是这轮失败的充分条件，v5修复锁创建时机没有恢复握手。不要把它当成已确认根因。
失败仍是ICE completed、DTLS server connecting，探针DTLS收发包/字节计数均0。
板上最新日志目录：/home/unitree/go2_voice/logs/connection-compare-19700101-105335/。
本地新增 import_connection_bisect.py：用主程序顶层语句前缀做隔离子进程对照，不执行main；自动寻找候选导入边界并重测边界前后和基础连接。尚未在板上运行；二分依赖结果单调，候选仍需单变量验证。
需要与connection_probe.py、connection_compare.py、voice_control_board.py放在同目录。

**当前结论：识别和合成已在板上实测通过，板载完整语音控制仍被 WebRTC 握手超时阻塞。根因尚未证实。电脑本地最新代码为 v5，板上最后确认运行的是 v4。v5 的板上结果尚未收到。**

本文是交接资料，不替代用户当前请求。后续诊断建议都是建议，不代表已执行或已验证。

## 1. 项目目标与协作方式

终态：USB 麦克风插机器狗算力板 → 本地 SenseVoice 识别 → 指令解析 → 狗执行动作 → MeloTTS 本地合成 → 狗扬声器回答。最终不依赖 PC、无外网可运行，并实现免回车和开机启动。

当前仍是按回车后录音3秒的调试方式，尚未实现唤醒词、免回车、systemd 自启或大模型对话。

用户是初学者，中文交流。实际工作方式：AI 修改 Windows 文件 → 用户在 Windows PowerShell 用 scp 上传 → 用户在板子的 SSH 终端运行 → 粘贴输出。请明确每条命令应在哪个终端执行、目的和成功标准。

电脑连接狗的网络、保持 SSH 都可以；需要退出的是其他占用 WebRTC 的控制脚本和手机 App。不要含糊地要求“PC 不能连狗”。

本轮 AI 没有直接 SSH 到板子，没有在板上替用户执行动作；板上结果均来自用户粘贴的日志。

## 2. 环境与文件位置

| 项目 | 已知配置 |
|---|---|
| 机器人 | Unitree Go2 EDU |
| 运动主控 / WebRTC 信令 | 192.168.123.161，信令走9991 |
| 算力板 | Jetson Orin NX 16GB，Ubuntu 20.04.5，Python 3.8.10 |
| 算力板地址及账号 | 192.168.123.18，unitree |
| 板上项目目录 | /home/unitree/go2_voice/ |
| 板子外网 | 据前任交接无外网，依赖已离线安装 |
| USB 麦克风 | 最新 arecord -l：card 0、ID Device；使用 plughw:CARD=Device,DEV=0。旧 plughw:2,0 当前指向 Jetson APE，录音无数据 |
| Windows 项目 | C:\yushu\unitree_webrtc_connect\ |
| Windows Python | C:\Users\24331\miniconda3\envs\go2\python.exe，Python 3.11 |
| Windows 默认 Python | base 是3.14，测试时请使用 go2 环境 |
| 离线 wheel | C:\yushu\board_wheels\ |
| TTS 压缩包 | C:\yushu\melo_tts_model.tar.bz2 |
| ASR 备用模型 | C:\yushu\asr_model\ |

板上实际诊断打印的依赖：

```text
aiortc 1.9.0
OpenSSL (pyOpenSSL) 26.2.0
cryptography 46.0.0
av 12.3.0
numpy 1.24.4
sherpa_onnx 2.0.1
OpenSSL 3.5.3 16 Sep 2025
```

板上实际导入通信库：

```text
/home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
```

**Windows 本地 aiortc 是1.15.0，与板上的1.9.0不同。Windows 实连回归检查通过不等于板上版本通过。**

额外线索：板上自动日志目录为 `connection-compare-19700101-104644`，说明板上系统日期很可能仍在1970年，尚未核对/修正。基础连接在同一系统时间条件下能成功，因此不能单凭此点断言它是根因。

## 3. 已修复并获板上验证的问题

### 3.1 SenseVoice 构造 API 错误

原报错：

```text
_recognizer = sherpa_onnx.OfflineRecognizer(config)
TypeError: OfflineRecognizer() takes no arguments
```

本轮直接读取离线 wheel 的 Python 源码确认：顶层 OfflineRecognizer 是包装类，没有接受 config 的构造器；但 **from_sense_voice 并未移除**。

采用：

```python
_recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=model_path,
    tokens=tokens,
    num_threads=2,
    provider="cpu",
    language="",
    use_itn=True,
)
```

前任 v2 文档凭二进制字符串推断应使用 `OfflineRecognizer.create(config)`，且说 from_sense_voice 已移除；这与本地实际2.0.1 wheel源码不符。不要沿用这个猜测。

`OfflineTts(config)` 实际已在板上成功，不需要改成猜测的 `.create(config)`。

### 3.2 合成模型是 Git LFS 指针

原报错：

```text
Load model from .../melo_tts/model.int8.onnx failed:Protobuf parsing failed.
```

读取 PC 保存的压缩包确认：

- model.int8.onnx 只有133字节，内容以 `version https://git-lfs.github.com/spec/v1` 开头，声明真正文件应有53517430字节。
- 同包 model.onnx 为170429550字节。
- 实际模型目录还包含 dict/、lexicon.txt、tokens.txt、date.fst、number.fst、phone.fst、new_heteronym.fst；不是 espeak-ng-data 方案。

新增 select_tts_model：优先int8，但跳过缺失文件、空文件及 Git LFS 指针，选用 model.onnx。

模型加载和完整离线推理已通过用户板上自检：

```text
识别模型加载完成
[TTS] 跳过 Git LFS 占位文件: /home/unitree/go2_voice/models/melo_tts/model.int8.onnx
[TTS] 使用模型: /home/unitree/go2_voice/models/melo_tts/model.onnx (170429550 字节)
合成模型加载完成
[自检] 合成耗时 1.82秒，文件: /home/unitree/go2_voice/self_test_tts.wav
[自检] WebRTC 音频转换通过，时长 1.20秒
[自检] 识别耗时 0.12秒，结果: 你好，请站起来。
[自检] 匹配指令: ('action', ('StandUp', '好的，我起来了'))
离线语音自检通过；未连接机器人、未发送动作。麦克风和扬声器仍需实测。
```

这验证的是合成文件→转换→识别链路，**没有验证板上真实麦克风到动作/扬声器的完整闭环**。

## 4. 其他代码修复与能力

| 修改 | 当前验证范围 |
|---|---|
| run_in_thread 使用 run_in_executor + partial，替换 Python3.8 没有的 asyncio.to_thread | 板上自检已用到 |
| main 声明 global _walk_task，避免走路任务保存到局部变量、停止却读全局变量 | 本地检查，板载动作取消待实测 |
| 走路使用 monotonic 截止时间，finally 发送摇杆归零 | 本地取消任务检查通过 |
| QueueAudioTrack 解码放线程，入队回到事件循环；补重采样flush尾帧 | 本地及板上音频转换自检通过 |
| dog_say 按实际PCM分帧时长等待，替代按字数猜时间 | 狗扬声器效果待板载实测 |
| connect/setup 异常时主动 disconnect | 用户日志已看到断开 |
| 转录函数校验16kHz/单声道/16位PCM WAV，并拒绝空文件 | 本地检查通过 |
| 新增只加载模型、离线自检、识别已有WAV、连接与启动诊断参数 | 详见下文 |

识别标记清理仍用 `re.sub(r"<\|[^|]*\|>", "", text)`。命令仍是关键词包含式，不是LLM。

## 5. 当前阻塞：连接探针成功，主程序导入后握手超时

### 5.1 主报错

```text
Data channel setup timed out after 15s
peer=connecting, ice=completed
DTLS server: State.NEW -> State.CONNECTING
```

ICE 成功不能证明 DTLS 成功。此前曾过早归因于其他客户端占用，但这并未被证实。

超时清理时有时附带：

```text
Task exception was never retrieved
aiortc.exceptions.InvalidStateError: RTCIceTransport is closed
```

这段出现在断开过程中，应与最初的DTLS握手失败分开分析。v4调度兼容处理可消除该清理异常，但没有解决握手超时。

### 5.2 分阶段实测

1. `board_test.py` 曾成功连接、验证，读取模式 `{"form":"0","name":"mcf"}`，干净断开。
2. 主程序加载模型后连接多次超时。
3. 主程序 `--check-connection`（不加载模型，但导入完整主程序）也失败。
4. `--check-connection-with-models` 也失败，因此模型初始化不是失败的必要条件。
5. 独立 `connection_probe.py` 成功；加 `--speech-imports before` 先导入 numpy/av/sherpa_onnx 后仍成功。
6. 主程序改为先连接、线程中加载模型（v3），仍在模型开始加载前失败。
7. aiortc 1.9.0 连接任务串行处理（v4）仍失败，但清理异常消失。

### 5.3 最有价值的四项隔离对照（用户最后一次完整实测）

运行：`python3 ~/go2_voice/connection_compare.py`。每项独立子进程，结束后间隔30秒。

| 项目 | 做什么 | 实测结果 |
|---|---|---|
| baseline | 独立探针，正常导入通信库 | 成功，DTLS收包9、发包9 |
| import-main | 先 import voice_control_board，不调用main，再跑同一探针 | 失败，DTLS收包0、发包0 |
| startup | 导入主程序，调用 --check-startup --legacy-handshake | 失败，模型还没加载就超时 |
| baseline-again | 再运行独立探针 | 成功，DTLS收包9、发包9 |

成功时远端协商：BUNDLE 0 1 2，audio/video/application，setup:active；本地DTLS为server，协商SRTP_AES128_CM_SHA1_80。

同轮前后基线都成功，**主程序导入阶段的差异是目前最强线索**；但还需单变量验证，不能说具体根因已确认。DTLS计数0仅表示该层没有统计到包，不代表网卡/UDP完全没有收包，ICE本身已有交互。

板上完整日志：`/home/unitree/go2_voice/logs/connection-compare-19700101-104644/`，包含每项log和summary.txt。

用户粘贴的该轮完整输出也附在本文末尾。

## 6. v3/v4/v5 改动及当前实际版本

### v3：先连接再加载模型——已实测，没有解决握手

connect_and_load 先 await conn.connect，再用 run_in_thread 加载ASR/TTS，避免连接后同步初始化模型阻塞心跳。
新增 `--check-startup`：成功加载后检查 pc.connectionState，直接disconnect返回，不切模式、不调音量、不发动作。

### v4：连接任务调度兼容——已实测，未解决握手

读取板子离线 aiortc1.9.0 wheel源码发现 setLocalDescription 和 setRemoteDescription 都 schedule 私有 __connect。
新增进程内子类 make_ordered_peer_connection：完整remoteDescription存在才运行、连接任务加锁串行、close前cancel并gather任务。
通过替换 webrtc_driver.RTCPeerConnection 引用生效，**未修改板上site-packages或安装依赖**。

本地PC的1.15.0进行了两端真实WebRTC握手和数据发送检查；1.9.0的板上v4仍DTLS超时。
因此不能把“源码有两次调度”当作已确认bug根因，v5已取消默认启用此处理。

### v5：移除导入阶段的全局异步锁——PC已改，板上尚未验证

旧代码在模块顶层 `_tts_lock = asyncio.Lock()`。
Python3.8的Lock构造会取得并绑定事件循环；此时 asyncio.run 尚未开始，之后会创建另一个循环。
这一做法至少有锁绑定旧循环的风险，但**尚未证明它导致DTLS超时**。

已改为 `_tts_lock = None`，首次进入 dog_say 协程时才创建锁。

默认恢复原始RTCPeerConnection，只保留DTLS定向日志。v4处理需显式 `--ordered-handshake` 开启；`--legacy-handshake` 强制原始实现。

connection_compare.py 同时从4项改为5项：

```text
baseline
early-lock       # 唯一增加：在asyncio.run之前创建并保留一个asyncio.Lock
import-main      # 导入修复后的v5主程序
startup          # 调用v5 --check-startup --legacy-handshake
baseline-again
```

如果early-lock失败而前后baseline及v5 import-main/startup成功，才更支持提前创建循环与故障有关；如果结果不同，应重新评估，而不是继续坚持该假设。

**交接时用户还没有提供v5上传或运行结果。板上最后确认运行的是v4；不要把Windows当前文件状态当作板上状态。**

## 7. 当前文件清单

均位于 `C:\yushu\unitree_webrtc_connect\`：

| 文件 | 用途 |
|---|---|
| voice_control_board.py | 最新v5板载程序，待板上验证 |
| voice_control.py | PC版，前任及用户记录已跑通；本轮也修过任务取消和播报逻辑 |
| board_test.py | 原始最小连接+读模式检查，曾成功 |
| connection_probe.py | 独立只连接探针，DTLS日志、依赖版本、精简SDP摘要，曾成功 |
| connection_compare.py | 最新5项子进程对照；此前4项版已经在板上复现差异 |
| test_board_asr_api.py | 读取实际2.0.1 wheel Python包装源码的接口回归，以及LFS指针选择检查 |
| test_board_offline.py | 不依赖板载ASR原生库的音频、任务、导入及本地WebRTC检查 |
| test_voice_control.py | 前期PC版回归检查 |
| DEPLOY.md | 部署记录；前半部分有历史陈旧描述，不能全部照抄 |
| OFFLINE_CHECKS.md | 自检、连接诊断操作及版本演进说明 |

目前测试共10项通过（test_board_asr_api + test_board_offline），以及Python3.8语法解析通过。
其中部分测试用AST提取函数并模拟原生依赖，验证的是接口调用/控制逻辑，不是真实SenseVoice或MeloTTS推理。
真实模型推理已由用户的板上self-test验证；Windows没有板上相同环境。

## 8. 当前可用命令（用途必须区分）

| 参数 | 是否连接狗 | 是否发动作 | 含义 |
|---|---|---|---|
| --help | 否 | 否 | 看当前版本支持的参数 |
| --check-models | 否 | 否 | 加载ASR/TTS |
| --self-test | 否 | 否 | 合成、转换、识别；输出测试WAV和耗时 |
| --transcribe-wav PATH | 否 | 否 | 识别16kHz单声道16位PCM录音 |
| --check-connection | 是 | 否 | 不初始化模型的主程序连接检查 |
| --check-connection-with-models | 是 | 否 | 模型初始化后连接检查 |
| --check-startup | 是 | 否 | 先连接、后加载模型，检查后断开 |
| 无参数 | 是 | 是 | 切normal、设置音量，进入按回车语音控制 |

无参数运行可能切换模式，不能拿它当只读诊断。

## 9. 接手后建议的首轮操作

先阅读本地v5代码，检查是否接受此前推测和改动，再决定是否执行对照。无需再次下载或重装全部依赖。

若继续验证v5，Windows PowerShell上传两个文件：

```powershell
scp C:\yushu\unitree_webrtc_connect\voice_control_board.py C:\yushu\unitree_webrtc_connect\connection_compare.py unitree@192.168.123.18:/home/unitree/go2_voice/
```

板子SSH终端运行：

```bash
python3 ~/go2_voice/connection_compare.py
```

约4分钟，自动保存日志，不切模式、不发动作。关注early-lock与import-main差异。

如果v5仍失败，建议进一步缩小import副作用，并在同一板上Python3.8环境验证；必要时看UDP/DTLS实际收包或依赖内部使用的事件循环/文件描述符。不要只因0计数就断言手机占用、证书错误或网络断开。

本地回归命令（Windows PowerShell）：

```powershell
& 'C:\Users\24331\miniconda3\envs\go2\python.exe' -m unittest test_board_asr_api test_board_offline -v
```

连接稳定之后再做麦克风/扬声器/动作验收，之后才做唤醒词和systemd。当前input交互不适合直接设为开机常驻服务。

## 10. 固件与运动信息（来自前任交接，未在本轮重新实测）

- Move(1008)被此固件拒收，移动使用rt/wirelesscontroller，50Hz摇杆，归零停止。
- 前进ly正、后退ly负；v2交接称实测左转rx负、右转rx正，板载代码按此实现。
- mcf切normal，MOTION_SWITCHER查询api1001、切换api1002，等待8秒。
- 单客户端WebRTC互斥存在，但本轮故障不能仅靠通用错误提示归因于占用。
- 音频使用常驻轨道，48kHz/s16/立体声，960采样每帧，不要未经验证就反复addTrack/replaceTrack。
- 动作执行期间可能忽略新命令，需要逐项验证动作间隔。
- 语音停止有录音和识别延迟，不等于遥控器急停。

## 11. 本轮诊断的局限与需坦诚纠正的地方

本轮曾多次给出待验证假设（模型加载时序、连接任务重叠），但前两者的改动均未解决板上握手。不要在交接时把这些推测写成事实。
目前最可靠证据是四项隔离对照，而不是“本地测试通过”或通用超时提示。
v4清理异常消失是一项改善，不代表原始DTLS故障已解决。v5也只是待证实的进一步缩小。

## 12. 用户最后一次四项对照原始输出

以下为用户提供的完整日志，保留原始时间和报错，便于独立判断。

```text
unitree@ubuntu:~$ python3 ~/go2_voice/connection_compare.py
开始4项对照，约3分钟。每项结束后间隔30秒。Ctrl+C 可退出。
[日志目录] /home/unitree/go2_voice/logs/connection-compare-19700101-104644

[对照 1/4] baseline
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:46:45)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:46:45)
[握手 0.0s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:46:45)
🕒 ICE Gathering State      : 🟡 gathering     (10:46:45)
🕒 ICE Gathering State      : 🟢 complete      (10:46:45)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:46:45)
🕒 ICE Connection State     : 🔵 checking      (10:46:45)
🕒 Peer Connection State    : 🔵 connecting    (10:46:45)
🕒 Signaling State          : 🟢 stable        (10:46:45)
[握手 0.3s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:46:45)
[DTLS] RTCDtlsTransport(server) x DTLS handshake negotiated SRTP_AES128_CM_SHA1_80
[DTLS] RTCDtlsTransport(server) - DTLS handshake complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.CONNECTED
🕒 Peer Connection State    : 🟢 connected     (10:46:45)
🕒 Data Channel Verification: ✅ OK            (10:46:46)
[传输计数] DTLS=connected 收包=9 发包=9 收字节=1814 发字节=2214
[结果] 连接及验证成功；没有发送动作。
[握手 0.5s] peer=connected ICE=completed DTLS/ICE/role=(('connected', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
🕒 Signaling State          : ⚫ closed        (10:46:47)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTED -> State.CLOSED
🕒 ICE Connection State     : ⚫ closed        (10:46:47)
🕒 Peer Connection State    : ⚫ closed        (10:46:47)
🕒 WebRTC connection        : 🔴 disconnected  (10:46:47)

[间隔] 等待30秒释放会话...

[对照 2/4] import-main
onnxruntime cpuid_info warning: Unknown CPU vendor. cpuinfo_vendor value: 0
[主程序文件] /home/unitree/go2_voice/voice_control_board.py
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:47:19)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:47:19)
[握手 0.0s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:47:19)
🕒 ICE Gathering State      : 🟡 gathering     (10:47:19)
🕒 ICE Gathering State      : 🟢 complete      (10:47:19)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:47:19)
🕒 ICE Connection State     : 🔵 checking      (10:47:19)
🕒 Peer Connection State    : 🔵 connecting    (10:47:19)
🕒 Signaling State          : 🟢 stable        (10:47:19)
[握手 0.3s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:47:19)
[握手 0.5s] peer=connecting ICE=completed DTLS/ICE/role=(('connecting', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
[传输计数] DTLS=connecting 收包=0 发包=0 收字节=0 发字节=0
[结果] DataChannelTimeoutError Data channel setup timed out after 15s — the WebRTC peer never reached `connected` (peer=connecting, ice=completed). This usually means ICE/DTLS failed — another WebRTC client is probably holding the slot, the network is flaky, or a stale peer on the robot hasn't been released yet. Try again in 10–20 s, or kill any other connected client (mobile apk, second script).
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
🕒 Signaling State          : ⚫ closed        (10:47:34)
🕒 ICE Connection State     : ⚫ closed        (10:47:34)
🕒 Peer Connection State    : ⚫ closed        (10:47:34)
[DTLS] RTCDtlsTransport(server) x DTLS handshake failed (connection error)
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.FAILED
ERROR:asyncio:Task exception was never retrieved
future: <Task finished name='Task-13' coro=<RTCPeerConnection.__connect() done, defined at /home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py:1008> exception=InvalidStateError('RTCIceTransport is closed')>
Traceback (most recent call last):
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py", line 1016, in __connect
    await iceTransport.start(self.__remoteIce[transceiver])
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcicetransport.py", line 312, in start
    raise InvalidStateError("RTCIceTransport is closed")
aiortc.exceptions.InvalidStateError: RTCIceTransport is closed
🕒 WebRTC connection        : 🔴 disconnected  (10:47:34)

[间隔] 等待30秒释放会话...

[对照 3/4] startup
onnxruntime cpuid_info warning: Unknown CPU vendor. cpuinfo_vendor value: 0
[主程序文件] /home/unitree/go2_voice/voice_control_board.py
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
[握手兼容] aiortc 1.9.0，使用原始连接实现。
[启动 v4] 先建立连接，再加载语音模型；模型就绪前不发送动作。
🕒 WebRTC connection        : 🟡 started       (10:48:06)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:48:06)
🕒 Signaling State          : 🟡 have-local-offer (10:48:06)
🕒 ICE Gathering State      : 🟡 gathering     (10:48:06)
🕒 ICE Gathering State      : 🟢 complete      (10:48:06)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:48:06)
🕒 ICE Connection State     : 🔵 checking      (10:48:07)
🕒 Peer Connection State    : 🔵 connecting    (10:48:07)
🕒 Signaling State          : 🟢 stable        (10:48:07)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:48:07)
启动失败: DataChannelTimeoutError Data channel setup timed out after 15s — the WebRTC peer never reached `connected` (peer=connecting, ice=completed). This usually means ICE/DTLS failed — another WebRTC client is probably holding the slot, the network is flaky, or a stale peer on the robot hasn't been released yet. Try again in 10–20 s, or kill any other connected client (mobile apk, second script).
(若为连接超时，确认其他控制程序已退出后等30秒；网络和SSH可以保持连接)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
🕒 Signaling State          : ⚫ closed        (10:48:22)
🕒 ICE Connection State     : ⚫ closed        (10:48:22)
🕒 Peer Connection State    : ⚫ closed        (10:48:22)
[DTLS] RTCDtlsTransport(server) x DTLS handshake failed (connection error)
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.FAILED
ERROR:asyncio:Task exception was never retrieved
future: <Task finished name='Task-12' coro=<RTCPeerConnection.__connect() done, defined at /home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py:1008> exception=InvalidStateError('RTCIceTransport is closed')>
Traceback (most recent call last):
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py", line 1016, in __connect
    await iceTransport.start(self.__remoteIce[transceiver])
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcicetransport.py", line 312, in start
    raise InvalidStateError("RTCIceTransport is closed")
aiortc.exceptions.InvalidStateError: RTCIceTransport is closed
🕒 WebRTC connection        : 🔴 disconnected  (10:48:22)

[间隔] 等待30秒释放会话...

[对照 4/4] baseline-again
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:48:54)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:48:54)
[握手 0.1s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:48:54)
🕒 ICE Gathering State      : 🟡 gathering     (10:48:54)
🕒 ICE Gathering State      : 🟢 complete      (10:48:54)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:48:54)
🕒 ICE Connection State     : 🔵 checking      (10:48:54)
🕒 Peer Connection State    : 🔵 connecting    (10:48:54)
[握手 0.3s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
🕒 Signaling State          : 🟢 stable        (10:48:54)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:48:54)
[DTLS] RTCDtlsTransport(server) x DTLS handshake negotiated SRTP_AES128_CM_SHA1_80
[DTLS] RTCDtlsTransport(server) - DTLS handshake complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.CONNECTED
🕒 Peer Connection State    : 🟢 connected     (10:48:54)
🕒 Data Channel Verification: ✅ OK            (10:48:54)
[传输计数] DTLS=connected 收包=9 发包=9 收字节=1815 发字节=2212
[结果] 连接及验证成功；没有发送动作。
[握手 0.6s] peer=connected ICE=completed DTLS/ICE/role=(('connected', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
🕒 Signaling State          : ⚫ closed        (10:48:55)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTED -> State.CLOSED
🕒 ICE Connection State     : ⚫ closed        (10:48:55)
🕒 Peer Connection State    : ⚫ closed        (10:48:55)
🕒 WebRTC connection        : 🔴 disconnected  (10:48:55)

[对照结果]
baseline: 成功
import-main: 失败
startup: 失败
baseline-again: 成功
[日志已保存] /home/unitree/go2_voice/logs/connection-compare-19700101-104644
unitree@ubuntu:~$
```

## 13. 最新五项对照原始输出（v5）

```text
unitree@ubuntu:~$ python3 ~/go2_voice/connection_compare.py
开始5项对照，约4分钟。每项结束后间隔30秒。Ctrl+C 可退出。
[日志目录] /home/unitree/go2_voice/logs/connection-compare-19700101-105335

[对照 1/5] baseline
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:53:36)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:53:36)
[握手 0.1s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:53:36)
🕒 ICE Gathering State      : 🟡 gathering     (10:53:36)
🕒 ICE Gathering State      : 🟢 complete      (10:53:36)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:53:36)
🕒 ICE Connection State     : 🔵 checking      (10:53:37)
🕒 Peer Connection State    : 🔵 connecting    (10:53:37)
🕒 Signaling State          : 🟢 stable        (10:53:37)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:53:37)
[握手 0.3s] peer=connecting ICE=completed DTLS/ICE/role=(('connecting', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
[DTLS] RTCDtlsTransport(server) x DTLS handshake negotiated SRTP_AES128_CM_SHA1_80
[DTLS] RTCDtlsTransport(server) - DTLS handshake complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.CONNECTED
🕒 Peer Connection State    : 🟢 connected     (10:53:37)
🕒 Data Channel Verification: ✅ OK            (10:53:37)
[传输计数] DTLS=connected 收包=9 发包=9 收字节=1813 发字节=2212
[结果] 连接及验证成功；没有发送动作。
[握手 0.6s] peer=connected ICE=completed DTLS/ICE/role=(('connected', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
🕒 Signaling State          : ⚫ closed        (10:53:38)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTED -> State.CLOSED
🕒 ICE Connection State     : ⚫ closed        (10:53:38)
🕒 Peer Connection State    : ⚫ closed        (10:53:38)
🕒 WebRTC connection        : 🔴 disconnected  (10:53:38)

[间隔] 等待30秒释放会话...

[对照 2/5] early-lock
[单变量对照] 已在 asyncio.run 之前创建异步锁
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:54:10)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:54:10)
[握手 0.0s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:54:10)
🕒 ICE Gathering State      : 🟡 gathering     (10:54:10)
🕒 ICE Gathering State      : 🟢 complete      (10:54:10)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:54:10)
🕒 ICE Connection State     : 🔵 checking      (10:54:10)
🕒 Peer Connection State    : 🔵 connecting    (10:54:10)
[握手 0.4s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
🕒 Signaling State          : 🟢 stable        (10:54:10)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:54:10)
[DTLS] RTCDtlsTransport(server) x DTLS handshake negotiated SRTP_AES128_CM_SHA1_80
[DTLS] RTCDtlsTransport(server) - DTLS handshake complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.CONNECTED
🕒 Peer Connection State    : 🟢 connected     (10:54:11)
🕒 Data Channel Verification: ✅ OK            (10:54:11)
[传输计数] DTLS=connected 收包=9 发包=9 收字节=1813 发字节=2212
[结果] 连接及验证成功；没有发送动作。
[握手 0.7s] peer=connected ICE=completed DTLS/ICE/role=(('connected', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
🕒 Signaling State          : ⚫ closed        (10:54:12)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTED -> State.CLOSED
🕒 ICE Connection State     : ⚫ closed        (10:54:12)
🕒 Peer Connection State    : ⚫ closed        (10:54:12)
🕒 WebRTC connection        : 🔴 disconnected  (10:54:12)

[间隔] 等待30秒释放会话...

[对照 3/5] import-main
onnxruntime cpuid_info warning: Unknown CPU vendor. cpuinfo_vendor value: 0
[主程序文件] /home/unitree/go2_voice/voice_control_board.py
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:54:44)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:54:44)
[握手 0.1s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:54:44)
🕒 ICE Gathering State      : 🟡 gathering     (10:54:44)
🕒 ICE Gathering State      : 🟢 complete      (10:54:44)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:54:44)
🕒 ICE Connection State     : 🔵 checking      (10:54:44)
🕒 Peer Connection State    : 🔵 connecting    (10:54:44)
[握手 0.5s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
🕒 Signaling State          : 🟢 stable        (10:54:44)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:54:44)
[握手 0.8s] peer=connecting ICE=completed DTLS/ICE/role=(('connecting', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
[传输计数] DTLS=connecting 收包=0 发包=0 收字节=0 发字节=0
[结果] DataChannelTimeoutError Data channel setup timed out after 15s — the WebRTC peer never reached `connected` (peer=connecting, ice=completed). This usually means ICE/DTLS failed — another WebRTC client is probably holding the slot, the network is flaky, or a stale peer on the robot hasn't been released yet. Try again in 10–20 s, or kill any other connected client (mobile apk, second script).
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
🕒 Signaling State          : ⚫ closed        (10:54:59)
🕒 ICE Connection State     : ⚫ closed        (10:54:59)
🕒 Peer Connection State    : ⚫ closed        (10:54:59)
[DTLS] RTCDtlsTransport(server) x DTLS handshake failed (connection error)
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.FAILED
ERROR:asyncio:Task exception was never retrieved
future: <Task finished name='Task-13' coro=<RTCPeerConnection.__connect() done, defined at /home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py:1008> exception=InvalidStateError('RTCIceTransport is closed')>
Traceback (most recent call last):
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py", line 1016, in __connect
    await iceTransport.start(self.__remoteIce[transceiver])
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcicetransport.py", line 312, in start
    raise InvalidStateError("RTCIceTransport is closed")
aiortc.exceptions.InvalidStateError: RTCIceTransport is closed
🕒 WebRTC connection        : 🔴 disconnected  (10:54:59)

[间隔] 等待30秒释放会话...

[对照 4/5] startup
onnxruntime cpuid_info warning: Unknown CPU vendor. cpuinfo_vendor value: 0
[主程序文件] /home/unitree/go2_voice/voice_control_board.py
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
[握手兼容] aiortc 1.9.0，使用原始连接实现。
[启动 v5] 导入阶段不创建异步锁；先连接，再在线程中加载模型。
🕒 WebRTC connection        : 🟡 started       (10:55:32)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:55:32)
🕒 Signaling State          : 🟡 have-local-offer (10:55:32)
🕒 ICE Gathering State      : 🟡 gathering     (10:55:32)
🕒 ICE Gathering State      : 🟢 complete      (10:55:32)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:55:32)
🕒 ICE Connection State     : 🔵 checking      (10:55:32)
🕒 Peer Connection State    : 🔵 connecting    (10:55:32)
🕒 Signaling State          : 🟢 stable        (10:55:32)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:55:32)
启动失败: DataChannelTimeoutError Data channel setup timed out after 15s — the WebRTC peer never reached `connected` (peer=connecting, ice=completed). This usually means ICE/DTLS failed — another WebRTC client is probably holding the slot, the network is flaky, or a stale peer on the robot hasn't been released yet. Try again in 10–20 s, or kill any other connected client (mobile apk, second script).
(若为连接超时，确认其他控制程序已退出后等30秒；网络和SSH可以保持连接)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
🕒 Signaling State          : ⚫ closed        (10:55:47)
🕒 ICE Connection State     : ⚫ closed        (10:55:47)
🕒 Peer Connection State    : ⚫ closed        (10:55:47)
[DTLS] RTCDtlsTransport(server) x DTLS handshake failed (connection error)
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.FAILED
ERROR:asyncio:Task exception was never retrieved
future: <Task finished name='Task-12' coro=<RTCPeerConnection.__connect() done, defined at /home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py:1008> exception=InvalidStateError('RTCIceTransport is closed')>
Traceback (most recent call last):
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcpeerconnection.py", line 1016, in __connect
    await iceTransport.start(self.__remoteIce[transceiver])
  File "/home/unitree/.local/lib/python3.8/site-packages/aiortc/rtcicetransport.py", line 312, in start
    raise InvalidStateError("RTCIceTransport is closed")
aiortc.exceptions.InvalidStateError: RTCIceTransport is closed
🕒 WebRTC connection        : 🔴 disconnected  (10:55:47)

[间隔] 等待30秒释放会话...

[对照 5/5] baseline-again
[通信库文件] /home/unitree/.local/lib/python3.8/site-packages/unitree_webrtc_connect/webrtc_driver.py
[加密库] OpenSSL 3.5.3 16 Sep 2025
🕒 WebRTC connection        : 🟡 started       (10:56:19)
🕒 Lidar Decoder            : 🧊 LibVoxelDecoder (10:56:19)
[握手 0.1s] peer=new ICE=new DTLS/ICE/role=(('new', 'new', 'controlling'), ('new', 'new', 'controlling'), ('new', 'new', 'controlling'))
🕒 Signaling State          : 🟡 have-local-offer (10:56:19)
🕒 ICE Gathering State      : 🟡 gathering     (10:56:19)
🕒 ICE Gathering State      : 🟢 complete      (10:56:19)
🕒 LAN Signaling Method     : 🆕 con_notify (192.168.123.161:9991) (10:56:19)
🕒 ICE Connection State     : 🔵 checking      (10:56:19)
🕒 Peer Connection State    : 🔵 connecting    (10:56:19)
[握手 0.5s] peer=connecting ICE=checking DTLS/ICE/role=(('new', 'checking', 'controlling'),)
🕒 Signaling State          : 🟢 stable        (10:56:19)
[DTLS] RTCDtlsTransport(server) - State.NEW -> State.CONNECTING
🕒 ICE Connection State     : 🟢 completed     (10:56:19)
[DTLS] RTCDtlsTransport(server) x DTLS handshake negotiated SRTP_AES128_CM_SHA1_80
[DTLS] RTCDtlsTransport(server) - DTLS handshake complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTING -> State.CONNECTED
🕒 Peer Connection State    : 🟢 connected     (10:56:19)
🕒 Data Channel Verification: ✅ OK            (10:56:19)
[传输计数] DTLS=connected 收包=9 发包=9 收字节=1812 发字节=2213
[结果] 连接及验证成功；没有发送动作。
[握手 0.7s] peer=connected ICE=completed DTLS/ICE/role=(('connected', 'completed', 'controlling'),)
[远端协商] a=group:BUNDLE 0 1 2 | m=audio 9 UDP/TLS/RTP/SAVPF 96 | a=mid:0 | a=setup:active | m=video 9 UDP/TLS/RTP/SAVPF 101 | a=mid:1 | a=setup:active | m=application 9 UDP/DTLS/SCTP webrtc-datachannel | a=mid:2 | a=setup:active
🕒 Signaling State          : ⚫ closed        (10:56:20)
[DTLS] RTCDtlsTransport(server) - DTLS shutdown complete
[DTLS] RTCDtlsTransport(server) - State.CONNECTED -> State.CLOSED
🕒 ICE Connection State     : ⚫ closed        (10:56:20)
🕒 Peer Connection State    : ⚫ closed        (10:56:20)
🕒 WebRTC connection        : 🔴 disconnected  (10:56:20)

[对照结果]
baseline: 成功
early-lock: 成功
import-main: 失败
startup: 失败
baseline-again: 成功
[日志已保存] /home/unitree/go2_voice/logs/connection-compare-19700101-105335
unitree@ubuntu:~$
```
