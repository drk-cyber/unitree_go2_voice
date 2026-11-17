# Step 3: 狗的扬声器测试 - 音量调最大 + 播放狗叫声
# 用法: python speaker_test.py
import asyncio, os, json
from aiortc.contrib.media import MediaPlayer
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")
AUDIO = os.path.join("examples", "go2", "audio", "mp3_player", "dog-barking.wav")

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)

        # 1) 查当前音量
        r = await conn.datachannel.pub_sub.publish_request_new(RTC_TOPIC["VUI"], {"api_id": 1004})
        try:
            cur = json.loads(r["data"]["data"])["volume"]
            print("当前狗的音量:", cur, "/ 10")
        except Exception:
            print("读取音量失败(不影响), 直接调大")

        # 2) 音量调到最大(10档)
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["VUI"], {"api_id": 1003, "parameter": {"volume": 10}})
        print("音量已调到 10/10")
        await asyncio.sleep(0.5)

        # 3) 播放音频文件 -> 推到狗的扬声器
        print("播放:", AUDIO)
        player = MediaPlayer(AUDIO)
        conn.pc.addTrack(player.audio)
        print("狗应该叫了... 等8秒")
        await asyncio.sleep(8)
    finally:
        # 必须正式断开: 狗只允许一个WebRTC客户端, 蛮退会让下一次连接被拒几十秒
        print("正在断开连接...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("完成. 若没听到: 凑近狗听 / 确认音量打印 / 把输出贴给我")

asyncio.run(main())
