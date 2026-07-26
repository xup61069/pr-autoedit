/*
 * 序列版面(layout JSON)的測試。執行:node tests/test_panel_layout.js
 *
 * host.jsx 把「目前序列」寫成一份 JSON 交給 Python,兩個字幕功能都靠它。
 * 這裡守住兩件「壞掉的時候很安靜」的事:
 *
 * 1. **Windows 路徑的跳脫**。路徑滿是反斜線(C:\影片\a.mp4),直接塞進 JSON
 *    會被當成跳脫字元 —— `\b` 會變成倒退鍵、`\影` 會讓整份 JSON 解析失敗。
 *    使用者看到的只會是「字幕產生失敗」,完全看不出是路徑造成的。
 * 2. **幀率換算**。序列可能不是本工具產的,沒有 03_timeline.json 可以問幀率,
 *    只能從 Premiere 的 timebase 換算。換錯的話字幕時間會整體拉伸或壓縮,
 *    而且愈到後面差愈多 —— 開頭幾句還對得上,所以很容易誤以為只是「有點跑掉」。
 *
 * 不必開 Premiere:用假的 app / File 物件,跑的是 host.jsx 裡真正那份程式碼。
 */
const fs = require("fs");
const path = require("path");
const os = require("os");

const ROOT = path.join(__dirname, "..");
let passed = 0;
function ok(cond, msg) {
  if (!cond) { console.error("  ✗ " + msg); process.exit(1); }
  console.log("  ✓ " + msg);
  passed++;
}

const src = fs.readFileSync(
  path.join(ROOT, "premiere-panel", "jsx", "host.jsx"), "utf8");

// 從 host.jsx 挖出某個函式(大括號配對)
function grab(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("host.jsx 裡找不到 " + name);
  let d = 0, started = false, j = i;
  for (; j < src.length; j++) {
    const c = src[j];
    if (c === "{") { d++; started = true; }
    else if (c === "}") { d--; if (started && d === 0) { j++; break; } }
  }
  return src.slice(i, j);
}

console.log("執行序列版面測試...");

// 反斜線一律用 fromCharCode 組,避免這個測試檔自己的轉義出錯
// (真的踩過:寫在字串裡的 \\ 經過多層轉義後就不是反斜線了,
//  結果測試「證明」了一個根本沒發生的 bug。)
const BS = String.fromCharCode(92);

let written = null;
global.File = function () {
  this.encoding = "";
  this.open = function () { return true; };
  this.write = function (t) { written = t; };
  this.close = function () {};
};

eval(grab("prMediaPathOf"));
eval(grab("prJsonStr"));
eval(grab("prDumpSequenceLayout"));

// ---- 1. 路徑跳脫:原樣進、原樣出 ----
{
  const cases = [
    ["一般 Windows 路徑", "C:" + BS + "pr-autoedit" + BS + "a.mp4"],
    ["中文與空格", "C:" + BS + "影片" + BS + "教學 01.mp4"],
    // \b \n \t 這幾個是最容易被 JSON 誤讀成控制字元的開頭
    ["路徑裡的 \\b", "D:" + BS + "b.mp4"],
    ["路徑裡的 \\n", "D:" + BS + "new" + BS + "t.mp4"],
    ["帶引號的檔名", 'E:' + BS + '我的 "教學" 片.mp4'],
  ];
  cases.forEach(function (c) {
    const out = prJsonStr(c[1]);
    let back = null;
    try { back = JSON.parse(out); } catch (e) { back = "(JSON 解析失敗:" + e.message + ")"; }
    ok(back === c[1], c[0] + " 原樣還原");
  });
}

// ---- 2. 整份 layout:解析得動,而且欄位都對 ----
{
  const P1 = "C:" + BS + "影片" + BS + "教學 01.mp4";
  const P2 = "D:" + BS + "b.mp4";
  const secs = function (v) { return { seconds: v }; };
  const clips = [
    { start: secs(0), end: secs(1), inPoint: secs(5), outPoint: secs(6),
      getSpeed: function () { return 1; },
      projectItem: { getMediaPath: function () { return P1; } } },
    { start: secs(1), end: secs(2), inPoint: secs(0), outPoint: secs(1),
      getSpeed: function () { return 6; },
      projectItem: { getMediaPath: function () { return P2; } } },
  ];
  global.app = { project: { activeSequence: {
    videoTracks: { numTracks: 1,
      0: { clips: { numItems: 2, 0: clips[0], 1: clips[1] } } },
    timebase: "8467200000",       // 30fps(Premiere 的 tick 數 / 幀)
  } } };

  const r = prDumpSequenceLayout(path.join(os.tmpdir(), "layout_test.json"));
  ok(r.indexOf("OK") === 0, "回報成功:" + r);

  let j = null;
  try { j = JSON.parse(written); } catch (e) {
    ok(false, "整份 layout 要解析得動,實際:" + e.message);
  }
  ok(j.clips.length === 2, "兩個片段都在");
  ok(j.clips[0].path === P1 && j.clips[1].path === P2,
    "來源檔路徑原樣帶出(重新辨識要拿它去讀聲音)");
  ok(j.fps === 30, "幀率換算正確(實際:" + j.fps + ")");
  ok(j.clips[1].speed === 6 && j.clips[1].start === 1 && j.clips[1].out === 1,
    "時間與速度欄位正確");
}

// ---- 3. 各種幀率都要換算得對 ----
{
  // Premiere 的 timebase 是「每一幀幾個 tick」,一秒固定 254016000000 個 tick
  const cases = [
    ["8467200000", 30, "30fps"],
    ["8475667200", 29.97, "29.97(NTSC)"],
    ["4233600000", 60, "60fps"],
    ["10584000000", 24, "24fps"],
  ];
  cases.forEach(function (c) {
    global.app.project.activeSequence.timebase = c[0];
    prDumpSequenceLayout(path.join(os.tmpdir(), "layout_test.json"));
    const got = JSON.parse(written).fps;
    ok(Math.abs(got - c[1]) < 0.01, c[2] + " 換算正確(得到 " + got.toFixed(3) + ")");
  });
}

// ---- 4. 拿不到 timebase 時要回 0,讓 Python 自己用退路,不能寫出 NaN ----
{
  delete global.app.project.activeSequence.timebase;
  prDumpSequenceLayout(path.join(os.tmpdir(), "layout_test.json"));
  const j = JSON.parse(written);      // NaN 會讓 JSON 解析失敗
  ok(j.fps === 0, "拿不到幀率時寫 0(不是 NaN,那會讓整份 JSON 壞掉)");
}

console.log("\n全部通過 ✓  序列版面的路徑與幀率都帶得對(共 " + passed + " 項)。");
