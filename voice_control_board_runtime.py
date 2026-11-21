# 板载语音运行模块：由 voice_control_board.py 在连接成功后导入
# 跑在算力板上: USB麦(arecord) -> SenseVoice识别 -> 命令表 -> 狗动 + MeloTTS合成 -> 狗扬声器
# 走路/动作逻辑与PC版完全一致; 识别与合成换成纯离线引擎
# 入口(板上): python3 ~/go2_voice/voice_control_board.py
import asyncio, os, sys, json, wave, time, hashlib, fractions, re, subprocess
import numpy as np
import av
import sherpa_onnx
from functools import partial
from collections import deque
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD


async def run_in_thread(func, *args, **kwargs):
    """Python 3.8 没有 asyncio.to_thread；在线程中运行阻塞任务。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

ROBOT_IP = "192.168.123.161"
REC_SECONDS = 3
MIC_DEVICE = "plughw:CARD=Device,DEV=0"  # Usb Audio Device；用名称避免数字卡号变化
DOG_VOLUME = 8
WALK_SECONDS = 2.0
MODELS = "/home/unitree/go2_voice/models"
SENSE_DIR = os.path.join(MODELS, "sensevoice")   # 里面要有 model.int8.onnx + tokens.txt
TTS_DIR = os.path.join(MODELS, "melo_tts")       # model.onnx + lexicon.txt + tokens.txt + dict/
REC_WAV = "/home/unitree/go2_voice/last_utt.wav"

# ---------- 命令表：reply 类型只播报 ----------
COMMANDS = [
    (["回收医疗废物", "收集医疗废物", "去回收医疗废物", "回收医疗垃圾", "收集医疗垃圾",
      "去回收医疗垃圾", "开始回收任务", "执行回收任务",
      "回收医疗费物", "收集医疗费物", "去回收医疗费物"], "reply", "好的，我去执行任务"),
    (["停", "停下", "停止", "别动", "不要动", "站住"], "stop", None),
    (["站起来", "站立", "起身", "起立", "站起來", "粘起来", "沾起来", "粘起來"], "action", ("StandUp", "好的，我起来了")),
    (["趴下", "卧倒", "臥倒"], "action", ("StandDown", "我趴下休息啦")),
    (["前进", "向前", "往前", "前進", "往前走"], "walk", ({"ly": 0.5}, "向前走咯")),
    (["后退", "向后", "往后", "後退", "倒退"], "walk", ({"ly": -0.4}, "退后")),
    (["左转", "向左", "左拐", "左轉"], "walk", ({"rx": -0.5}, "向左转")),
    (["右转", "向右", "右拐", "右轉"], "walk", ({"rx": 0.5}, "向右转")),
    (["跳舞", "舞蹈"], "action", ("Dance1", "音乐起，看我的")),
    (["打招呼", "你好", "挥手", "问好", "問好", "hello"], "action", ("Hello", "你好呀，很高兴见到你")),
    (["比心", "笔心", "爱心", "比星", "必心"], "action", ("FingerHeart", "给你比个心")),
]

# ---------- 常驻音频轨道 (与PC版一致: 连接时挂一次, 播报时灌PCM) ----------
RATE = 48000
PTIME_SAMPLES = 960
AUDIO_TRACK = None

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
        return self.enqueue(self.decode_file(path))

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
            raise ValueError("文件中没有可播放音频")
        need = PTIME_SAMPLES * 2
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
            pcm = np.zeros(PTIME_SAMPLES * 2, np.int16)
        self._delivered += 1
        wait = self._start + self._delivered * PTIME_SAMPLES / RATE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        return self._make_frame(pcm)

# ---------- 识别: arecord录音 -> SenseVoice ----------
_recognizer = None

def load_asr():
    global _recognizer
    model_path = os.path.join(SENSE_DIR, "model.int8.onnx")
    tokens = os.path.join(SENSE_DIR, "tokens.txt")
    if not (os.path.exists(model_path) and os.path.exists(tokens)):
        raise FileNotFoundError(f"识别模型缺失, 请确认 {SENSE_DIR}/ 里有 model.int8.onnx 和 tokens.txt")
    # 2.0.1 wheel 的 offline_recognizer.py 提供此工厂方法。
    # 顶层 OfflineRecognizer 是 Python 包装类，不能直接传 config。
    _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model_path,
        tokens=tokens,
        num_threads=2,
        provider="cpu",
        language="",
        use_itn=True,
    )
    print("识别模型加载完成")

def record_and_transcribe(device=MIC_DEVICE):
    """阻塞函数, 调用时放线程里"""
    try:
        result = subprocess.run(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000",
             "-c", "1", "-d", str(REC_SECONDS), REC_WAV],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=REC_SECONDS + 7)
    except subprocess.TimeoutExpired as error:
        detail = error.stderr or b""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"设备 {device} 的{REC_SECONDS}秒文件录音在{REC_SECONDS + 7}秒内未完成；"
            f"录音子进程已终止并回收。arecord 输出: {detail.strip() or '(无)'}") from error
    if result.returncode:
        raise RuntimeError("arecord 录音失败: " + result.stderr.decode("utf-8", errors="replace").strip())
    return transcribe_wav(REC_WAV)


def transcribe_wav(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000:
            raise ValueError("识别输入需要 16000Hz、单声道、16位 PCM WAV")
        d = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    return transcribe_samples(d)


def transcribe_pcm(pcm):
    return transcribe_samples(np.frombuffer(pcm, dtype="<i2").astype(np.float32))


def transcribe_samples(d):
    if not len(d):
        raise ValueError("录音文件为空")
    rms = float(np.sqrt((d ** 2).mean()))
    if rms < 100:
        return ""
    stream = _recognizer.create_stream()
    stream.accept_waveform(16000, d / 32768.0)
    _recognizer.decode_stream(stream)
    text = re.sub(r"<\|[^|]*\|>", "", stream.result.text).strip()   # 去掉SenseVoice的语言/情绪标记
    return text

def match_command(text):
    t = text.replace(" ", "")
    if not t:
        return None
    # 回车模式的回收口令也要求整句匹配，不在普通对话里找子串。
    exact = exact_voice_command(t)
    if exact and exact[0] == "reply":
        return exact
    for keys, ctype, arg in COMMANDS:
        if ctype != "reply" and any(k in t for k in keys):
            return ctype, arg
    return None


def normalize_voice(text):
    return "".join(char for char in text if char.isalnum()).lower()


def exact_voice_command(text):
    """自动听取时只接受完整短指令，拒绝聊天中的子串和否定句。"""
    text = normalize_voice(text)
    if text.startswith("请"):
        text = text[1:]
    for suffix in ("一下", "吧"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    for keys, kind, arg in COMMANDS:
        if text in [normalize_voice(key) for key in keys]:
            return kind, arg
    if text == "暂停":
        return "stop", None
    return None


class WakeGate:
    """每条命令一次唤醒；单独唤醒后给下一句话 8 秒窗口。"""
    def __init__(self, word, timeout=8.0):
        self.word = normalize_voice(word)
        if len(self.word) < 2:
            raise ValueError("唤醒词至少需要两个字")
        self.timeout = timeout
        self.deadline = 0.0

    def arm(self, now):
        self.deadline = now + self.timeout

    def accept(self, text, now):
        text = normalize_voice(text)
        if not text:
            return "ignore", None
        # 完整停止短语无需唤醒；句中包含“停”不算停止指令。
        command = exact_voice_command(text)
        if command and command[0] == "stop":
            self.deadline = 0.0
            return "command", command
        if text.startswith(self.word):
            text = text[len(self.word):]
            if not text:
                self.arm(now)
                return "wake", None
        elif not (self.deadline and now < self.deadline):
            return "ignore", None
        self.deadline = 0.0
        command = exact_voice_command(text)
        return ("command", command) if command else ("unknown", None)


class SpeechSegmenter:
    """16k PCM、20ms 一帧；用音量和尾部静音分句，不依赖新增模型。"""
    def __init__(self, threshold=500):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.before = deque(maxlen=10)  # 保留开口前 200ms
        self.frames = []
        self.voiced = 0
        self.silence = 0
        self.overlong = False

    @staticmethod
    def rms(pcm):
        data = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        return float(np.sqrt(np.mean(data * data)))

    def is_loud(self, pcm):
        return self.rms(pcm) >= self.threshold

    def feed(self, pcm):
        loud = self.is_loud(pcm)
        if self.overlong:
            self.silence = 0 if loud else self.silence + 1
            if self.silence >= 30:
                self.reset()
            return None
        if not self.frames:
            if not loud:
                self.before.append(pcm)
                return None
            self.frames = list(self.before)
            self.before.clear()
        self.frames.append(pcm)
        if loud:
            self.voiced += 1
            self.silence = 0
        else:
            self.silence += 1
        if self.silence >= 30:  # 600ms 静音结束一句
            result = b"".join(self.frames) if self.voiced >= 9 else None
            self.reset()
            return result
        if len(self.frames) >= 250:  # 超长语音整句丢弃，不能拆出意外动作
            self.frames = []
            self.overlong = True
            self.silence = 0
        return None


class CaptureUnavailable(RuntimeError):
    pass


class ContinuousMicrophone:
    """持续排空 arecord，暂停时丢弃声音，禁止积压播报后再识别。"""
    def __init__(self, device, threshold, show_levels=False):
        self.device = device
        self.segmenter = SpeechSegmenter(threshold)
        self.minimum_threshold = threshold
        self.show_levels = show_levels
        self.calibration = []
        self.calibrated = False
        self.level = 0.0
        self.peak_level = 0.0
        self.last_meter = time.monotonic()
        self.last_audio = time.monotonic()
        self.audio_bytes = 0
        self.enabled = False
        self.queue = asyncio.Queue(maxsize=1)
        self.process = None
        self.reader = None
        self.errors = None
        self.stderr_tail = b""
        self.waiting_quiet = True
        self.quiet_frames = 0

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            "arecord", "-D", self.device, "-f", "S16_LE", "-r", "16000",
            "-c", "1", "-t", "raw", "-", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        self.last_audio = time.monotonic()
        self.errors = asyncio.create_task(self._read_errors())
        self.reader = asyncio.create_task(self._read_audio())
        print(f"[收音 v8.2] 设备 {self.device}；请先保持安静约1秒，正在测背景音。", flush=True)
        self.resume()
        return self

    def _clear(self):
        self.segmenter.reset()
        while not self.queue.empty():
            self.queue.get_nowait()

    def pause(self):
        self.enabled = False
        self._clear()

    def resume(self):
        self._clear()
        # 等300ms安静再收新句，避免截取尾音或一句话的后半段。
        self.waiting_quiet = True
        self.quiet_frames = 0
        self.enabled = True

    async def _read_errors(self):
        while True:
            chunk = await self.process.stderr.read(1024)
            if not chunk:
                return
            self.stderr_tail = (self.stderr_tail + chunk)[-2048:]

    async def _read_audio(self):
        try:
            while True:
                pcm = await self.process.stdout.readexactly(640)
                if self.audio_bytes == 0:
                    print("[收音] 已收到连续录音的第一帧音频。", flush=True)
                self.audio_bytes += len(pcm)
                self.last_audio = time.monotonic()
                self.level = self.segmenter.rms(pcm)
                self.peak_level = max(self.peak_level, self.level)
                if not self.calibrated:
                    self.calibration.append(self.level)
                    if len(self.calibration) >= 50:
                        background = float(np.percentile(self.calibration, 20))
                        self.segmenter.threshold = max(self.minimum_threshold, background * 2.5 + 100)
                        self.calibrated = True
                        self.waiting_quiet = True
                        self.segmenter.reset()
                        print(f"[收音] 背景音 {background:.0f}，门槛 {self.segmenter.threshold:.0f}；可以说话了。", flush=True)
                    continue
                if self.show_levels and time.monotonic() - self.last_meter >= 2:
                    if not self.enabled:
                        state = "正在识别或播报，暂停收句"
                    elif self.waiting_quiet:
                        state = "等待短暂安静"
                    elif self.segmenter.overlong:
                        state = "声音持续过长，等停顿"
                    elif self.segmenter.frames:
                        state = "收音中，等说完后停顿"
                    else:
                        state = "等待说话"
                    print(f"[收音] 音量 {self.level:.0f}，近2秒峰值 {self.peak_level:.0f}，门槛 {self.segmenter.threshold:.0f}；{state}", flush=True)
                    self.peak_level = self.level
                    self.last_meter = time.monotonic()
                if not self.enabled:
                    self.segmenter.reset()
                    continue
                if self.waiting_quiet:
                    self.quiet_frames = 0 if self.segmenter.is_loud(pcm) else self.quiet_frames + 1
                    if self.quiet_frames >= 15:
                        self.waiting_quiet = False
                    continue
                utterance = self.segmenter.feed(pcm)
                if utterance is not None:
                    # 只保留一句；识别和播报时继续排空麦克风而不留存声音。
                    self.pause()
                    self.queue.put_nowait(utterance)
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError as error:
            # stdout EOF 本身已经是录音失败，不无限等异常子进程退出。
            try:
                await asyncio.wait_for(asyncio.shield(self.errors), 0.5)
            except asyncio.TimeoutError:
                pass
            self._clear()
            detail = self.stderr_tail.decode("utf-8", errors="replace").strip()
            self.queue.put_nowait(CaptureUnavailable("麦克风录音结束: " + (detail or str(error))))
        except Exception as error:
            self._clear()
            self.queue.put_nowait(error)

    async def next_utterance(self):
        # 进程异常不能被 pause/resume 清除后变成永久等待。
        while True:
            if self.reader.done():
                error = self.reader.exception()
                detail = self.stderr_tail.decode("utf-8", errors="replace").strip()
                raise CaptureUnavailable("麦克风录音已停止: " + (detail or str(error or "arecord exited")))
            try:
                item = await asyncio.wait_for(self.queue.get(), 5)
            except asyncio.TimeoutError:
                if time.monotonic() - self.last_audio >= 5:
                    detail = self.stderr_tail.decode("utf-8", errors="replace").strip()
                    raise CaptureUnavailable(
                        f"连续录音5秒未返回音频（已收到 {self.audio_bytes} 字节，"
                        f"进程退出码 {self.process.returncode}）；" + (detail or "arecord 暂无错误输出"))
                continue
            if isinstance(item, Exception):
                raise item
            return item

    async def __aexit__(self, *exc):
        self.enabled = False
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        # 退出时仍排空 stdout，否则满管道可能阻止 Process.wait 返回。
        async def drain():
            while await self.process.stdout.read(4096):
                pass
        draining = asyncio.create_task(drain())
        if self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(self.process.wait(), 2)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        await asyncio.gather(self.errors, draining, return_exceptions=True)


async def listen_hands_free(mic_device, wake_word, vad_threshold, on_event, show_levels=False):
    try:
        await listen_stream(mic_device, wake_word, vad_threshold, on_event, show_levels)
    except CaptureUnavailable as error:
        # 先让 async with 结束旧录音并回收，再打开同一设备录文件。
        print("[收音]", str(error), flush=True)
        print("[收音] 自动切换到已验证的3秒文件录音，仍无需按回车。", flush=True)
        await listen_file_chunks(mic_device, wake_word, vad_threshold, on_event, show_levels)


async def listen_file_chunks(mic_device, wake_word, vad_threshold, on_event, show_levels=False):
    """连续流不可用时，复用板上已成功的定长 WAV 录音路径。"""
    gate = WakeGate(wake_word)
    while True:
        print(f"[收音] 开始录音{REC_SECONDS}秒，请说“{wake_word}，打招呼”。", flush=True)
        text = await run_in_thread(record_and_transcribe, mic_device)
        print("识别结果:", text or "(未识别出文字)", flush=True)
        if text:
            event, command = gate.accept(text, time.monotonic())
            spoken = await on_event(event, command)
            if spoken:
                await asyncio.sleep(0.5)
            if event == "wake":
                gate.arm(time.monotonic())


async def listen_stream(mic_device, wake_word, vad_threshold, on_event, show_levels=False):
    gate = WakeGate(wake_word)
    async with ContinuousMicrophone(mic_device, vad_threshold, show_levels=show_levels) as microphone:
        print(f"[免回车] 说“{wake_word}，打招呼”，或先叫“{wake_word}”等回应再说指令。", flush=True)
        print("[免回车] 每次指令重新唤醒；播报时暂停听取，Ctrl+C 退出。", flush=True)
        while True:
            pcm = await microphone.next_utterance()
            if show_levels:
                print(f"[收音] 已收到一句 {len(pcm) / 32000:.2f}秒音频，正在识别...", flush=True)
            text = await run_in_thread(transcribe_pcm, pcm)
            if text:
                print("识别结果:", text, flush=True)
                event, command = gate.accept(text, time.monotonic())
                spoken = await on_event(event, command)
                if spoken:
                    # dog_say 已等完整播报时长；再丢弃一段尾声。
                    await asyncio.sleep(0.5)
                if event == "wake":
                    gate.arm(time.monotonic())
            elif show_levels:
                print("识别结果: (未识别出文字)", flush=True)
            microphone.resume()


async def run_listen_only(args):
    print("[试听] 仅使用麦克风和识别模型，不连接狗、不播放声音、不执行动作。", flush=True)
    await run_in_thread(load_asr)
    async def on_event(event, command):
        if event == "wake":
            print("[试听] 已唤醒，请在8秒内说一条指令。", flush=True)
        elif event == "command":
            print("[试听] 指令匹配:", command, "（未执行）", flush=True)
        elif event == "unknown":
            print("[试听] 已唤醒，但不是支持的完整指令。", flush=True)
        return False
    await listen_hands_free(args.mic_device, args.wake_word, args.vad_threshold, on_event,
                           show_levels=True)

# ---------- 说话: MeloTTS离线合成 -> 常驻轨道 ----------
# Python 3.8 的 Lock 构造会取得事件循环；导入阶段不能创建它。
# asyncio.run 使用另一个新循环，播报锁必须在运行中的循环内创建。
_tts_lock = None
_tts = None

def select_tts_model(directory):
    """Skip Git LFS pointer files, which are text rather than ONNX data."""
    for name in ("model.int8.onnx", "model.onnx"):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as source:
            header = source.read(200)
        if header.startswith(b"version https://git-lfs.github.com/spec/v1"):
            print(f"[TTS] 跳过 Git LFS 占位文件: {path}")
            continue
        if not header:
            print(f"[TTS] 跳过空文件: {path}")
            continue
        print(f"[TTS] 使用模型: {path} ({os.path.getsize(path)} 字节)")
        return path
    raise FileNotFoundError(f"{directory} 中没有可用模型；需要真正的 ONNX 文件，不能是 Git LFS 指针")

def load_tts():
    global _tts
    model = select_tts_model(TTS_DIR)
    lexicon = os.path.join(TTS_DIR, "lexicon.txt")
    tokens = os.path.join(TTS_DIR, "tokens.txt")
    dict_dir = os.path.join(TTS_DIR, "dict")
    if not all(os.path.exists(p) for p in (model, lexicon, tokens, dict_dir)):
        raise FileNotFoundError(f"合成模型缺失, 请确认 {TTS_DIR}/ 里有模型/lexicon/tokens/dict")
    # 新版melo模型: 文本归一化用dict目录 + fst规则(代替旧版espeak-ng-data)
    fsts = ",".join(os.path.join(TTS_DIR, f) for f in ("date.fst", "number.fst", "phone.fst"))
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=model,
                lexicon=lexicon,
                tokens=tokens,
                data_dir="",
                dict_dir=dict_dir,
            ),
            num_threads=2,
            debug=False,
            provider="cpu",
        ),
        rule_fsts=fsts if os.path.exists(os.path.join(TTS_DIR, "number.fst")) else "",
        rule_fars="",
        max_num_sentences=-1,
        silence_scale=0.0,
    )
    _tts = sherpa_onnx.OfflineTts(config)
    print("合成模型加载完成")

def _synth_offline(text, path):
    audio = _tts.generate(text=text, sid=0, speed=1.0)
    if audio.samples is None or len(audio.samples) == 0:
        return False
    pcm = (np.clip(audio.samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(audio.sample_rate)
        w.writeframes(pcm.tobytes())
    return True

async def dog_say(conn, text):
    global _tts_lock
    if _tts_lock is None:
        _tts_lock = asyncio.Lock()
    async with _tts_lock:
        if AUDIO_TRACK is None or _tts is None:
            return
        h = hashlib.sha256(text.encode()).hexdigest()[:12]
        path = f"/home/unitree/go2_voice/tts_cache_{h}.wav"
        if not os.path.exists(path):
            try:
                ok = await run_in_thread(_synth_offline, text, path)
                if not ok:
                    print("[TTS] 合成失败(空音频)")
                    return
            except Exception as e:
                print("[TTS] 合成失败:", type(e).__name__)
                return
        try:
            chunks = await run_in_thread(AUDIO_TRACK.decode_file, path)
            duration = AUDIO_TRACK.enqueue(chunks)
            print(f"[TTS] 发送播报: {text} ({duration:.1f}秒)")
            await asyncio.sleep(duration + 0.3)
        except Exception as e:
            print("[TTS] 播放失败:", type(e).__name__, str(e))

# ---------- 走路 (无线手柄通道, 与PC版一致) ----------
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

# ---------- 离线检查（不会创建机器人连接） ----------
async def run_offline(args):
    print("加载识别模型...")
    await run_in_thread(load_asr)
    if args.transcribe_wav:
        text = await run_in_thread(transcribe_wav, args.transcribe_wav)
        print("识别结果:", text or "(没听清)")
        print("匹配指令:", match_command(text))
        return
    print("加载合成模型...")
    await run_in_thread(load_tts)

    if args.check_models:
        print("模型初始化检查通过；未连接机器人、未发送动作。")
        return

    if args.self_test:
        path = os.path.join(os.path.dirname(REC_WAV), "self_test_tts.wav")
        started = time.monotonic()
        if not await run_in_thread(_synth_offline, "你好，请站起来", path):
            raise RuntimeError("离线合成返回空音频")
        print(f"[自检] 合成耗时 {time.monotonic() - started:.2f}秒，文件: {path}")
        chunks = await run_in_thread(QueueAudioTrack.decode_file, path)
        if not chunks:
            raise RuntimeError("音频转换后为空")
        print(f"[自检] WebRTC 音频转换通过，时长 {len(chunks) * 0.02:.2f}秒")
        with av.open(path) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            frames = []
            for frame in container.decode(audio=0):
                frames.extend(resampler.resample(frame))
            frames.extend(resampler.resample(None))
        asr_path = os.path.join(os.path.dirname(REC_WAV), "self_test_asr.wav")
        with wave.open(asr_path, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"".join(frame.to_ndarray().tobytes() for frame in frames))
        started = time.monotonic()
        text = await run_in_thread(transcribe_wav, asr_path)
        print(f"[自检] 识别耗时 {time.monotonic() - started:.2f}秒，结果: {text}")
        if not text:
            raise RuntimeError("合成语音识别结果为空")
        print("[自检] 匹配指令:", match_command(text))
        print("离线语音自检通过；未连接机器人、未发送动作。麦克风和扬声器仍需实测。")
        return


# ---------- 主流程（连接由轻量入口负责） ----------
async def wait_for_enter():
    """板上用可取消的终端读取，避免退出时等不到 input 线程结束。"""
    if os.name == "nt":
        await run_in_thread(input, "按回车开始说话...")
        return
    print("按回车开始说话...", end="", flush=True)
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    def readable():
        if not ready.done():
            ready.set_result(None)
    loop.add_reader(sys.stdin.fileno(), readable)
    try:
        await ready
        if not sys.stdin.readline():
            raise EOFError
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def run_control(conn, volume=DOG_VOLUME, hands_free=False, wake_word="小狗",
                      mic_device=MIC_DEVICE, vad_threshold=500):
    global AUDIO_TRACK, _walk_task
    pub = conn.datachannel.pub_sub
    speech_tasks = set()
    def say(text):
        task = asyncio.create_task(dog_say(conn, text))
        speech_tasks.add(task)
        task.add_done_callback(speech_tasks.discard)

    async def execute(command, wait_for_speech=False):
        global _walk_task
        ctype, arg = command
        if ctype == "reply":
            line = arg
        elif ctype == "stop":
            await stop_walk()
            pub.publish_without_callback(
                RTC_TOPIC["WIRELESS_CONTROLLER"],
                {"lx": 0, "ly": 0, "rx": 0, "ry": 0, "keys": 0})
            await pub.publish_request_new(RTC_TOPIC["SPORT_MOD"],
                                          {"api_id": SPORT_CMD["StopMove"]})
            line = "好，停"
        elif ctype == "walk":
            axes, line = arg
            await stop_walk()
            _walk_task = asyncio.create_task(_joystick_walk(pub, axes, WALK_SECONDS))
        elif ctype == "action":
            name, line = arg
            r = await pub.publish_request_new(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD[name]})
            code = r.get("data", {}).get("header", {}).get("status", {}).get("code", -1)
            if code != 0:
                line = "这个动作我暂时做不到"
        if wait_for_speech:
            await dog_say(conn, line)
        else:
            say(line)

    async def on_voice_event(event, command):
        if event == "ignore":
            return False
        if event == "wake":
            print("[唤醒] 请在8秒内说一条指令。", flush=True)
            await dog_say(conn, "我在，请说")
        elif event == "unknown":
            await dog_say(conn, "我没听清指令，请重新叫我再说一遍")
        else:
            await execute(command, wait_for_speech=True)
        return True

    try:
        AUDIO_TRACK = QueueAudioTrack()
        conn.pc.addTrack(AUDIO_TRACK)
        r = await pub.publish_request_new(RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        try:
            mode = json.loads(r["data"]["data"]).get("name")
        except Exception:
            mode = None
        if mode != "normal":
            await pub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": 1002, "parameter": {"name": "normal"}})
            print(f"从{mode}切换到normal, 等站稳(8秒)...")
            await asyncio.sleep(8)
        await pub.publish_request_new(
            RTC_TOPIC["VUI"], {"api_id": 1003, "parameter": {"volume": volume}})
        print(f"狗音量设为{volume}/10, 音频通道已建立")
        print("\n=== 板载语音控制就绪(纯离线) ===")
        print("支持:", " / ".join(keys[0] for keys, *_ in COMMANDS))
        if hands_free:
            await listen_hands_free(mic_device, wake_word, vad_threshold, on_voice_event)
            return
        print("按回车后说3秒话, Ctrl+C 退出\n")
        while True:
            await wait_for_enter()
            text = await run_in_thread(record_and_transcribe, mic_device)
            print("识别结果:", text or "(没听清)")
            cmd = match_command(text)
            if cmd is None:
                say("我没听清，你再说一遍")
                continue
            await execute(cmd)
    except EOFError:
        pass
    finally:
        try:
            await stop_walk()
        finally:
            tasks = list(speech_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if AUDIO_TRACK is not None:
                AUDIO_TRACK.stop()
                AUDIO_TRACK = None


async def stop_walk():
    global _walk_task
    task = _walk_task
    _walk_task = None
    if task is not None:
        if not task.done():
            task.cancel()
        # 等 finally 归零完成后，才允许下一次走路或断开连接。
        await asyncio.gather(task, return_exceptions=True)
