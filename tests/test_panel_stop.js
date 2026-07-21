/*
 * 「停止」鈕的邏輯測試。執行:node tests/test_panel_stop.js
 *
 * 守住兩件事,兩件都是「看起來有動、其實沒動」的那種問題:
 *
 * 1. 停止必須收掉整棵行程樹。Windows 上 proc.kill() 只結束直接的子行程
 *    (python.exe),Python 底下開的 ffmpeg 會變孤兒繼續跑——繼續寫輸出檔、
 *    繼續佔檔案鎖。使用者看到的是「按了停止卻沒停」,下次重跑還會因為
 *    檔案被鎖住而失敗。
 * 2. 使用者按停止,不能被當成「處理失敗」。跳錯誤說明、嗶錯誤音會讓人
 *    以為程式壞了。
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

console.log("執行「停止」邏輯測試...");

// --- 1. killTree 必須用 taskkill /T /F 收整棵樹 ---
{
  const calls = [];
  const sandbox = {
    cp: {
      spawn: function (cmd, args) {
        calls.push({ cmd: cmd, args: args });
        return { on: function (ev, cb) { if (ev === "close") setTimeout(cb, 0); } };
      }
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(extract("killTree"), sandbox);

  let doneCalled = false;
  sandbox.killTree({ pid: 4242 }, function () { doneCalled = true; });

  ok(calls.length === 1 && calls[0].cmd === "taskkill",
    "用 taskkill 而不是 proc.kill()");
  const a = calls[0].args;
  ok(a.indexOf("/T") >= 0, "帶 /T:連子孫行程一起收(ffmpeg 才不會變孤兒)");
  ok(a.indexOf("/F") >= 0, "帶 /F:強制結束");
  ok(a.indexOf("4242") >= 0, "殺的是正在跑的那個 pid");

  setTimeout(function () {
    ok(doneCalled, "收完之後有回呼(按鈕才解得開)");
    stage2();
  }, 10);
}

// --- 2. 沒有 taskkill 時要退回 proc.kill(),不能整個當掉 ---
function stage2() {
  const sandbox = {
    cp: {
      spawn: function () {
        return {
          on: function (ev, cb) {
            if (ev === "error") setTimeout(function () { cb(new Error("ENOENT")); }, 0);
          }
        };
      }
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(extract("killTree"), sandbox);

  let killed = false, doneCalled = false;
  sandbox.killTree({ pid: 7, kill: function () { killed = true; } },
    function () { doneCalled = true; });

  setTimeout(function () {
    ok(killed, "taskkill 叫不動時退回 proc.kill()");
    ok(doneCalled, "退路走完也有回呼");
    stage3();
  }, 20);
}

// --- 3. 空的 proc 不能爆掉(面板剛開、什麼都沒跑的時候) ---
function stage3() {
  const sandbox = { cp: { spawn: function () { throw new Error("不該被呼叫"); } } };
  vm.createContext(sandbox);
  vm.runInContext(extract("killTree"), sandbox);
  let doneCalled = false;
  sandbox.killTree(null, function () { doneCalled = true; });
  ok(doneCalled, "沒有正在跑的行程時,安靜地直接回呼");
  stage4();
}

// --- 4. 原始碼層面:停止與失敗必須分開處理 ---
function stage4() {
  ok(/if \(stopping\)/.test(src), "close 時有分辨「使用者按了停止」");
  // 主流程的 close:stopping 的分支要排在 code !== 0 前面,
  // 否則被停掉的行程回傳非 0,照樣會被當成失敗跳錯誤說明
  const body = src.slice(src.indexOf("function runPipeline"));
  const iStop = body.indexOf("if (stopping)");
  const iFail = body.indexOf("if (code !== 0)");
  ok(iStop >= 0 && iFail >= 0 && iStop < iFail,
    "「已停止」判斷排在「失敗」判斷前面(被殺掉的行程離開碼也不是 0)");
  // 只看 stopping 那個區塊「自己」的內容。不能用「往後掃 N 個字元」——
  // 失敗分支就緊接在後面,那樣掃到的是失敗分支的 beep,會誤報。
  function blockAfter(text, marker) {
    const i = text.indexOf(marker);
    const start = text.indexOf("{", i);
    let depth = 0;
    for (let j = start; j < text.length; j++) {
      if (text[j] === "{") depth++;
      else if (text[j] === "}") { depth--; if (depth === 0) return text.slice(start, j + 1); }
    }
    return "";
  }
  const stopBlock = blockAfter(body, "if (stopping)");
  ok(stopBlock.indexOf("beep(false)") < 0, "停止不會嗶錯誤音");
  ok(stopBlock.indexOf("explainInto()") < 0, "停止不會跳錯誤說明");
  ok(stopBlock.indexOf("return") >= 0, "停止分支會 return,不會掉進失敗分支");

  console.log("\n全部通過 ✓  停止邏輯正確(共 " + passed + " 項)。");
}
