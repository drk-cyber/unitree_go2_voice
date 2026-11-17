# board_test.py - 算力板上的首个 WebRTC 冒烟测试
# 用法(在板子上): python3 board_test.py
# 目的: 证明板子能直连狗的运动主控并读取模式 —— 阶段二的地基
import asyncio
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC

ROBOT_IP = "192.168.123.161"   # 狗的主控(WebRTC信令端点)

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("== 板子连上狗了! ==")
        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        print("狗的当前模式:", r["data"]["data"])
        await asyncio.sleep(3)
    finally:
        print("断开中...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("完成")

asyncio.run(main())
