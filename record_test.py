# Step 1: PC麦克风测试 - 录5秒存成wav, 回放确认
# 用法: python record_test.py        用系统默认麦克风录
#       python record_test.py 3      用编号为3的设备录(编号看脚本打印的列表)
import sys, wave

import numpy as np
import sounddevice as sd

SR = 16000        # whisper 用 16kHz, 直接按这个采样
SECONDS = 5
OUT = "pc_mic_test.wav"

# 1) 列出所有输入设备(麦克风), 带"通道数>0"的才是输入设备
print("== 可用输入设备(麦克风) ==")
inputs = []
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        inputs.append(i)
        mark = "  <-- 当前默认" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']}  ({d['hostapi']}){mark}")

# 2) 决定用哪个设备录
if len(sys.argv) > 1:
    dev = int(sys.argv[1])
    print(f"\n使用你指定的设备 #{dev}")
else:
    dev = sd.default.device[0]
    print(f"\n未指定, 使用系统默认设备 #{dev}: {sd.query_devices(dev)['name']}")
    print("(如果这不是你的新麦克风: python record_test.py 编号)")

# 3) 录音
input(f"\n按回车开始录音 {SECONDS} 秒, 请对着麦克风正常说话...")
print("录音中...")
audio = sd.rec(int(SECONDS * SR), samplerate=SR, channels=1,
               dtype="float32", device=dev)
sd.wait()

# 4) 读结果
rms = float(np.sqrt(np.mean(audio ** 2)))
peak = float(np.max(np.abs(audio)))
print(f"录音结束. 整体响度: {rms:.4f} | 峰值: {peak:.3f}")
if rms < 0.005:
    print("[判断] 几乎没录到声音: 检查是不是选错设备/麦克风静音开关/离太远")
elif rms < 0.01:
    print("[判断] 声音偏小: 离麦克风近一点, 或把Windows输入音量调到80-100")
else:
    print("[判断] 音量正常")

with wave.open(OUT, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((audio * 32767).astype(np.int16).tobytes())
print(f"已保存 {OUT}. 回放: 在cmd里输入  start {OUT}  或双击文件")
