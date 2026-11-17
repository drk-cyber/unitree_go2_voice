# 板载语音 v7：启动修复与验收

> 后续 v8 增加免回车唤醒和音量参数，最新操作见 [BOARD_V8.md](BOARD_V8.md)。
> 用户随后运行完整 v7，识别与播报发送正常，反馈“挺好的”；已进入免回车开发阶段。

## 最新实机结果

用户已在板上运行 `python3 ~/go2_voice/voice_control_board.py --check-startup`，本轮启动检查通过：

- 板上日志 11:24:33：peer connected、Data Channel Verification OK。
- 连接成功后导入语音模块，ASR 和 TTS 模型加载完成；TTS 继续使用 170429550 字节的 model.onnx。
- 11:24:41：正常关闭并断开，未出现此前的 RTCIceTransport is closed 或未回收任务异常。
- 未切模式、未设置音量、未发动作。

这次实测表明延迟导入方案已通过一次完整启动检查；尚未验证 USB 麦克风、狗扬声器和动作组成的完整控制流程，也未证明具体是哪条提前导入的语句触发旧故障。
onnxruntime 的 Unknown CPU vendor 警告未阻止本轮模型加载。

## 本次改动

旧版“先连接再加载模型”仍然在文件开头导入 numpy、av、sherpa 和完整语音模块。
现有板上对照显示：基础连接成功；先导入旧主程序则在 ICE completed 后卡住 DTLS。
具体触发语句尚未确定，不能据此断言是某个库或 aiortc 双任务竞争。

v7 将入口和语音实现分为两个同目录文件：

- `voice_control_board.py`：轻量入口。先调用原始通信库连接并完成验证，成功后再导入语音运行模块。
- `voice_control_board_runtime.py`：原有 ASR、TTS、录音、命令表、音频轨道和动作逻辑。导入与模型初始化放在线程中，连接循环继续运行。

保留已在板上验证的 SenseVoice 工厂接口和跳过 Git LFS 指针的 TTS 模型选择。
不重装依赖、不更改模型、不修改已安装的 aiortc。移除无效的同进程三次重试和实验性握手调度。
退出时先结束属于此 peer 的握手任务再关闭连接；走路取消后等待归零完成，再允许下一次走路或断开。
板上按回车的等待改为可取消的终端读取，防止 Ctrl+C 后卡在 input 工作线程。

## 上传（Windows PowerShell）

一次上传两个文件：

```powershell
scp C:\yushu\unitree_webrtc_connect\voice_control_board.py C:\yushu\unitree_webrtc_connect\voice_control_board_runtime.py unitree@192.168.123.18:/home/unitree/go2_voice/
```

## 先验启动（板子 SSH 终端）

退出手机 App 和其他控制程序；网络、SSH 保持。若刚运行过连接程序，先留出 90 秒间隔。

```bash
python3 ~/go2_voice/voice_control_board.py --check-startup
```

此命令连接、加载模型后断开，不切模式、不设置音量、不执行动作。
输出顺序应为：

```text
[启动 v7] 先完成 WebRTC 连接，再导入语音模块和加载模型。
... Data Channel Verification: OK ...
[连接] 已连接并验证通过。
[启动] 正在导入语音模块（连接已建立）...
[启动] 加载识别模型...
[启动] 加载合成模型...
[启动检查] 连接和模型均就绪；未切模式、未设置音量、未发送动作。
... disconnected ...
```

失败时保留从 `[启动 v7]` 到断开的完整输出。该修复必须通过这项板上验证，PC 测试不能证明握手已经恢复。

## 通过后运行完整语音控制

再次启动前留出 90 秒。以下命令会进入动作控制，可能切换到 normal 模式，按回车录音 3 秒。

```bash
python3 ~/go2_voice/voice_control_board.py
```

正常启动可能切换到 normal 模式、使狗起身，先留出活动空间。
按回车说“你好”，分别记录识别文字、打招呼动作、扬声器回应；“你好”本身会触发 Hello 动作。
移动仍使用原有无线手柄通道。

离线模式保留：`--check-models`、`--self-test`、`--transcribe-wav PATH`；均不会创建机器人连接。
`--check-connection` 只检查连接，完全不导入语音模块。
`--check-connection-with-models` 现在与 `--check-startup` 相同，先连接再加载模型。

旧的 import-prefix 二分脚本针对旧主程序；v7 的入口结构已改变，不应将旧候选边界直接用于此版。

## 本地验证范围

使用 Windows 的 go2 Python 3.11 环境执行 17 项检查，全部通过：启动边界、ASR 实际 wheel 接口、TTS 指针选择、音频转换和走路归零。
新增检查真实拦截连接前的语音导入；替身连接验证调用顺序、无动作检查和失败清理。
超时清理检查执行板载 aiortc 1.9.0 wheel 中的实际 `__connect` 方法，但没有运行其 aarch64 原生组件或真实网络握手。
入口与运行模块通过 Python 3.8 语法检查。板上启动检查已通过一次；麦克风、播报和动作完整流程仍待验收。
