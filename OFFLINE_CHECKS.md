# 当前版本：v8（2026-11-17）

一次Ctrl+C退出已收到用户板上成功反馈；下一步开机启动见 [BOARD_SERVICE.md](BOARD_SERVICE.md)。服务安装和重启验收尚未完成。

最新免回车唤醒与音量设置见 [BOARD_V8.md](BOARD_V8.md)。v7 完整手动控制与 v8 免回车打招呼/比心/播报后恢复听取已收到用户成功反馈；长时间运行与退出稳定性仍待验证。

最新启动修复、两个文件的上传命令和只读验收步骤见 [BOARD_V7.md](BOARD_V7.md)。
v7 在连接验证成功后才导入语音模块；此前仅延迟模型加载，语音库仍在连接前导入。
下文 v3–v6 连接排查记录属于历史过程，不作为当前操作步骤。板上 ASR/TTS 离线自检已经通过；v7 实机启动检查也已通过一次（连接、模型加载、正常断开）。麦克风、播报和动作完整流程仍待验收。


---

# 板载语音：离线自检与实机验收

## 上传新版程序（Windows PowerShell）

```powershell
scp C:\yushu\unitree_webrtc_connect\voice_control_board.py C:\yushu\unitree_webrtc_connect\voice_control_board_runtime.py unitree@192.168.123.18:/home/unitree/go2_voice/
```

## 不连接机器狗的检查（板子 SSH 终端）

仅检查识别、合成模型加载：

```bash
python3 ~/go2_voice/voice_control_board.py --check-models
```

实际合成“你好，请站起来”，转换播放格式，再转为 16kHz 录音格式做识别：

```bash
python3 ~/go2_voice/voice_control_board.py --self-test
```

成功标准：输出合成耗时、音频转换时长、非空识别结果与“离线语音自检通过”。
匹配指令只打印，不执行。识别结果仍需人工核对是否与原句一致。
生成文件：~/go2_voice/self_test_tts.wav 与 self_test_asr.wav。
此检查不使用麦克风、不连接机器人，不能证明实际收音、扬声器与动作正常。
Windows 未安装板载原生推理环境，真实模型自检需要在板子上执行；狗充电时可等板子可用后再跑。

识别已有的录音（必须 16000Hz、单声道、16位 PCM WAV）：

```bash
python3 ~/go2_voice/voice_control_board.py --transcribe-wav ~/go2_voice/last_utt.wav
```

常见问题：Git LFS 指针会被跳过，自动选用完整模型；找不到模型要检查对应目录。
音频格式错误会明确报错，不能把 MP3 改名为 WAV。
自检若在 generate 报接口错误，请保留完整报错，不需要重装全部依赖。

## 充电完成后的顺序

### 连接超时对照诊断

最新 v5：四项对照结果 baseline 成功、import-main 失败、startup 失败、baseline-again 成功。
导入主程序是当前已缩小的触发条件，尚不能归因于 sherpa 或网络。
修复主程序导入时创建全局 asyncio.Lock 的问题：Python 3.8 会提前创建/绑定事件循环，现改为首次播报时创建锁。
此修改与握手失败的因果关系尚待板上确认；v4 调度兼容处理不再默认开启（需要 --ordered-handshake）。
connection_compare.py 更新为五项：baseline、early-lock（单独在 asyncio.run 前创建锁）、import-main、startup、baseline-again。
上传新版主程序和对照脚本后运行原对照命令，约4分钟，所有项目均不发动作。
若 early-lock 失败而 baseline 和修复后的 import-main/startup 成功，将支持提前创建循环与故障相关；仍需重复完整控制验收。

v4 实测清理异常消失，但 DTLS 仍超时，不能宣称已修复握手。
connection_compare.py 可一次运行四个独立子进程：基础探针、导入完整主程序后的探针、原始握手的主程序启动检查、再次基础探针。
每项间隔30秒，不切模式、不发动作；约3分钟结束，日志保存到同目录 logs/connection-compare-日期时间/。
需要 connection_probe.py、voice_control_board.py 和 connection_compare.py 同目录。

```bash
python3 ~/go2_voice/connection_compare.py
```

重点比较基础探针与 import-main、startup 的结果。前后基础探针用来发现测试期间机器人端或网络状态变化。
探针额外打印 DTLS 收发包/字节数以及实际导入库路径；不打印完整 SDP、ICE 密码、证书指纹。

v4 针对 aiortc 1.9.0 增加进程内连接任务调度兼容处理（不修改已安装依赖），并输出 DTLS 日志。
启动检查仍使用 --check-startup，先确认出现“[握手兼容] aiortc 1.9.0”和“[启动 v4]”。
如需对照原始调度，使用 --check-startup --legacy-handshake。此为诊断处理，板上效果尚待确认。

v3 默认启动顺序改为先连接、再在线程中加载模型；这是待实机验证的调整，不代表已确认此前超时的根因。
用下面的检查可验证新启动流程，不切运动模式、不设置音量、不执行动作：

```bash
python3 ~/go2_voice/voice_control_board.py --check-startup
```

成功时出现“[启动 v3]”“连接验证通过”“连接和模型均就绪”，然后主动断开。
自检 --self-test 和 --check-models 仍不连接机器人。

新版程序提供两种连接检查，都不读取/切换运动模式、不发动作。
先运行（导入语音依赖但不初始化模型）：

```bash
python3 ~/go2_voice/voice_control_board.py --check-connection
```

等待 30 秒，再运行（加载识别和合成模型后连接）：

```bash
python3 ~/go2_voice/voice_control_board.py --check-connection-with-models
```

保存两次完整输出，特别是 peer、ICE、DTLS 状态。单次结果不能排除间歇网络/旧会话影响。
若第一项也失败，用 board_test.py 交叉验证；不要凭一次超时判断一定有手机占用。
RTCIceTransport is closed 若出现在超时断开过程中，应和原始握手失败分别分析。

1. 退出手机 App、电脑上的控制脚本；电脑网络和 SSH 可以保持连接。
2. 运行 python3 ~/go2_voice/board_test.py，先确认读模式并干净断开。
3. 运行 python3 ~/go2_voice/voice_control_board.py，按回车说“你好”。
4. 分别记录识别文字、动作、回答声音，再逐项测站立、趴下、前进和左右转。
5. 语音停止受录音和识别延迟影响，测试移动时保留遥控器。

## 后续开发门槛

板载完整闭环通过后，再实现唤醒词与防自我唤醒；免回车模式稳定后再配置开机启动。
当前程序依赖终端 input，不能直接拿去做 systemd 常驻服务。
