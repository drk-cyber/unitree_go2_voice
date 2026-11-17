# Move诊断2: 1)打印Move(1008)的应答报文找错误码 2)试无线手柄通道走路
# 用法: python move_test2.py
import asyncio, os, json
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD, OBSTACLES_AVOID_API

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")

def brief(r, n=400):
    try:
        return json.dumps(r, ensure_ascii=False)[:n]
    except Exception:
        return str(r)[:n]

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)

        # 确保normal模式
        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        try:
            mode = json.loads(r["data"]["data"]).get("name")
            print("当前模式:", mode)
        except Exception:
            mode = None
        if mode != "normal":
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": 1002, "parameter": {"name": "normal"}})
            print("切换normal, 等站稳(8秒)...")
            await asyncio.sleep(8)

        # 站立
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandUp"]})
        print("站立中, 等5秒...")
        await asyncio.sleep(5)

        # A) 查避障开关状态(排除避障拦截)
        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["OBSTACLES_AVOID"], {"api_id": OBSTACLES_AVOID_API["SWITCH_GET"]})
        print("A. 避障状态应答:", brief(r))

        # B) Move(1008)并打印完整应答报文
        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["Move"], "parameter": {"x": 0.5, "y": 0, "z": 0}})
        print("B. Move(1008)应答:", brief(r))
        await asyncio.sleep(3)
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StopMove"]})
        print("   (已发StopMove, 刚才3秒狗走了吗?)")
        await asyncio.sleep(1)

        # C) 无线手柄通道: 以50Hz发"摇杆前推"持续2秒, 然后归零
        print("C. 切无线手柄通道, 向前走2秒...")
        t = 0.0
        while t < 2.0:
            conn.datachannel.pub_sub.publish_without_callback(
                RTC_TOPIC["WIRELESS_CONTROLLER"],
                {"lx": 0.0, "ly": 0.5, "rx": 0.0, "ry": 0.0, "keys": 0})
            await asyncio.sleep(0.02)
            t += 0.02
        conn.datachannel.pub_sub.publish_without_callback(
            RTC_TOPIC["WIRELESS_CONTROLLER"],
            {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0, "keys": 0})
        print("   (手柄摇杆已归零, 这次狗走了吗?)")
        print("完成. 把B行的应答报文和A/B/C哪个让狗走了告诉我")
    finally:
        print("正在断开...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("已干净退出")

if __name__ == "__main__":
    asyncio.run(main())
