/*
 * 面板「錯誤翻譯表」的測試。執行:node tests/test_panel_errors.js
 *
 * 守的是一件事:**不准給錯的答案**。
 *
 * 實際發生過:混音那一步壞掉(TypeError),面板卻回答「降噪外掛載入失敗,
 * 去檢查 VST 路徑」。原因是它拿「整份 log」去比對,而第一步印過一行
 * 「載入 1 個 VST 外掛並處理...」——那是成功的訊息,卻剛好命中 VST 規則。
 * 使用者於是跑去改一個根本沒壞的設定。
 *
 * 給錯答案比不給答案更糟:不給答案他會把訊息貼出來問,給錯答案他會照做,
 * 然後在錯的方向上耗很久。所以規則寧可放過,不可誤判。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
let passed = 0;
function ok(cond, msg) {
  if (!cond) { console.error("  ✗ " + msg); process.exit(1); }
  console.log("  ✓ " + msg);
  passed++;
}

const src = fs.readFileSync(
  path.join(ROOT, "premiere-panel", "js", "main.js"), "utf8");

// 把 ERROR_TABLE + errorTail + explainError 這一整段挖出來跑
const from = src.indexOf("var ERROR_TABLE");
const to = src.indexOf("// 失敗時在 log 尾巴補上白話說明");
if (from < 0 || to < 0) throw new Error("main.js 裡找不到錯誤翻譯表");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(src.slice(from, to), sandbox);
const explain = sandbox.explainError;

console.log("執行面板錯誤翻譯測試...");

// 這一段是「跑成功」的訊息,裡面含著好幾個規則會找的關鍵字
const NOISY_PREFIX = [
  "▶ 已啟動,正在載入程式與模型…",
  "=== 處理 making(fps=60.0)===",
  "[1/5] 音訊清理",
  "  載入 1 個 VST 外掛並處理...",          // 含 "VST"
  "  響度標準化到 -14.0 LUFS...",
  "[2/5] 語音轉錄",
  "  載入 Whisper 模型 large-v3...",
  "  轉錄完成:3847 個詞",
  "  分析畫面活動…",
  "[3/5] 決策引擎",
  "  auto-editor 產生 Premiere XML...",     // 含 "auto-editor"
  "  混回影片...",
  ""
].join("\n");

// --- 1. 真正的錯誤在尾巴時,不可以被前面的正常訊息帶偏 ---
{
  const log = NOISY_PREFIX + [
    "Traceback (most recent call last):",
    '  File "C:\\pr-autoedit\\pipeline.py", line 344, in main',
    "TypeError: expected str, bytes or os.PathLike object, not VideoSource"
  ].join("\n");
  const got = explain(log);
  ok(got === null || got.indexOf("降噪外掛") < 0,
    "混音壞掉時不會誤答「降噪外掛載入失敗」(前面那行 VST 是成功訊息)");
  ok(got === null || got.indexOf("auto-editor") < 0,
    "也不會誤答 auto-editor(那行同樣是成功訊息)");
}

// --- 2. 該認得的還是要認得(修完不能變成什麼都認不出來) ---
{
  const cases = [
    ["CUDA out of memory", "顯示卡記憶體不足", "顯示卡記憶體不足"],
    ["ModuleNotFoundError: No module named 'lxml'", "缺少套件", "缺少套件:lxml"],
    ["[WinError 5] Access is denied", "檔案被鎖住", "檔案被 Premiere 鎖住"],
    ["RuntimeError: 找不到 ffmpeg,無法分析畫面", "找不到 ffmpeg", "找不到 ffmpeg"],
    ["Unable to load plugin: scan failure", "降噪外掛", "VST 載入真的失敗"],
  ];
  cases.forEach(function (c) {
    const got = explain(NOISY_PREFIX + "Traceback (most recent call last):\n" + c[0]);
    ok(got !== null && got.indexOf(c[1]) >= 0, "還認得:" + c[2]);
  });
  // 缺套件那條要把套件名字帶出來
  const missing = explain(NOISY_PREFIX + "Traceback (most recent call last):\n" +
    "ModuleNotFoundError: No module named 'lxml'");
  ok(/pip install lxml/.test(missing), "缺套件時給出正確的套件名稱");
}

// --- 3. 沒有 traceback 時(sys.exit 印一段話就結束)也要抓得到尾巴 ---
{
  const log = NOISY_PREFIX + [
    "這幾個檔的規格不一樣,沒辦法直接接在一起:",
    "    a.mp4:3840x2160、30fps、hevc",
    "    b.mp4:1920x1080、60fps、h264"
  ].join("\n");
  const got = explain(log);
  ok(got === null || got.indexOf("降噪外掛") < 0,
    "沒有 traceback 時也不會被前面的 VST 字樣帶偏");
}

// --- 4. 認不出來就老實回 null,不要硬湊 ---
{
  const got = explain(NOISY_PREFIX +
    "Traceback (most recent call last):\nSomeWeirdError: 前所未見的狀況");
  ok(got === null, "認不出來的錯誤老實回 null(讓面板顯示原始訊息)");
}

console.log("\n全部通過 ✓  錯誤翻譯不會給錯答案(共 " + passed + " 項)。");
