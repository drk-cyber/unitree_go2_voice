# voice_control.py - 语音控制闭环 (阶段一，实机验证中)
# 用法: python voice_control.py        或 python voice_control.py 3 (指定麦克风编号)
# 玩法: 按回车 -> 说3秒 -> 狗执行动作并开口回答
# 移动走"无线手柄"通道(本机固件实测可用), 其他动作走sport命令
# 音频输出: 常驻队列轨道, 连接时addTrack一次, 之后每次播报只灌PCM数据
import asyncio, os, sys, json, wave, time, hashlib, fractions
import numpy as np
import sounddevice as sd
import edge_tts
import av
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from faster_whisper import WhisperModel

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

ROBOT_IP = os.environ.get("UNITREE_ROBOT_IP", "192.168.123.161")
SR = 16000
REC_SECONDS = 3
DOG_VOLUME = 8          # 狗的音量0-10
VOICE = "zh-CN-YunxiNeural"
WALK_SECONDS = 2.0      # 移动类命令走多久

# ---------- 命令表 ----------
# 类型: stop=停 / action=一次性动作 / walk=摇杆移动(axes)
# action参数: (SPORT_CMD名, 狗的回答台词)
COMMANDS = [
    (["停", "停下", "停止", "别动", "不要动", "站住"], "stop", None),
    (["站起来", "站立", "起身", "起立", "站起來", "粘起来", "沾起来", "粘起來"], "action", ("StandUp", "好的，我起来了")),
    (["趴下", "卧倒", "臥倒"], "action", ("StandDown", "我趴下休息啦")),
    (["前进", "向前", "往前", "前進", "往前走"], "walk", ({"ly": 0.5}, "向前走咯")),
    (["后退", "向后", "往后", "後退", "倒退"], "walk", ({"ly": -0.4}, "退后")),
    (["左转", "向左", "左拐", "左轉"], "walk", ({"rx": -0.5}, "向左转")),
    (["右转", "向右", "右拐", "右轉"], "walk", ({"rx": 0.5}, "向右转")),
    (["跳舞", "舞蹈"], "action", ("Dance1", "音乐起，看我的")),
    (["打招呼", "你好", "挥手", "问好", "問好", "hello"], "action", ("Hello", "你好呀，很高兴见到你")),
    (["比心", "爱心", "比星", "必心"], "action", ("FingerHeart", "给你比个心")),
]

# ---------- 常驻音频轨道: 空闲送静音, 播报时灌真实音频 ----------
RATE = 48000                # WebRTC音频采样率
PTIME_SAMPLES = 960         # 每帧20毫秒
AUDIO_TRACK = None          # 连接成功后创建

class QueueAudioTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._q = asyncio.Queue()
        self._pts = 0
        self._start = None
        self._delivered = 0

    def _make_frame(self, pcm):
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, RATE)
        self._pts += PTIME_SAMPLES
        return frame

    def play_file(self, path):
        """在事件循环中入队；解码由 decode_file 在线程中完成。"""
        chunks = self.decode_file(path)
        return self.enqueue(chunks)

    def enqueue(self, chunks):
        while not self._q.empty():
            try: self._q.get_nowait()
            except asyncio.QueueEmpty: break
        for chunk in chunks:
            self._q.put_nowait(chunk)
        return len(chunks) * PTIME_SAMPLES / RATE

    @staticmethod
    def decode_file(path):
        resampler = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
        buf = None
        with av.open(path) as container:
            for frame in container.decode(audio=0):
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray().reshape(-1)
                    buf = arr if buf is None else np.concatenate([buf, arr])
            for rf in resampler.resample(None):
                arr = rf.to_ndarray().reshape(-1)
                buf = arr if buf is None else np.concatenate([buf, arr])
        if buf is None or len(buf) == 0:
            print("[TTS] 文件里没有音频流:", path)
            raise ValueError(f"没有可播放的音频: {path}")
        need = PTIME_SAMPLES * 2   # 960采样 x 2声道
        if len(buf) % need:
            buf = np.concatenate([buf, np.zeros(need - len(buf) % need, np.int16)])
        return [buf[i:i + need].astype(np.int16) for i in range(0, len(buf), need)]

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if self._start is None:
            self._start = time.monotonic()
        try:
            pcm = self._q.get_nowait()
        except asyncio.QueueEmpty:
            pcm = np.zeros(PTIME_SAMPLES * 2, np.int16)   # 空闲时送静音, 保持时间轴连续
        self._delivered += 1
        wait = self._start + self._delivered * PTIME_SAMPLES / RATE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        return self._make_frame(pcm)

# ---------- 语音合成: edge-tts在线(音质好) -> pyttsx3离线兜底 ----------
_tts_lock = asyncio.Lock()
MIN_AUDIO_BYTES = 10000   # 小于这个大小的音频文件视为损坏

def _synth_offline(text, path):
    """用Windows自带语音离线合成, 成功返回True"""
    try:
        import pyttsx3
    except ImportError:
        return False
    engine = pyttsx3.init()
    for v in engine.getProperty("voices"):
        nm = (v.id + " " + v.name).lower()
        if "zh" in nm or "huihui" in nm:
            engine.setProperty("voice", v.id)
            break
    engine.save_to_file(text, path)
    engine.runAndWait()
    return os.path.exists(path) and os.path.getsize(path) > MIN_AUDIO_BYTES

async def _synth(text, path):
    """合成到path: 在线2次尝试 -> 离线兜底; 原子写入防坏文件"""
    if os.path.exists(path) and os.path.getsize(path) > MIN_AUDIO_BYTES:
        return True
    tmp = path + ".part"
    for attempt in (1, 2):
        try:
            print(f"[TTS] 在线合成第{attempt}次（最多8秒）")
            await asyncio.wait_for(edge_tts.Communicate(text, VOICE).save(tmp), timeout=8)
            if os.path.exists(tmp) and os.path.getsize(tmp) > MIN_AUDIO_BYTES:
                os.replace(tmp, path)
                return True
            print(f"[TTS] 在线第{attempt}次: 文件过小, 视为失败")
        except Exception as e:
            print(f"[TTS] 在线合成第{attempt}次失败: {type(e).__name__}")
        finally:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
        await asyncio.sleep(1)
    try:
        ok = await asyncio.to_thread(_synth_offline, text, tmp)
        if ok:
            os.replace(tmp, path)
            print("[TTS] 在线不可用, 已用离线语音兜底(音质一般)")
            return True
        print("[TTS] 离线兜底不可用(没装pyttsx3或没有中文语音包)")
    except Exception as e:
        print("[TTS] 离线兜底失败:", type(e).__name__)
    return False

async def dog_say(conn, text):
    async with _tts_lock:
        if AUDIO_TRACK is None:
            return
        h = hashlib.sha256((VOICE + text).encode()).hexdigest()[:12]
        path = f"tts_cache_{h}.audio"
        if not await _synth(text, path):
            return
        try:
            chunks = await asyncio.to_thread(AUDIO_TRACK.decode_file, path)
            duration = AUDIO_TRACK.enqueue(chunks)
            print(f"[TTS] 发送播报: {text}（{duration:.1f}秒）")
            await asyncio.sleep(duration + 0.3)
            print("[TTS] 音频发送等待结束（是否出声请以现场为准）")
        except Exception as e:
            print("[TTS] 播放失败:", type(e).__name__, str(e))

# ---------- 走路 (无线手柄通道: 50Hz发摇杆量, 到点归零) ----------
_walk_task = None

async def _joystick_walk(pub_sub, axes, seconds):
    msg = {"lx": axes.get("lx", 0), "ly": axes.get("ly", 0),
           "rx": axes.get("rx", 0), "ry": 0, "keys": 0}
    zero = {"lx": 0, "ly": 0, "rx": 0, "ry": 0, "keys": 0}
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            pub_sub.publish_without_callback(RTC_TOPIC["WIRELESS_CONTROLLER"], msg)
            await asyncio.sleep(0.02)
    finally:
        pub_sub.publish_without_callback(RTC_TOPIC["WIRELESS_CONTROLLER"], zero)

def _cancel_walk():
    global _walk_task
    if _walk_task and not _walk_task.done():
        _walk_task.cancel()
    _walk_task = None

# ---------- 录音+识别 (阻塞函数, 放线程里跑) ----------
def record_and_transcribe(model, dev):
    audio = sd.rec(int(REC_SECONDS * SR), samplerate=SR, channels=1,
                   dtype="float32", device=dev)
    sd.wait()
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 0.005:
        return ""
    path = "last_utt.wav"
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    segments, _ = model.transcribe(path, language="zh", vad_filter=True)
    return "".join(s.text for s in segments).strip()

def match_command(text):
    t = text.replace(" ", "")
    if not t:
        return None
    for keys, ctype, arg in COMMANDS:
        if any(k in t for k in keys):
            return ctype, arg
    return None

# ---------- 主流程 ----------
async def main():
    global AUDIO_TRACK, _walk_task
    dev = int(sys.argv[1]) if len(sys.argv) > 1 else sd.default.device[0]

    print("加载识别模型...")
    model = WhisperModel("small", device="cpu", compute_type="int8")

    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=ROBOT_IP)
    try:
        await conn.connect()
        print("已连接狗:", ROBOT_IP)

        AUDIO_TRACK = QueueAudioTrack()
        conn.pc.addTrack(AUDIO_TRACK)   # 常驻音频轨道, 连接时挂一次

        r = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        try:
            mode = json.loads(r["data"]["data"]).get("name")
        except Exception:
            mode = None
        if mode != "normal":
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": 1002, "parameter": {"name": "normal"}})
            print(f"从{mode}切换到normal, 等站稳(8秒)...")
            await asyncio.sleep(8)

        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["VUI"], {"api_id": 1003, "parameter": {"volume": DOG_VOLUME}})
        print(f"狗音量设为{DOG_VOLUME}/10, 音频通道已建立")
    except Exception as e:
        print("连接失败:", e, "\n(等20秒再试, 并确认手机App没连着狗)")
        await conn.disconnect()
        return

    pub = conn.datachannel.pub_sub
    print("\n=== 语音控制就绪 ===")
    print("支持:", " / ".join(keys[0] for keys, *_ in COMMANDS))
    print("按回车后说3秒话, Ctrl+C 退出\n")

    try:
        while True:
            await asyncio.to_thread(input, "按回车开始说话...")
            text = await asyncio.to_thread(record_and_transcribe, model, dev)
            print("识别结果:", text or "(没听清)")
            cmd = match_command(text)
            if cmd is None:
                await dog_say(conn, "我没听清，你再说一遍")
                continue
            ctype, arg = cmd

            if ctype == "stop":
                _cancel_walk()
                pub.publish_without_callback(RTC_TOPIC["WIRELESS_CONTROLLER"],
                                             {"lx": 0, "ly": 0, "rx": 0, "ry": 0, "keys": 0})
                await pub.publish_request_new(RTC_TOPIC["SPORT_MOD"],
                                              {"api_id": SPORT_CMD["StopMove"]})
                asyncio.create_task(dog_say(conn, "好，停"))

            elif ctype == "walk":
                axes, line = arg
                _cancel_walk()
                _walk_task = asyncio.create_task(_joystick_walk(pub, axes, WALK_SECONDS))
                asyncio.create_task(dog_say(conn, line))

            elif ctype == "action":
                name, line = arg
                r = await pub.publish_request_new(
                    RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD[name]})
                code = r.get("data", {}).get("header", {}).get("status", {}).get("code", -1)
                asyncio.create_task(dog_say(conn, line if code == 0 else "这个动作我暂时做不到"))
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        _cancel_walk()
        pub.publish_without_callback(RTC_TOPIC["WIRELESS_CONTROLLER"],
                                     {"lx": 0, "ly": 0, "rx": 0, "ry": 0, "keys": 0})
        print("\n正在断开...")
        await conn.disconnect()
        await asyncio.sleep(1)
        print("已干净退出")

if __name__ == "__main__":
    asyncio.run(main())
