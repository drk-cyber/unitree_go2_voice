# 扬声器测试(离线版): 用Windows自带TTS合成, 不需要任何网络
# 用法: 先 pip install pyttsx3, 然后 python tts_offline.py
import asyncio, json, os
import pyttsx3
from aiortc.contrib.media import MediaPlayer
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")
TEXT = ("老师好，我是宇树Go2机器人。现在测试扬声器清晰度："
        "四是四，十是十，十四是十四，四十是四十。"
        "电池电量百分之七十四，现在几点了？准备出发！")
OUT = "tts_test_offline.wav"

def synth_offline():
    engine = pyttsx3.init()
    # 找中文语音; 找不到就用默认(会提醒)
    zh = None
    for v in engine.getProperty("voices"):
        name = (v.id + " " + v.name).lower()
        print("发现语音:", v.id, "|", v.name)
        if "zh" in name or "huihui" in name or "chinese" in name:
            zh = v.id
            break
    if zh:
        engine.setProperty("voice", zh)
        print("使用中文语音:", zh)
    else:
        print("[警告] 没找到中文语音包, 将用英文语音读中文(会很难听, 但仍能测喇叭)")
    engine.save_to_file(TEXT, OUT)
    engine.runAndWait()
    print("已生成", OUT)

async def main():
    synth_offline()

    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["VUI"], {"api_id": 1003, "parameter": {"volume": 10}})
        print("音量 10/10, 狗正在说话...")
        await asyncio.sleep(0.5)
        player = MediaPlayer(OUT)
        conn.pc.addTrack(player.audio)
        await asyncio.sleep(20)
    finally:
        print("正在断开...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("完成")

if __name__ == "__main__":
    asyncio.run(main())
