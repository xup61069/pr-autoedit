/*
 * 面板「掛人聲處理」的邏輯測試(不需要開 Premiere)。
 * 執行:node tests/test_panel_voicefx.js
 *
 * 為什麼要有這個:掛效果那段程式跑在 Premiere 裡面,平常只能開 Premiere 手動點。
 * 但它最容易壞的地方偏偏不是「正常情況」,而是:
 *   - 中文版 Premiere 的效果叫「參數等化器」而不是 Parametric Equalizer;
 *   - 片段有一千多個(剪很兇的教學片);
 *   - 某個效果在這台機器上根本不存在。
 * 這幾種情況用真的 Premiere 反而很難湊出來,用假的反而測得準。
 *
 * host.jsx 是純 JavaScript、沒有 import,所以可以直接餵一個假的 Premiere 給它跑。
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

// ---------- 假的 Premiere ----------
// effectNames = 這台 Premiere「有」哪些音訊效果(用來模擬不同語言版本)
// clipsPerTrack = 每條音軌上的片段名稱
function fakePremiere(effectNames, clipsPerTrack) {
  const applied = [];          // 記錄每個片段被掛了哪些效果,供斷言檢查
  function makeItem(name) {
    const log = [];
    applied.push({ name: name, fx: log });
    return {
      name: name,
      type: name === "" ? "Empty" : "Clip",
      addAudioEffect: function (fx) { log.push(fx.name); },
    };
  }
  const tracks = clipsPerTrack.map(function (names) {
    const items = names.map(makeItem);
    return { numItems: items.length, getItemAt: function (i) { return items[i]; } };
  });
  return {
    applied: applied,
    app: {
      project: { activeSequence: {} },
      enableQE: function () { },
    },
    qe: {
      project: {
        // 名單裡有才回傳,模擬 getAudioEffectByName 找不到會回 null/丟例外
        getAudioEffectByName: function (n) {
          return effectNames.indexOf(n) >= 0 ? { name: n } : null;
        },
        getAudioEffectList: function () {
          return effectNames.map(function (n) { return { name: n }; });
        },
        getActiveSequence: function () {
          return {
            numAudioTracks: tracks.length,
            getAudioTrackAt: function (i) { return tracks[i]; },
          };
        },
      },
    },
  };
}

// 把 host.jsx 載進一個帶假 Premiere 的沙盒
function loadHost(pr) {
  const code = fs.readFileSync(path.join(ROOT, "premiere-panel", "jsx", "host.jsx"), "utf8");
  const sandbox = { app: pr.app, qe: pr.qe, File: function () { } };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox;
}

// 預設鏈(跟 config/settings.py 的 PREMIERE_VOICE_FX 對應)
const CHAIN = "DeNoise|消除雜訊|降噪"
  + "||Parametric Equalizer|參數等化器|參數式等化器"
  + "||Dynamics|Dynamics Processing|動態|動態處理";

console.log("執行面板人聲處理測試...");

// --- 1. 英文版 Premiere:三個效果都掛上,而且順序正確 ---
{
  const pr = fakePremiere(
    ["DeNoise", "Parametric Equalizer", "Dynamics", "Reverb"],
    [["旁白 1", "旁白 2"]]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r.indexOf("OK 2 0") === 0, "英文版:兩個片段都掛上,沒有失敗 (" + r + ")");
  ok(pr.applied[0].fx.join(">") === "DeNoise>Parametric Equalizer>Dynamics",
    "順序是 降噪 → EQ → 壓縮(先清乾淨再修頻率再壓音量)");
}

// --- 2. 中文版 Premiere:效果名稱被翻譯了,還是要認得 ---
// 這是寫死英文名稱一定會踩到的坑,而且失敗時完全看不出原因。
{
  const pr = fakePremiere(
    ["消除雜訊", "參數等化器", "動態"],
    [["旁白 1"]]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r.indexOf("OK 1 0") === 0, "中文版:照樣掛得上 (" + r + ")");
  ok(pr.applied[0].fx.join(">") === "消除雜訊>參數等化器>動態",
    "中文版:用的是翻譯後的名稱");
}

// --- 3. 名稱只是「包含」候選字時也要認得(例如「動態處理器」) ---
{
  const pr = fakePremiere(
    ["DeNoise", "Parametric Equalizer", "動態處理器"],
    [["旁白 1"]]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r.indexOf("OK 1 0") === 0, "名稱多帶了字(動態處理器)也認得 (" + r + ")");
}

// --- 4. 缺一個效果:整個不做,並回報缺哪個 ---
// 只掛到一半的音色比完全不掛還難判斷,所以寧可不動手。
{
  const pr = fakePremiere(["DeNoise", "Parametric Equalizer"], [["旁白 1"]]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r.indexOf("NOFX") === 0, "缺效果時回報 NOFX (" + r + ")");
  ok(r.indexOf("Dynamics") >= 0, "訊息裡說得出缺的是哪一個");
  ok(pr.applied[0].fx.length === 0, "缺效果時一個都不掛(不留半套音色)");
}

// --- 5. 音樂段不掛(降噪會把音樂當噪音削掉) ---
{
  const pr = fakePremiere(
    ["DeNoise", "Parametric Equalizer", "Dynamics"],
    [["旁白 1", "音樂 示範段", "旁白 2"]]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r.indexOf("OK 2 0") === 0, "三個片段裡只掛兩個,音樂段跳過 (" + r + ")");
  const music = pr.applied.filter(function (a) { return a.name.indexOf("音樂") === 0; })[0];
  ok(music.fx.length === 0, "音樂段完全沒被掛效果");
}

// --- 6. 片段太多:拒絕動手(這才是真實教學片的樣子) ---
// 剪很兇的 34 分鐘教學片實測有 1100~1560 個片段,乘三個效果就是四千多個實例。
{
  const many = [];
  for (let i = 0; i < 1200; i++) many.push("旁白 " + i);
  const pr = fakePremiere(["DeNoise", "Parametric Equalizer", "Dynamics"], [many]);
  const h = loadHost(pr);
  const r = h.prApplyVoiceChain(CHAIN, "音樂", 20);
  ok(r === "TOOMANY 1200", "1200 個片段時拒絕動手並回報數量 (" + r + ")");
  ok(pr.applied.every(function (a) { return a.fx.length === 0; }),
    "拒絕時真的一個都沒掛(不是掛到一半才停)");
}

// --- 7. 上限設 0 / 沒給 = 不限制(照掛) ---
{
  const pr = fakePremiere(
    ["DeNoise", "Parametric Equalizer", "Dynamics"], [["旁白 1"]]);
  const h = loadHost(pr);
  ok(h.prApplyVoiceChain(CHAIN, "音樂", 0).indexOf("OK") === 0,
    "上限設 0 視為不限制");
}

// --- 8. 效果清單診斷:名稱對不上時要問得出「那你有什麼」 ---
{
  const pr = fakePremiere(["消除雜訊", "動態"], [["旁白 1"]]);
  const h = loadHost(pr);
  const r = h.prListAudioEffects();
  ok(r === "OK 消除雜訊|動態", "列得出這台實際有的效果 (" + r + ")");
}

console.log("\n全部通過 ✓  人聲處理掛載邏輯正確(共 " + passed + " 項)。");
