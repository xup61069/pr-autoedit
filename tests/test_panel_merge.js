/*
 * 「多檔合併」的面板邏輯測試。執行:node tests/test_panel_merge.js
 *
 * 守住三件事,三件都是「錯了要等到匯進 Premiere 才發現」的那種:
 *
 * 1. 面板算出來的 output 資料夾名稱,必須跟 Python 的
 *    sources.VideoSource.name 一模一樣。對不上的話,面板會說「找不到報告」,
 *    而 Python 其實好好地產出來了,只是放在另一個名字的資料夾裡。
 * 2. 檔案排序要跟 Python 的 natural_key 一致 —— 面板上顯示的順序就是實際
 *    接合的順序,不然你在畫面上確認過的東西是假的。
 * 3. 重算剪輯要把「整份清單」再傳一次。只傳第一個檔的話,重算出來的是
 *    「只有第一段」的短片,但按鈕上寫的是「用新設定重算」,
 *    你會以為是設定改壞了。
 *
 * 比對的對象刻意是「Python 實際算出來的值」而不是這裡自己抄一份預期值:
 * 抄一份的話,哪天 Python 那邊改了命名規則,這個測試還是綠的,
 * 而面板已經找不到資料夾了。
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

// 叫 Python 時一律加 -X utf8。Windows 的命令列預設是 cp950,輸出被管線
// 接走時中文會變成亂碼,比對就永遠不會相等——而且看起來像是邏輯錯了,
// 會往完全錯誤的方向查。(跟 pipeline.py 開頭要 reconfigure(utf-8) 同一個坑。)
function py(script, arg) {
  const out = execFileSync("python", ["-X", "utf8", "-c", script, arg],
    { encoding: "utf8", cwd: ROOT });
  const lines = out.trim().split(/\r?\n/);
  return JSON.parse(lines[lines.length - 1]);
}

const PY_ROOT = ROOT.replace(/\\/g, "\\\\");

const src = fs.readFileSync(
  path.join(ROOT, "premiere-panel", "js", "main.js"), "utf8");

function extract(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("main.js 裡找不到 " + name);
  let depth = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") {
      depth--;
      if (started && depth === 0) return src.slice(i, j + 1);
    }
  }
  throw new Error(name + " 括號不成對");
}

console.log("執行多檔合併的面板邏輯測試...");

// --- 1. 資料夾名稱要跟 Python 算出一樣的 ---
{
  const sandbox = { path: path };
  vm.createContext(sandbox);
  vm.runInContext(extract("outputNameOf"), sandbox);

  const cases = [
    [["C:\\v\\教學_0718.mp4"], "教學_0718"],
    [["C:\\v\\教學_0718.mp4", "C:\\v\\教學_0718_0001.mp4"], "教學_0718_合併2支"],
    [["/x/a.mov", "/x/b.mov", "/x/c.mov"], "a_合併3支"]
  ];

  const script = [
    "import sys, json",
    'sys.path.insert(0, r"' + PY_ROOT + '")',
    "from modules.sources import VideoSource",
    "groups = json.loads(sys.argv[1])",
    "print(json.dumps([VideoSource(g).name for g in groups], ensure_ascii=False))"
  ].join("\n");

  const pyNames = py(script, JSON.stringify(cases.map(function (c) { return c[0]; })));

  cases.forEach(function (c, i) {
    const jsName = sandbox.outputNameOf(c[0]);
    ok(jsName === c[1], "面板算出「" + jsName + "」");
    ok(jsName === pyNames[i], "跟 Python 算的一致(" + pyNames[i] + ")");
  });
}

// --- 2. 排序要跟 Python 的 natural_key 一致 ---
{
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(extract("naturalKey") + "\n" + extract("sortNatural"), sandbox);

  const messy = ["/v/part10.mp4", "/v/part2.mp4", "/v/part1.mp4",
                 "/v/rec_0002.mp4", "/v/rec_0001.mp4"];
  const jsSorted = sandbox.sortNatural(messy);

  const script = [
    "import sys, json",
    'sys.path.insert(0, r"' + PY_ROOT + '")',
    "from modules.sources import natural_key",
    "print(json.dumps(sorted(json.loads(sys.argv[1]), key=natural_key)))"
  ].join("\n");

  const pySorted = py(script, JSON.stringify(messy));

  ok(JSON.stringify(jsSorted) === JSON.stringify(pySorted),
    "面板與 Python 排出同樣的順序");
  ok(jsSorted[0].indexOf("part1.mp4") >= 0 && jsSorted[1].indexOf("part2.mp4") >= 0,
    "part2 排在 part10 前面(不是字串排序)");
}

// --- 3. 原始碼層面:整份清單要傳下去 ---
{
  const runBlock = src.slice(src.indexOf("function runPipeline"),
    src.indexOf("function outputNameOf"));
  ok(/\["-u", "pipeline\.py"\]\.concat\(videos\)/.test(runBlock),
    "一鍵剪輯把全部的檔都傳給 pipeline.py");
  ok(/var name = outputNameOf\(videos\)/.test(runBlock),
    "一鍵剪輯用 outputNameOf 算資料夾(不是只取第一個檔的檔名)");
  ok(runBlock.indexOf("rememberVideo(videos)") >= 0,
    "記住的是整份清單,不是第一個檔");

  const rebuild = src.slice(src.indexOf('$("rebuild")'));
  ok(/\["-u", "pipeline\.py"\]\s*\.concat\(lastVideos\)/.test(rebuild),
    "重算剪輯把整份清單原封不動再傳一次");
  ok(rebuild.indexOf("outDirOf(lastVideos)") >= 0,
    "重算剪輯找的是合併後的那個資料夾");

  ok(!/\blastVideo\b/.test(src),
    "沒有殘留的單一影片變數(避免兩套狀態互相打架)");
  ok(/showOpenDialog\(true,/.test(src),
    "檔案選取視窗允許多選");
}

console.log("\n全部通過 ✓  多檔合併的面板邏輯正確(共 " + passed + " 項)。");
