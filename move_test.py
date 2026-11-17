# Move专项测试: 排查"不往前走"
# 用法: python move_test.py
# 观察: 两次Move各2秒, 狗到底走没走? 走了几步? 停止命令后是否立刻停?
import asyncio, os, json
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")

async def cmd(conn, name, wait_after, param=None):
    req = {"api_id": SPORT_CMD[name]}
    if param:
        req["parameter"] = param
    print("执行:", name, param or "")
    await conn.datachannel.pub_sub.publish_request_new(RTC_TOPIC["SPORT_MOD"], req)
    await asyncio.sleep(wait_after)

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)

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
            print("切换到 normal, 等站稳(8秒)...")
            await asyncio.sleep(8)

        # 干净状态: 先站立, 多等一会, 不掺杂其他动作
        await cmd(conn, "StandUp", 5)
        # 连发两次Move(间隔发): 有的固件单次Move只走一小段
        await cmd(conn, "Move", 2, {"x": 0.5, "y": 0, "z": 0})
        await cmd(conn, "Move", 2, {"x": 0.5, "y": 0, "z": 0})
        await cmd(conn, "StopMove", 1)
        print("测试完成, 观察点: 两次Move期间狗走了吗? 走多远? StopMove后立刻停了吗?")
    finally:
        print("正在断开...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("已干净退出")

if __name__ == "__main__":
    asyncio.run(main())
