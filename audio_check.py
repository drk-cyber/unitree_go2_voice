# 狗麦克风音频诊断: 实时打印响度条形图 + 保存录音文件
# 用法: python audio_check.py   (跑约10秒, 对狗拍手说话, 观察条形图)
# 结束: Ctrl+C, 会自动保存 audio_check.wav
import asyncio, os, wave
import numpy as np
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")
SR = 48000  # WebRTC 音频是48kHz

chunks = []      # 收到的原始帧, 用于存文件
frame_no = 0

async def on_frame(frame):
    """每收到一帧音频(约20ms)被调用一次; 必须是async, 库会await它"""
    global frame_no
    arr = np.frombuffer(frame.to_ndarray(), dtype=np.int16).astype(np.float32)
    chunks.append(arr.tobytes())
    frame_no += 1
    if frame_no % 25 == 0:                      # 每25帧(约0.5秒)打印一次响度
        rms = float(np.sqrt(np.mean(arr ** 2))) # 响度(RMS), 安静~几十, 拍手~几千
        bar = "#" * int(min(rms, 3000) / 50)
        print(f"帧{frame_no:5d}  音量{rms:7.0f} |{bar}")

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    await conn.connect()
    conn.audio.switchAudioChannel(True)
    conn.audio.add_track_callback(on_frame)
    print("监听狗麦克风... 对着狗拍手/说话, 看下面的音量条 (Ctrl+C 结束)")
    await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        with wave.open("audio_check.wav", "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"".join(chunks))
        print(f"\n已保存 audio_check.wav, 共{frame_no}帧(约{frame_no//50}秒). 双击用播放器听.")
