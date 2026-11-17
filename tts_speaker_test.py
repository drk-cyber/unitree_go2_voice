# 扬声器清晰度测试: TTS合成人话 -> 推给狗的扬声器播放
# 用法: 先 pip install edge-tts, 然后 python tts_speaker_test.py [台词编号1-4]
import asyncio, os, sys, json
import edge_tts
from aiortc.contrib.media import MediaPlayer
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")

# 台词库: 用法 python tts_speaker_test.py 1  (编号1-4, 不带编号默认1)
PRESETS = {
    "1": ("zh-CN-YunxiNeural",
          "大家好！我是宇树Go2，一只四足机器人。我会跑，会跳，会翻跟头，还会说话。"
          "刚才的测试说明我吐字清晰、口齿伶俐。我的师傅正在给我装上耳朵，"
          "很快我就能听懂人话了，敬请期待！"),
    "2": ("zh-CN-YunxiNeural",
          "给你讲个笑话：有一天，一只机器狗去面试，面试官问它有什么特长。"
          "它说：我特别忠诚，断电也绝不离开岗位。……好冷吧？"
          "我讲冷笑话的时候，麻烦给我开个暖气。"),
    "3": ("zh-CN-YunyangNeural",
          "下面播报一条科技新闻：今天，一只宇树Go2机器人成功完成了语音控制实验。"
          "实验结果表明，该机器人扬声器清晰度良好，语音识别系统即将上线。"
          "研究人员表示，下一步将给它接入人工智能大模型。感谢聆听。"),
    "4": ("zh-CN-XiaoxiaoNeural",
          "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"
          "这是王之涣的登鹳雀楼。狗也不能只学技术，偶尔也要读读诗，对吧？"),
}

choice = sys.argv[1] if len(sys.argv) > 1 else "1"
VOICE, TEXT = PRESETS[choice]
print(f"台词{choice}, 音色{VOICE}")
OUT = "tts_test.mp3"

async def synth():
    """合成语音, 自动重试3次"""
    for attempt in range(1, 4):
        try:
            print(f"合成语音中(第{attempt}次尝试)...")
            await edge_tts.Communicate(TEXT, VOICE).save(OUT)
            print("已生成", OUT, "(可以先在PC上双击听一遍做对比)")
            return True
        except Exception as e:
            print(f"  第{attempt}次失败: {type(e).__name__}, 3秒后重试")
            await asyncio.sleep(3)
    return False

async def main():
    # 1) 先在PC上合成语音(联网调用微软TTS)——失败就不占用狗的连接
    if not await synth():
        print("\n[TTS联网失败] 两个解决办法:")
        print("  A. 如果电脑开着梯子/VPN(如Clash), 先设代理再重跑本脚本:")
        print("     cmd:   set HTTPS_PROXY=http://127.0.0.1:7890")
        print("     ps:    $env:HTTPS_PROXY=\"http://127.0.0.1:7890\"")
        print("     (端口按你的代理软件改, Clash默认7890, v2rayN默认10809)")
        print("  B. 不想折腾网络: 跑离线版 python tts_offline.py")
        return

    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接", ROBOT_IP)

        # 2) 音量调到最大
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["VUI"], {"api_id": 1003, "parameter": {"volume": 10}})
        print("音量 10/10")
        await asyncio.sleep(0.5)

        # 3) 推给狗的扬声器播放
        print("狗正在说话, 请凑近听吐字...")
        player = MediaPlayer(OUT)
        conn.pc.addTrack(player.audio)
        await asyncio.sleep(30)   # 台词较长, 留足播放时间
    finally:
        print("正在断开...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("完成")

if __name__ == "__main__":
    asyncio.run(main())
