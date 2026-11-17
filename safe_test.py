# Step 4: 狗的腿测试 - 安全动作序列: 趴下->站立->打招呼->走2秒->停
# 用法: python safe_test.py
# 安全: 开阔地面 / 手机App断开 / 人站狗旁边 / 紧急情况长按狗背电源键
import asyncio, os, json
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")

async def cmd(conn, name, wait_after, move=None):
    """发一条动作命令; move=(x,y,z)时是行走命令, 速度单位m/s"""
    req = {"api_id": SPORT_CMD[name]}
    if move:
        req["parameter"] = {"x": move[0], "y": move[1], "z": move[2]}
    print("执行:", name)
    await conn.datachannel.pub_sub.publish_request_new(RTC_TOPIC["SPORT_MOD"], req)
    await asyncio.sleep(wait_after)

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)

        # 1) 确认处于 normal 模式(运动命令只认这个模式)
        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        try:
            mode = json.loads(r["data"]["data"]).get("name")
            print("当前模式:", mode)
        except Exception:
            mode = None
            print("读取模式失败, 尝试直接切normal")
        if mode != "normal":
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": 1002, "parameter": {"name": "normal"}})
            print("切换到 normal 模式, 等它站稳(8秒)...")
            await asyncio.sleep(8)

        # 2) 安全动作序列
        await cmd(conn, "StandDown", 3)                # 趴下(先做最稳的)
        await cmd(conn, "StandUp", 3)                  # 站起来
        await cmd(conn, "Hello", 4)                    # 打招呼
        await cmd(conn, "Move", 2, move=(0.4, 0, 0))   # 前进0.4m/s走2秒
        await cmd(conn, "StopMove", 1)                 # 停止(防止一直走)
        print("全部动作完成")
    finally:
        print("正在断开连接...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("已干净退出")

if __name__ == "__main__":
    asyncio.run(main())
