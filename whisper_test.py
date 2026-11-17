# Step 2: 语音识别测试 - 录3秒 -> whisper转文字
# 用法: python whisper_test.py        用系统默认麦克风
#       python whisper_test.py 3      用编号为3的设备(编号看启动时打印的列表)
# 首次运行自动下载模型(约500MB), 耐心等
import sys, os, wave

# HuggingFace国内直连不通, 改用镜像站; 必须在import faster_whisper之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SR = 16000
SECONDS = 3
OUT = "last_utt.wav"

# 要测的命令词: 以后狗就要听懂这些
WORDS = ["站起来", "趴下", "前进", "后退", "左转", "右转", "跳舞", "打招呼"]

print("== 可用输入设备(麦克风) ==")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        mark = "  <-- 当前默认" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']}{mark}")

if len(sys.argv) > 1:
    dev = int(sys.argv[1])
    print(f"\n使用指定设备 #{dev}")
else:
    dev = sd.default.device[0]
    print(f"\n未指定, 使用默认设备 #{dev}: {sd.query_devices(dev)['name']}")
    print("(如果这不是你的新麦克风: python whisper_test.py 编号)")

print("\n加载 whisper 模型(首次会下载约500MB, 请等待)...")
model = WhisperModel("small", device="cpu", compute_type="int8")
print("模型就绪. 请反复说下面这些词, 数一数识别对几个:")
print("  " + " / ".join(WORDS))

hit = miss = 0
while True:
    input("\n按回车开始说话(3秒, Ctrl+C 退出)...")
    print("请讲...")
    audio = sd.rec(int(SECONDS * SR), samplerate=SR, channels=1,
                   dtype="float32", device=dev)
    sd.wait()

    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 0.005:
        print("(几乎没录到声音, 离麦克风近一点/检查设备选择)")
        continue

    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    print("识别中...")
    segments, _ = model.transcribe(OUT, language="zh", vad_filter=True)
    text = "".join(s.text for s in segments).strip()
    print(f"识别结果: {text or '(空)'}")
