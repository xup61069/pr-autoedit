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

// ---- 1. 拿 Python 真的印出來的進度行來餵,並驗證「一路往前、不回頭」 ----
//
// 這是這次修的核心:一次處理有好幾個小步驟(抽音軌 → 轉錄 → 混音),
// 以前每個都自己 0→100%,共用一條進度條就會一直填滿又清空,像壞掉。
// 改成登記整條執行後,回報的是「整條走到哪」,百分比只能往上、不能歸零。
{
  const script = [
    "import sys",
    'sys.path.insert(0, r"' + ROOT.replace(/\\/g, "\\\\") + '")',
    "from modules.progress import Reporter, begin_run, finish_run",
    // 登記這次會依序跑的三個步驟(名稱要跟 Reporter 的 stage 一致)
    "begin_run(['抽出音軌', '語音轉錄', '混回影片'])",
    "def run(stage, total, points):",
    "    r = Reporter(stage, total, unit='分', scale=1/60)",
    "    for s in points:",
    "        r._last_at = 0",      // 繞過節流,測試不要等
    "        r.update(s)",
    "    r.done()",
    "run('抽出音軌', 60.0, (0, 30, 60))",
    "run('語音轉錄', 1620.0, (0, 400, 800, 1620))",
    "run('混回影片', 200.0, (0, 100, 200))",
    "finish_run()"
  ].join("\n");
  const out = execFileSync("python", ["-X", "utf8", "-c", script],
    { encoding: "utf8", cwd: ROOT });
  const lines = out.trim().split(/\r?\n/);
  ok(lines.length >= 6, "Python 產出了 " + lines.length + " 行進度");

  // 把每一行的百分比挖出來,確認「只增不減」——這就是這次要修的行為。
  const PCT = /\[進度\]\s+.+?\s+(\d+)%/;
  const pcts = lines.map(function (l) {
    const m = PCT.exec(l); return m ? parseInt(m[1], 10) : null;
  }).filter(function (v) { return v !== null; });
  ok(pcts.length >= 6, "解析出 " + pcts.length + " 個百分比");
  let monotonic = true;
  for (let k = 1; k < pcts.length; k++) {
    if (pcts[k] < pcts[k - 1]) monotonic = false;
  }
  ok(monotonic, "整條進度只增不減、不會歸零(實際:" + pcts.join(" ") + ")");
  ok(pcts[0] < 50 && pcts[pcts.length - 1] === 100,
    "從低點一路走到 100%(頭 " + pcts[0] + "% 尾 " + pcts[pcts.length - 1] + "%)");
  // 跨步驟不歸零:抽音軌做完(它只佔前面一小段)之後,轉錄的百分比不能
  // 掉回抽音軌的起點——這正是舊版「填滿又清空」的破法。
  ok(Math.max.apply(null, pcts.slice(0, 3)) <= pcts[3],
    "換到下一步時百分比接著往上,不是打回原點");

  const before = els.log.children.length;
  sandbox.appendLog(out);

  ok(els.log.children.length === before,
    "進度行沒有被堆進訊息區(長片會有上百行)");
  ok(els.progWrap.style.display === "block", "進度條有出現");
  ok(els.progPct.textContent === "100%",
    "最後停在 100%(實際:" + els.progPct.textContent + ")");
  ok(els.progFill.style.width === "100%",
    "進度條長度對得上(" + els.progFill.style.width + ")");
}

// ---- 2. 中途的百分比、以及「剩約 X 分」都要顯示出來 ----
{
  sandbox.appendLog("  [進度] 語音轉錄 37% 剩約 3.2 分\n");
  ok(els.progPct.textContent === "37%", "中途百分比正確");
  ok(els.progFill.style.width === "37%", "進度條長度跟著中途百分比");
  ok(/剩約 3\.2 分/.test(els.progStage.textContent),
    "「剩約」時間預估有顯示在步驟旁邊(實際:" + els.progStage.textContent + ")");
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
