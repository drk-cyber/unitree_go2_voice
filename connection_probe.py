"""Read-only Go2 handshake probe. No motion or volume requests are sent."""
import argparse
import asyncio
import importlib
import logging
import sys
import time


def speech_imports():
    for name in ("numpy", "av", "sherpa_onnx"):
        module = importlib.import_module(name)
        print("[依赖]", name, getattr(module, "__version__", "unknown"), flush=True)


def sdp_summary(description):
    if description is None:
        return []
    # Do not print full SDP: it contains ICE credentials and fingerprints.
    return [line.strip() for line in description.sdp.splitlines()
            if line.startswith(("m=", "a=setup:", "a=group:", "a=mid:"))]


async def probe(connection_class, method, ip):
    conn = connection_class(method, ip=ip)
    started = time.monotonic()
    async def observe():
        previous = None
        while True:
            pc = conn.pc
            if pc is not None:
                transports = {id(t.sender.transport): t.sender.transport
                              for t in pc.getTransceivers() if t.sender.transport}
                if pc.sctp and pc.sctp.transport:
                    transports[id(pc.sctp.transport)] = pc.sctp.transport
                states = tuple((t.state, t.transport.state, t.transport.role)
                               for t in transports.values())
                summary = (pc.connectionState, pc.iceConnectionState, states,
                           tuple(sdp_summary(pc.remoteDescription)))
                if summary != previous:
                    print("[握手 %.1fs] peer=%s ICE=%s DTLS/ICE/role=%s" %
                          (time.monotonic() - started, summary[0], summary[1], states), flush=True)
                    if summary[3]:
                        print("[远端协商]", " | ".join(summary[3]), flush=True)
                    previous = summary
            await asyncio.sleep(0.25)
    monitor = asyncio.create_task(observe())
    try:
        await conn.connect()
        print("[结果] 连接及验证成功；没有发送动作。", flush=True)
        await asyncio.sleep(1)
        return 0
    except Exception as error:
        print("[结果]", type(error).__name__, str(error), flush=True)
        return 1
    finally:
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        await conn.disconnect()
        await asyncio.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Go2 只读连接诊断，不加载模型、不发动作")
    parser.add_argument("--speech-imports", choices=("none", "before", "after"), default="none",
                        help="none=与 board_test 相同；before/after=在通信库前/后导入语音依赖")
    parser.add_argument("--ip", default="192.168.123.161")
    args = parser.parse_args()
    # Only DTLS messages: full peer-connection debug logs contain SDP secrets.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[DTLS] %(message)s"))
    logger = logging.getLogger("aiortc.rtcdtlstransport")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    print("[测试] speech-imports=" + args.speech_imports, flush=True)
    if args.speech_imports == "before":
        speech_imports()
    from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
    if args.speech_imports == "after":
        speech_imports()
    for name in ("aiortc", "OpenSSL", "cryptography", "av"):
        module = importlib.import_module(name)
        print("[依赖]", name, getattr(module, "__version__", "unknown"), flush=True)
    from OpenSSL import SSL
    print("[加密库]", SSL.SSLeay_version(SSL.SSLEAY_VERSION).decode(), flush=True)
    raise SystemExit(asyncio.run(probe(UnitreeWebRTCConnection, WebRTCConnectionMethod.LocalSTA, args.ip)))


if __name__ == "__main__":
    main()
