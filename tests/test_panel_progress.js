/*
 * 面板進度條的測試。執行:node tests/test_panel_progress.js
 *
 * 守住三件事:
 *
 * 1. 面板認得的格式,必須就是 Python 實際印出來的格式。兩邊各寫各的話,
 *    進度條永遠不會動,而那個「壞法」很安靜——訊息區照樣有字在跑,
 *    只是進度條一直是 0%,沒人會馬上發現。所以這裡直接叫 Python 產生
 *    真正的進度行來餵。
 * 2. 進度行不可以堆進訊息區。長片的轉錄會印出上百行進度,全部堆進去
 *    會把真正要看的訊息淹掉。
 * 3. 一般訊息不可以被誤判成進度行(不然就會從訊息區消失)。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
let passed = 0;
function ok(cond, msg) {
  if (!cond) { console.error("  ✗ " + msg); process.exit(1); }
  console.log("  ✓ " + msg);
  passed++;
}

const src = fs.readFileSync(
  path.join(ROOT, "premiere-panel", "js", "main.js"), "utf8");

console.log("執行面板進度條測試...");

// ---- 假的 DOM,只夠讓 appendLog / showProgress 跑起來 ----
function makeEl() {
  return {
    textContent: "", style: { width: "", display: "" }, scrollTop: 0,
    scrollHeight: 0, children: [],
    classList: { toggle: function () {}, remove: function () {} },
    appendChild: function (c) { this.children.push(c); }
  };
}
const els = {};
["log", "progWrap", "progStage", "progPct", "progFill"].forEach(function (id) {
  els[id] = makeEl();
});

const sandbox = {
  $: function (id) { return els[id] || makeEl(); },
  document: {
    createElement: function () { return makeEl(); },
    createTextNode: function (t) { return { text: t }; }
  },
  logBuf: ""
};
vm.createContext(sandbox);

// 把 log/進度那一段挖出來執行
const from = src.indexOf("var LOG_ERR_RE");
const to = src.indexOf("function toFwd(");
vm.runInContext(src.slice(from, to), sandbox);

// ---- 1. 拿 Python 真的印出來的進度行來餵 ----
{
  const script = [
    "import sys",
    'sys.path.insert(0, r"' + ROOT.replace(/\\/g, "\\\\") + '")',
    "from modules.progress import Reporter",
    "import time",
    "r = Reporter('語音轉錄', 1620.0, unit='分', scale=1/60)",
    "for s in (0, 400, 800, 1200):",
    "    r._last_at = 0",      // 繞過節流,測試不要等
    "    r.update(s)",
    "r.done()"
  ].join("\n");
  const out = execFileSync("python", ["-X", "utf8", "-c", script],
    { encoding: "utf8", cwd: ROOT });
  const lines = out.trim().split(/\r?\n/);
  ok(lines.length >= 4, "Python 產出了 " + lines.length + " 行進度");

  const before = els.log.children.length;
  sandbox.appendLog(out);

  ok(els.log.children.length === before,
    "進度行沒有被堆進訊息區(長片會有上百行)");
  ok(els.progWrap.style.display === "block", "進度條有出現");
  ok(els.progPct.textContent === "100%",
    "最後停在 100%(實際:" + els.progPct.textContent + ")");
  ok(els.progStage.textContent.indexOf("語音轉錄") === 0,
    "顯示步驟名稱:" + els.progStage.textContent);
  ok(/27\.0 分/.test(els.progStage.textContent),
    "顯示已完成/總長:" + els.progStage.textContent);
  ok(els.progFill.style.width === "100%",
    "進度條長度對得上(" + els.progFill.style.width + ")");
}

// ---- 2. 中途的百分比要真的反映出來 ----
{
  sandbox.appendLog("  [進度] 混回影片 37% 12.5/34.0 分\n");
  ok(els.progPct.textContent === "37%", "中途百分比正確");
  ok(els.progFill.style.width === "37%", "進度條長度跟著中途百分比");
}

// ---- 3. 一般訊息不可以被吃掉 ----
{
  const before = els.log.children.length;
  sandbox.appendLog("[3/5] 決策引擎\n  1803 段:刪除 1009、快轉 107\n");
  ok(els.log.children.length > before, "一般訊息照常進訊息區");
  // 含有百分比、但不是進度行的訊息也不能被吃掉
  const b2 = els.log.children.length;
  sandbox.appendLog("  省下的時間 47%\n");
  ok(els.log.children.length > b2, "含百分比的一般訊息不會被誤判成進度");
}

// ---- 4. 收尾要把進度條收掉 ----
{
  sandbox.hideProgress();
  ok(els.progWrap.style.display === "none",
    "行程結束後進度條收掉(不然會像卡在半路)");
  ok(/track\(proc\)[\s\S]{0,400}hideProgress\(\)/.test(src),
    "track() 的 close 會呼叫 hideProgress");
}

console.log("\n全部通過 ✓  進度條與 Python 的輸出格式一致(共 " + passed + " 項)。");
