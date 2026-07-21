/*
 * 教學類型編輯器的邏輯測試。執行:node tests/test_panel_vocab.js
 *
 * 重點在「面板算出來的額度」必須跟 Python 算出來的一致。
 * 不一致的下場最難查:面板說「還剩 20 額度」,你放心加了詞,
 * 結果 Whisper 那邊早就超標、把詞默默砍掉,而且不會有任何錯誤訊息
 * ——你只會覺得「明明加了術語卻沒效果」。
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

console.log("執行教學類型編輯器測試...");

// ---- 從 Python 取得真正的預算參數與對照答案 ----
const py = `
import sys, json
sys.path.insert(0, r"${ROOT.replace(/\\/g, "\\\\")}")
import ui_settings
from modules.transcribe import _est_tokens
samples = ["剪輯", "Premiere Pro", "UV", "拓樸、法線、頂點",
           "After Effects、軌道遮罩", ""]
print(json.dumps({"budget": ui_settings._vocab_budget(),
                  "samples": {s: _est_tokens(s) for s in samples}},
                 ensure_ascii=False))
`;
const out = execFileSync("python", ["-c", py], { cwd: ROOT, encoding: "utf8" });
const ref = JSON.parse(out.trim().split(/\r?\n/).pop());

// ---- 把 main.js 的 estTokens / parseWords 拿出來跑 ----
// main.js 是一整包 IIFE、又依賴 CEP 的環境,沒辦法整份 require;
// 這裡把那兩個純函式抽出來在沙盒裡執行,測的仍是「檔案裡真正那份程式碼」。
const src = fs.readFileSync(path.join(ROOT, "premiere-panel", "js", "main.js"), "utf8");
function extract(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("main.js 裡找不到 " + name);
  let depth = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
  }
  throw new Error(name + " 括號不成對");
}

const sandbox = { settingsData: { vocab_budget: ref.budget } };
vm.createContext(sandbox);
vm.runInContext(extract("estTokens") + "\n" + extract("parseWords"), sandbox);

// --- 1. 面板與 Python 的估算完全一致 ---
Object.keys(ref.samples).forEach(function (s) {
  const mine = sandbox.estTokens(s);
  ok(mine === ref.samples[s],
    "「" + (s || "(空字串)") + "」面板算 " + mine + "、Python 算 " + ref.samples[s]);
});

// --- 2. 詞的分隔:換行、全形/半形逗號、頓號都要能拆 ---
ok(JSON.stringify(sandbox.parseWords("剪輯、調色\n幀率,LUT,Proxy"))
  === JSON.stringify(["剪輯", "調色", "幀率", "LUT", "Proxy"]),
  "換行 / 頓號 / 全形逗號 / 半形逗號都拆得開");
ok(JSON.stringify(sandbox.parseWords("  剪輯  、、 \n\n 調色 "))
  === JSON.stringify(["剪輯", "調色"]),
  "多餘的空白與連續分隔符號會被清掉(不會產生空詞)");
ok(sandbox.parseWords("").length === 0 && sandbox.parseWords("、,\n").length === 0,
  "空白內容拆出來是空清單");

// --- 3. 預算數字合理 ---
const b = ref.budget;
ok(b.total > 0 && b.demo > 0 && b.wrapper > 0, "預算參數齊全(上限/示範句/框架)");
ok(b.demo + b.wrapper < b.total, "固定開銷小於總額度,還有空間放詞");

// --- 4. 額度算式:固定開銷 + 詞的長度 ---
const words = ["拓撲", "法線", "頂點"];
const used = b.demo + b.wrapper + sandbox.estTokens(words.join("、"));
ok(used > b.demo + b.wrapper && used < b.total,
  "三個詞的用量介於「只有固定開銷」與「總上限」之間(" + used + "/" + b.total + ")");

console.log("\n全部通過 ✓  教學類型編輯器邏輯正確(共 " + passed + " 項)。");
