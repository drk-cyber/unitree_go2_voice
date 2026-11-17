"""逐条定位 voice_control_board.py 中破坏 Go2 DTLS 握手的顶层语句。

已实测事实（2026-11-16 slot_timing_test）：
- 90秒安全间隔下，纯探针连接成功（A1/A2/C1）；
- 同样间隔，"导入主程序后连接"失败（B），失败签名固定为：
  信令成功 + ICE completed + DTLS 收发 0（DataChannelTimeoutError）；
- 5 秒快连的失败是信令层 429 / NoSdpAnswerError（会话未释放），
  属另一种故障，与本工具要定位的问题无关。

本工具不预设根因，只做定位：把主程序前 N 条顶层语句的源码逐字抄写
到临时模块 board_prefix_tmp.py（内容只来自项目自己的源码，无外部
输入），子进程正常 import 该模块（不调用 main），再跑与 baseline
相同的只读探针。失败按签名分类：
  success      连接及验证成功
  poison       DataChannelTimeoutError 签名（计入定位的唯一失败类）
  slot         429 / NoSdpAnswerError（会话未释放噪声：等待后重测同一前缀）
  script_error 子进程自身报错（中止并给出日志路径）
  unknown      其他（重测一次，仍未知则中止）

流程：锚点(prefix=0 必须 success) -> 嫌疑扫描(标准库块/numpy/av/
sherpa/aiortc) -> 二分收敛到单条语句 -> 交叉复核(低/高/低/高/基线)。
所有连接测试之间统一等待 90 秒（含噪声重测）。开始前请确保狗已
空闲 2 分钟。预计 15-35 分钟。

用法(板上):  python3 ~/go2_voice/import_connection_bisect.py
本地自检:    python3 import_connection_bisect.py --self-test （不连接狗）
"""
import argparse
import ast
import importlib
import tempfile
import time
from pathlib import Path
import subprocess
import sys

PREFIX_MODULE = "board_prefix_tmp"
GAP_SECONDS = 90
SUSPECTS = ("<stdlib>", "numpy", "av", "sherpa_onnx", "aiortc")


def statements(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
               ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Expr)
    nodes = []
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
                and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"):
            continue
        if not isinstance(node, allowed):
            raise RuntimeError("发现未预期的顶层语句，停止测试: line %d" % node.lineno)
        nodes.append(node)
    return nodes


def find_index(nodes, target):
    """返回目标 import 语句的 0 基下标；<stdlib> = 第一条 Import 语句。

    注意: 语义是"包含该语句需要 count = 下标 + 1"。
    """
    for idx, node in enumerate(nodes):
        if target == "<stdlib>":
            if isinstance(node, ast.Import):
                return idx
            continue
        if isinstance(node, ast.Import) and any(a.name == target for a in node.names):
            return idx
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == target:
            return idx
    return None


def describe(node, lines):
    return "line %d: %s" % (node.lineno, lines[node.lineno - 1].strip()[:80])


def write_prefix_module(root, lines, nodes, count):
    """把前 count 条顶层语句的源码逐字抄写到临时模块文件。

    每行末尾补换行符，保证语句之间不会粘连；内容只来自项目自己的
    voice_control_board.py，count 由二分逻辑给定，无外部输入。
    """
    body = []
    for node in nodes[:count]:
        for line in lines[node.lineno - 1:node.end_lineno]:
            body.append(line + "\n")
    (root / (PREFIX_MODULE + ".py")).write_text(
        "# 自动生成: voice_control_board.py 前 %d 条顶层语句(逐字抄写)\n" % count
        + "".join(body), encoding="utf-8")


def classify(output, code):
    """对一次子进程结果分类，见模块 docstring 的定义。"""
    if code == 0 and "[结果] 连接及验证成功" in output:
        return "success"
    if "DataChannelTimeoutError" in output:
        return "poison"
    if "NoSdpAnswerError" in output or "429" in output:
        return "slot"
    if "[结果]" not in output and "Traceback" in output:
        return "script_error"
    return "unknown"


def run_child():
    importlib.import_module(PREFIX_MODULE)
    from connection_compare import child as run_case
    raise SystemExit(run_case("baseline"))


def self_test():
    """本地自检，不连接狗。证明：
    1. 每个前缀模块(0..N)都可通过 ast.parse（可编译）；
    2. 前缀 N 的顶层语句 == 前缀 N-1 + 源文件第 N 条语句（报告与实际新增一致）；
    3. 嫌疑索引命中真实语句行；classify 分类符合定义。
    """
    root = Path(__file__).resolve().parent
    board = root / "voice_control_board.py"
    nodes = statements(board)
    lines = board.read_text(encoding="utf-8").splitlines()
    print("顶层语句数:", len(nodes))
    failures = []

    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        prev_dumps = []
        for count in range(0, len(nodes) + 1):
            tool = sys.modules[__name__]
            write_prefix_module(tmp_root, lines, nodes, count)
            src = (tmp_root / (PREFIX_MODULE + ".py")).read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError as error:
                failures.append("prefix=%d 不可编译: %s" % (count, error))
                break
            dumps = [ast.dump(s, include_attributes=False) for s in tree.body]
            expect = [ast.dump(s, include_attributes=False) for s in nodes[:count]]
            if dumps != expect:
                failures.append("prefix=%d 语句集与源文件前%d条不一致" % (count, count))
                break
            if count > 0:
                if dumps[:-1] != prev_dumps:
                    failures.append("prefix=%d 与 prefix=%d 的公共前缀不一致" % (count, count - 1))
                    break
                if dumps[-1] != expect[-1]:
                    failures.append("prefix=%d 新增语句与源文件第%d条不一致" % (count, count))
                    break
            prev_dumps = dumps
        print("检查1+2 (可编译+增量一致):", "PASS" if not failures else "FAIL")
        write_prefix_module(tmp_root, lines, nodes, 5)
        print("---- 样例: 前5条生成内容 ----")
        print((tmp_root / (PREFIX_MODULE + ".py")).read_text(encoding="utf-8"), end="")
        print("---- 样例结束 ----")

    for target, needle in [("<stdlib>", "import asyncio"), ("numpy", "import numpy"),
                           ("av", "import av"), ("sherpa_onnx", "import sherpa_onnx"),
                           ("aiortc", "aiortc.mediastreams")]:
        idx = find_index(nodes, target)
        if idx is None:
            failures.append("find_index 未找到 %s" % target)
            continue
        line = lines[nodes[idx].lineno - 1]
        if needle not in line:
            failures.append("find_index(%s) 命中 line %d 但不含 %r: %s"
                            % (target, nodes[idx].lineno, needle, line))
        else:
            print("嫌疑 %-12s -> 语句#%d (含它需 prefix=%d) line %d: %s"
                  % (target, idx + 1, idx + 1, nodes[idx].lineno, line.strip()[:60]))

    cases = [
        ("[结果] 连接及验证成功；没有发送动作。", 0, "success"),
        ("[结果] DataChannelTimeoutError Data channel setup timed out after 15s", 1, "poison"),
        ("ERROR:root:Local request failed: 429 Client Error\n[结果] NoSdpAnswerError Robot signaling returned no SDP answer.", 1, "slot"),
        ("Traceback (most recent call last):\n  File ...\nSyntaxError: invalid syntax", 1, "script_error"),
        ("(无法识别的输出)", 124, "unknown"),
    ]
    for output, code, want in cases:
        got = classify(output, code)
        if got != want:
            failures.append("classify -> %s, 期望 %s" % (got, want))
        print("分类检查 code=%d -> %s (期望 %s): %s" % (code, got, want, "PASS" if got == want else "FAIL"))

    if failures:
        print("\n自检失败:")
        for item in failures:
            print(" -", item)
        return 1
    print("\n全部自检通过。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="定位破坏握手的顶层语句；不运行main，不发动作")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true", help="本地自检：前缀可编译/增量一致/索引/分类，不连接狗")
    args = parser.parse_args()
    if args.child:
        run_child()
        return
    if args.self_test:
        raise SystemExit(self_test())

    root = Path(__file__).resolve().parent
    board = root / "voice_control_board.py"
    nodes = statements(board)
    lines = board.read_text(encoding="utf-8").splitlines()
    logs = root / "logs" / ("import-bisect-" + str(time.time_ns()))
    logs.mkdir(parents=True, exist_ok=False)
    mapping = "\n".join("%d. 含它需prefix=%d  %s" % (i + 1, i + 1, describe(n, lines))
                        for i, n in enumerate(nodes))
    (logs / "statements.txt").write_text(mapping, encoding="utf-8")
    print("[日志目录]", logs, flush=True)
    print("共%d条顶层语句；预计15-35分钟。开始前请确认狗已空闲2分钟。" % len(nodes), flush=True)

    records = []
    state = {"first": True}

    def check(count):
        """一次定位测试（含噪声重测）。返回 success / poison / slot / unknown。"""
        attempts = 0
        while True:
            attempts += 1
            if state["first"]:
                state["first"] = False
            else:
                print("[间隔] 等待%d秒..." % GAP_SECONDS, flush=True)
                time.sleep(GAP_SECONDS)
            print("[测试] 执行前%d条顶层语句后连接（第%d次尝试）" % (count, attempts), flush=True)
            write_prefix_module(root, lines, nodes, count)
            try:
                result = subprocess.run(
                    [sys.executable, "-u", str(Path(__file__).resolve()), "--child"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", timeout=120, cwd=str(root))
                output, code = result.stdout, result.returncode
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                output += "\n[测试] 子进程超过120秒。\n"
                code = 124
            cls = classify(output, code)
            (logs / ("%02d-prefix-%d.log" % (len(records) + 1, count))).write_text(
                "[分类] %s\n%s" % (cls, output), encoding="utf-8")
            records.append((count, cls))
            print("[测试结果] prefix=%d -> %s" % (count, cls), flush=True)
            if cls == "slot" and attempts < 3:
                print("[噪声] 判定为会话未释放(429)，不算失败；等待后重测同一前缀。", flush=True)
                continue
            if cls == "unknown" and attempts < 2:
                print("[未知] 结果无法分类；等待后重测一次。", flush=True)
                continue
            return cls

    try:
        if check(0) != "success":
            print("基础连接(prefix=0)未成功。请确认狗已空闲2分钟后重跑。", flush=True)
            return

        last_good = 0
        fail_count = None
        for target in SUSPECTS:
            idx = find_index(nodes, target)
            if idx is None:
                print("[嫌疑扫描] 未找到语句: %s（跳过）" % target, flush=True)
                continue
            count = idx + 1  # 包含该语句需要的前缀数
            cls = check(count)
            print("[嫌疑扫描] %s(prefix=%d): %s" % (target, count, cls), flush=True)
            if cls == "success":
                last_good = max(last_good, count)
            elif cls == "poison":
                fail_count = count
                break
            else:
                print("嫌疑扫描遇到非预期结果(%s)，中止。把日志目录发给AI。" % cls, flush=True)
                return

        if fail_count is None:
            cls = check(len(nodes))
            if cls != "poison":
                print("完整顶层执行结果为 %s（非poison），未复现目标失败，不推断边界。" % cls, flush=True)
                return
            fail_count = len(nodes)

        low, high = last_good, fail_count
        while high - low > 1:
            middle = (low + high) // 2
            cls = check(middle)
            if cls == "success":
                low = middle
            elif cls == "poison":
                high = middle
            else:
                print("二分中遇到非预期结果(%s)，中止。把日志目录发给AI。" % cls, flush=True)
                return

        added = nodes[low:high]
        print("\n[定位] 从成功前缀(%d条)到失败前缀(%d条)新增 %d 条语句:" % (low, high, len(added)), flush=True)
        for node in added:
            print("      " + describe(node, lines), flush=True)

        print("\n[交叉复核] 按 低/高/低/高/基线 顺序复测（各隔90秒）。", flush=True)
        seq = (low, high, low, high, 0)
        cross = [check(c) for c in seq]
        print("[交叉复核结果]", " ".join("prefix=%d:%s" % (c, cls) for c, cls in zip(seq, cross)), flush=True)
        if tuple(cross) == ("success", "poison", "success", "poison", "success"):
            print("=> 边界确认：新增语句 %s 触发失败（与其前面 %d 条语句共同作用）。" % (describe(added[0], lines), low), flush=True)
        else:
            print("=> 交叉复核不干净（非 success/poison 交替），存在时机或状态干扰。把日志目录发给AI。", flush=True)
    finally:
        (logs / "summary.txt").write_text(str(records), encoding="utf-8")
        tmp = root / (PREFIX_MODULE + ".py")
        if tmp.is_file():
            tmp.unlink()
        print("[日志已保存]", logs, flush=True)


if __name__ == "__main__":
    main()
