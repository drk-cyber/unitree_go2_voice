# 当前板载入口：v8（2026-11-17）

免回车唤醒、试听和音量参数见 [BOARD_V8.md](BOARD_V8.md)。默认仍是手动回车模式。

最新上传与验收请使用 [BOARD_V7.md](BOARD_V7.md)。必须同时上传 voice_control_board.py 和 voice_control_board_runtime.py。
下文连接超时等于客户端占用的说法过于绝对：现有实验发现了信令限流和 ICE 成功后 DTLS 超时两种不同情况，不能混为一谈。


---

# Go2 语音控制项目 · 部署手册

> 项目：让宇树 Go2 听懂人话、执行动作、开口回答。
> 当前阶段：阶段二（把 PC 上已跑通的语音闭环迁入算力板，最终脱机运行）。
> 维护约定：改了什么请在文末"变更记录"里加一行。

## 一、这台狗的架构（30 秒版）

- **运动主控**（192.168.123.161）：宇树官方程序，管四条腿。我们通过 WebRTC 协议（官方 App 同款）给它发命令、收数据、用它的扬声器出声。
- **算力板**（本机，eth0=192.168.123.18）：Jetson Orin NX 16GB，Ubuntu 20.04，Python 3.8。**我们所有的语音识别、决策代码都跑在这**，通过内部网络直连主控，无需任何外部网络。

## 二、环境部署（新人照抄即可，全程不需要外网）

```bash
# 1. 装系统库(sounddevice 的底层依赖, dpkg 离线安装)
sudo dpkg -i ~/go2_voice/wheels/libportaudio2_19.6.0-1build1_arm64.deb

# 2. 装 28 个 Python 包(WebRTC栈+识别+音频, 全部 aarch64 预编译, 已验证齐)
pip3 install --user --no-index --no-deps ~/go2_voice/wheels/*.whl

# 3. 冒烟测试: 板子直连狗的主控
python3 ~/go2_voice/board_test.py
# 预期输出: == 板子连上狗了! == 和狗的当前模式
```

依赖总清单见 `wheels/` 文件夹（含 unitree_webrtc_connect 2.2.0、aiortc、faster-whisper 全家、numpy 1.24.4 等）。
如果 pip3 不存在：在 PC 上下载 https://bootstrap.pypa.io/pip/get-pip.py ，scp 到板上执行 `python3 get-pip.py --user`。

## 三、怎么跑

```bash
python3 ~/go2_voice/voice_control.py        # 语音控制主程序(待移植验证)
python3 ~/go2_voice/board_test.py           # 连接冒烟测试
```

PC 端（开发/调试用，与板上互斥——**同一时间只允许一个 WebRTC 客户端连狗**，手机 App 也算）：
`C:\yushu\unitree_webrtc_connect\` 下 `conda activate go2` 后跑 `voice_control.py`。

## 四、必须知道的坑（全部实测踩出）

1. **`Move`(SPORT_CMD 1008) 在本机固件不可用**（应答 code=-1）。移动一律走 `rt/wirelesscontroller` 摇杆通道：50Hz 发布 `{"lx","ly","rx","ry","keys"}`，前进=ly 正、后退=ly 负、**左转=rx 负、右转=rx 正**（实测方向：rx 正=右转，官方示例注释标反了，以实机为准），归零即停。
2. **运动模式**：狗常在 `mcf` 模式，必须先切 `normal`（MOTION_SWITCHER api 1001 查 / 1002 切），切完等 8 秒。
3. **单客户端互斥**：连接报 `DataChannelTimeoutError` / "holding the slot" = 有别的客户端（App/旧会话）占着，等 20-30 秒再试，或杀掉另一个客户端。退出务必 `disconnect()`。
4. **狗执行动作时会忽略同时到达的其他运动命令**——命令间留间隔。
5. **语音识别模型下载**必须走镜像：环境变量 `HF_ENDPOINT=https://hf-mirror.com`（脚本里已写死）。
6. **edge-tts（在线语音合成）依赖外网**，本板无外网，板上 TTS 一律走离线方案（pyttsx3 在 Windows / sherpa-onnx 在 Linux）。
7. WebRTC 音频回调必须是 `async def`；多次播报音频用"常驻轨道+队列灌 PCM"模式（voice_control.py 里的 QueueAudioTrack），**不要**反复 addTrack/replaceTrack。
8. Jetson 没有 nvidia-smi，看 GPU 用 `sudo tegrastats`。

## 五、文件说明

| 文件 | 作用 |
|---|---|
| board_test.py | 板上连接冒烟测试 |
| voice_control.py | 语音控制主程序（PC 版已验证；板载版移植中） |
| wheels/ | 全部离线安装包（28 whl + libportaudio2 deb），重装环境的全部材料 |
| models/ | 语音识别/合成模型（待放入） |
| logs/ | 运行日志（待使用） |

## 六、变更记录

MeloTTS 离线压缩包中的 model.int8.onnx 实测为 133 字节 Git LFS 指针，并非模型。板载程序会跳过此指针，选用同包 170429550 字节的 model.onnx；不需要重装依赖。

| 日期 | 人 | 内容 |
|---|---|---|
| 2026-11-16 | 二分v3 | 修复三个缺陷：①write_prefix_module 用 splitlines 切片直拼导致语句粘连无换行（生成文件不可编译）——改为每行补换行；②前缀索引 off-by-one——check(idx) 不含目标语句本身，嫌疑扫描改用 idx+1，边界报告由 nodes[high] 改为 nodes[high-1]，并打印"从成功前缀到失败前缀新增语句列表"；③无错误分类——新增 classify：success/DataChannelTimeoutError(poison)/429+NoSdpAnswer(slot，等待后重测同一前缀，最多3次)/脚本自身报错(中止)/unknown(重测1次)。按用户要求：所有连接测试统一等待90秒（含重测）；边界确认改为交叉复测（低/高/低/高/基线，须严格 success/poison 交替）；工具文档移除根因推测（双重调度仅为待验证假设）。新增 --self-test 本地自检并已通过：44条顶层语句、全部45个前缀(0..44) ast.parse 可编译且增量一致（前缀N==前缀N-1+第N条，AST比对）、5个嫌疑索引命中真实行、5类分类样例全部正确。 |
| 2026-11-16 | 时序实验结论 + 二分v2 | slot_timing_test 六项实测：90秒安全间隔下纯探针3次成功（A1/A2/C1），同样间隔"导入主程序后连接"失败（B），v6三次重试均失败（D）。"重连太快"假设被证伪，**导入污染进程坐实**。另确认两种失败签名不同：快连=信令层429/NoSdpAnswer（会话未释放），导入污染=信令成功+ICE completed+DTLS收发0。pip实测 aarch64+py3.8 的 aiortc 上限就是1.9.0（新版需更高Python），升级路线不可行。读1.9.0源码确认 setLocal/ setRemoteDescription 各自无条件 ensure_future(__connect)、iceTransport.start() 有"已在进行则等Event"逻辑，构成竞争温床。import_connection_bisect.py 重写：间隔改自适应（失败后90秒/成功后45秒，防429噪声）、新增五嫌疑扫描（标准库块/numpy/av/sherpa/aiortc）、前缀执行改为临时模块文件+正常import（弃用exec）。尚未在板上运行。 |
| 2026-11-16 | v6 + 时序实验 | 分析：主程序模块体仅import+函数定义，且探针 --speech-imports before 已验证 numpy/av/sherpa 先导入不破坏握手，"导入污染"缺机制；而对照实验各项间隔仅30秒，恰在狗释放会话的窗口（PC时代实测连续两次运行第二次必报同样超时）。voice_control_board.py v6：连接改为 connect_with_retry（3次×45秒，每次换新连接对象，失败对象先disconnect）。新增 slot_timing_test.py：6项时序对照（90秒基线/导入后即连/5秒快连复现/完整启动），用于一锤定音区分"导入污染"与"重连过快"；尚未在板上运行。 |
| 2026-11-16 | v5 实测补充 | 五项结果 baseline/early-lock/baseline-again成功，import-main/startup失败。提前创建锁假设不获支持，v5未修复握手。新增import_connection_bisect.py自动缩小顶层导入差异，尚待板上运行。交接文档顶部已更新结论。 |
| 2026-11-16 | 启动 v5 | 四项隔离对照：基础连接前后均成功，导入主程序后及启动均失败，失败DTLS收发均0。移除导入阶段 asyncio.Lock 创建，改为首次播报时创建；v4 调度处理改为 --ordered-handshake 可选。增加 early-lock 单变量对照。10 项本地检查通过；Python 3.8 实机握手是否因此恢复待确认，不能宣称根因已证实。 |
| 2026-11-16 | 对照诊断 | 板上 v4 仍在 DTLS connecting 超时，但断开时 InvalidStateError 消失。新增 connection_compare.py，自动运行四项隔离对照、记录传输计数和库路径并保存日志；startup 项明确关闭 v4 调度兼容处理以复现原始条件。仅检查连接及模型，无动作。根因仍未确定。 |
| 2026-11-16 | 启动 v4 | v3 在模型加载前仍超时，启动顺序未解决问题。核对板上 aiortc 1.9.0 源码后增加进程内兼容子类：完整远端 SDP 就绪后运行连接任务、串行执行、close 前取消并等待连接任务；只作用于 1.9.0，未改系统包。加 DTLS 日志及 --legacy-handshake 对照。9 项本地检查通过，其中实连测试使用 PC aiortc 1.15.0；板上握手根因与效果待验证。 |
| 2026-11-16 | 启动 v3 | 独立探针在不导入/先导入语音依赖时均成功，而完整程序模型加载后仍超时。默认改为先连接再在线程中加载模型；新增 --check-startup 只验启动并断开，不发动作。7 项本地检查通过；该调整是否解决实机超时待验证，根因未定。 |
| 2026-11-16 | 连接诊断 | 两种模型加载对照均在 ICE completed / DTLS connecting 超时，模型初始化并非必要触发条件。新增独立 connection_probe.py，记录 DTLS 重传/角色及库版本，并支持语音依赖导入顺序对照；不发控制请求，不输出完整 SDP。实际握手根因待板上结果确认。 |
| 2026-11-16 | 离线检查 | 新增 --self-test 合成/转换/识别自检及 --transcribe-wav；修复跨线程音频入队，按真实音频时长等待，补齐重采样尾帧。5 项本地回归检查通过；真实板载推理及硬件验收待进行。操作见 OFFLINE_CHECKS.md。 |
| 2026-11-11 | 初版 | 环境部署手册、已知坑清单建立；PC 端语音闭环已全部验证通过 |
| 2026-11-16 | 修复 | 按离线 2.0.1 wheel 的 Python 源码，将板载 ASR 初始化改为 OfflineRecognizer.from_sense_voice；以 run_in_executor 替代 Python 3.8 不支持的 asyncio.to_thread；修复走路任务作用域及取消后归零。新增 --check-models，仅加载 ASR/TTS，不连接机器人。Windows 接口回归检查通过，板上模型推理仍待实测。 |
