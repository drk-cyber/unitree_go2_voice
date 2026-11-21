"use strict";

const ui = Object.fromEntries([
  "connection", "status", "mode", "source-title", "source-note", "hint-title", "hint-text",
  "replay", "empty", "empty-title", "empty-text", "messages", "detail", "count",
].map(id => [id, document.getElementById(id)]));
const viewport = ui.messages.parentElement;
const sample = [
  ["person", "小狗，请回收医疗废物。", "09:41:02"],
  ["robot", "好的，我去执行任务。", "09:41:03"],
].map(([role, text, time], index) => ({ id: index + 1, role, text, time }));
const statusLabels = {
  demo: "演示模式",
  connecting: "正在连接",
  connected: "日志已接入",
  offline: "连接中断",
};

function setStatus(status, detail) {
  ui.connection.dataset.state = status;
  ui.status.textContent = statusLabels[status];
  ui.detail.textContent = detail;
  const waiting = status === "connecting" || status === "offline";
  ui["empty-title"].textContent = status === "offline" ? "语音日志暂不可用" : "等待对话";
  ui["empty-text"].textContent = waiting ? detail : "对着狗身上的麦克风说“小狗，打招呼”。";
}

function addMessage(message) {
  const li = document.createElement("li");
  const person = message.role === "person";
  li.className = `message ${person ? "person" : "robot"}`;
  li.dataset.id = String(message.id);

  const badge = document.createElement("span");
  badge.className = "badge";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = person ? "人" : "G2";
  const body = document.createElement("div");
  const meta = document.createElement("p");
  meta.className = "message-meta";
  const name = document.createElement("strong");
  name.textContent = person ? "现场人员" : "机器狗";
  const time = document.createElement("time");
  time.textContent = message.time;
  const text = document.createElement("p");
  text.className = "message-text";
  text.textContent = message.text;

  meta.append(name, time);
  body.append(meta, text);
  li.append(badge, body);
  ui.messages.append(li);
}

function showMessages(messages) {
  const nearLatest = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80;
  const ids = new Set(messages.map(message => String(message.id)));
  for (const item of Array.from(ui.messages.children)) {
    if (!ids.has(item.dataset.id)) item.remove();
  }
  const displayed = new Set(Array.from(ui.messages.children, item => item.dataset.id));
  for (const message of messages) {
    if (!displayed.has(String(message.id))) addMessage(message);
  }
  ui.empty.hidden = messages.length > 0;
  ui.count.textContent = `${messages.length} 条消息`;
  if (nearLatest) viewport.scrollTop = viewport.scrollHeight;
}

let demoTimer;
function playDemo() {
  clearTimeout(demoTimer);
  ui.messages.replaceChildren();
  let shown = 1;
  function step() {
    showMessages(sample.slice(0, shown));
    shown += 1;
    if (shown <= sample.length) demoTimer = setTimeout(step, 480);
  }
  step();
}

function showDemo() {
  ui.mode.textContent = "场景演示";
  ui["source-title"].textContent = "回收交接";
  ui["source-note"].textContent = "通过模拟对话预览人和机器狗的交接过程。";
  ui["hint-title"].textContent = "演示内容";
  ui["hint-text"].textContent = "以下为模拟台词。接入实机后，页面显示狗上已有语音程序的实际识别和播报。";
  setStatus("demo", "模拟数据 · 医疗废物回收交接");
  ui.replay.hidden = false;
  ui.replay.addEventListener("click", playDemo);
  showMessages(sample);
}

function showLive(state) {
  ui.mode.textContent = "实机对话";
  setStatus(state.status, state.detail);
  showMessages(state.messages);
}

let liveStarted = false;
async function refresh() {
  try {
    const response = await fetch("/api/session", { cache: "no-store", signal: AbortSignal.timeout(3500) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    if (state.mode === "demo") {
      showDemo();
      return;
    }
    liveStarted = true;
    showLive(state);
  } catch (error) {
    setStatus("offline", "网页程序暂时不可用。请检查电脑上的运行窗口。");
    if (!liveStarted) ui.mode.textContent = "等待连接";
  }
  setTimeout(refresh, 900);
}

refresh();
