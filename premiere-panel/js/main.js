(function () {
  "use strict";

  // =====================================================================
  //  設定 —— 依你的電腦調整這兩行(其餘不用動)
  // =====================================================================
  var PROJECT_DIR = "C:\\pr-autoedit";
  var PYTHON = "C:\\Users\\Administrator\\miniconda3\\python.exe";
  // =====================================================================

  var cs = new CSInterface();
  var cp = require("child_process");
  var fs = require("fs");
  var path = require("path");

  var selectedVideo = null;
  var settingsData = null;   // ui_settings.py dump 的結果
  var controls = {};         // key -> 讀值函式

  var $ = function (id) { return document.getElementById(id); };
  function setStatus(t) { $("status").textContent = t; }
  function appendLog(t) { $("log").textContent += t; $("log").scrollTop = $("log").scrollHeight; }
  function toFwd(p) { return String(p).replace(/\\/g, "/"); }

  // ---------- 選擇影片 ----------
  $("pick").addEventListener("click", function () {
    var res = window.cep.fs.showOpenDialog(false, false, "選擇要剪輯的影片", "",
      ["mp4", "mov", "mkv", "avi", "m4v"]);
    if (res && res.data && res.data.length) {
      selectedVideo = res.data[0];
      $("videoPath").textContent = selectedVideo;
      $("run").disabled = false;
      setStatus("已選擇影片,可以開始自動剪輯");
    }
  });

  // ---------- 設定區摺疊 ----------
  $("settingsHead").addEventListener("click", function () {
    var body = $("settingsBody");
    var open = body.style.display === "none";
    body.style.display = open ? "block" : "none";
    $("settingsToggle").textContent = open ? "▾" : "▸";
    if (open && !settingsData) { loadSettings(); }
  });
  $("advHead").addEventListener("click", function () {
    var adv = $("formAdvanced");
    var open = adv.style.display === "none";
    adv.style.display = open ? "block" : "none";
    $("advChev").textContent = open ? "▾" : "▸";
  });

  // ---------- 讀取目前設定並產生表單 ----------
  function loadSettings() {
    $("formCommon").textContent = "讀取設定中…";
    cp.execFile(PYTHON, ["ui_settings.py", "dump"], { cwd: PROJECT_DIR, maxBuffer: 4 * 1024 * 1024 },
      function (err, stdout) {
        if (err) { $("formCommon").textContent = "讀取設定失敗:" + err.message; return; }
        try { settingsData = JSON.parse(stdout); } catch (e) {
          $("formCommon").textContent = "設定格式解析失敗"; return;
        }
        renderForm();
      });
  }

  function renderForm() {
    $("formCommon").innerHTML = "";
    $("formAdvanced").innerHTML = "";
    controls = {};
    settingsData.fields.forEach(function (f) {
      var el = controlFor(f, settingsData.values[f.key]);
      (f.tier === "advanced" ? $("formAdvanced") : $("formCommon")).appendChild(el);
    });
  }

  // 依欄位型別產生一個控制項,並登記讀值函式到 controls[key]
  function controlFor(f, value) {
    var wrap = document.createElement("div");
    wrap.className = "field" + (f.type === "bool" ? " bool" : "");
    var label = document.createElement("label");
    label.textContent = f.label;

    var input;
    if (f.type === "select") {
      input = document.createElement("select");
      (f.options || []).forEach(function (o) {
        var op = document.createElement("option");
        op.value = o; op.textContent = o;
        if (o === value) op.selected = true;
        input.appendChild(op);
      });
      controls[f.key] = function () { return input.value; };

    } else if (f.type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!value;
      controls[f.key] = function () { return input.checked; };

    } else if (f.type === "number") {
      input = document.createElement("input");
      input.type = "number";
      if (f.min !== undefined) input.min = f.min;
      if (f.max !== undefined) input.max = f.max;
      if (f.step !== undefined) input.step = f.step;
      input.value = value;
      controls[f.key] = function () { return parseFloat(input.value); };

    } else if (f.type === "list") {
      input = document.createElement("input");
      input.type = "text";
      input.value = (value || []).join("、");
      controls[f.key] = function () {
        return input.value.split(/[、,，\n]/).map(function (s) { return s.trim(); })
          .filter(function (s) { return s; });
      };

    } else if (f.type === "vstlist") {
      input = document.createElement("textarea");
      input.value = (value || []).join("\n");
      input.placeholder = "一行一個 .vst3 完整路徑";
      controls[f.key] = function () {
        return input.value.split("\n").map(function (s) { return s.trim(); })
          .filter(function (s) { return s; });
      };

    } else if (f.type === "category") {
      input = document.createElement("div");
      input.className = "cats";
      var picked = (value || []).slice();
      (settingsData.categories_available || []).forEach(function (cat) {
        var chip = document.createElement("span");
        chip.className = "cat" + (picked.indexOf(cat) >= 0 ? " on" : "");
        chip.textContent = cat;
        chip.addEventListener("click", function () {
          var i = picked.indexOf(cat);
          if (i >= 0) { picked.splice(i, 1); chip.classList.remove("on"); }
          else { picked.push(cat); chip.classList.add("on"); }
        });
        input.appendChild(chip);
      });
      controls[f.key] = function () { return picked.slice(); };
    }

    // bool 的 label 排在核取方塊後面(CSS 用 order 處理)
    wrap.appendChild(label);
    wrap.appendChild(input);
    if (f.hint) {
      var h = document.createElement("div");
      h.className = "fhint"; h.textContent = f.hint;
      wrap.appendChild(h);
    }
    return wrap;
  }

  // ---------- 收集表單 -> 物件 ----------
  function collectValues() {
    var out = {};
    Object.keys(controls).forEach(function (k) {
      var v = controls[k]();
      if (typeof v === "number" && isNaN(v)) return;   // 空數字欄跳過
      out[k] = v;
    });
    return out;
  }

  // ---------- 儲存設定到 settings_local.json ----------
  function saveSettings(cb) {
    if (!settingsData) { if (cb) cb(); return; }
    var vals = collectValues();
    var dst = path.join(PROJECT_DIR, "config", "settings_local.json");
    fs.writeFile(dst, JSON.stringify(vals, null, 2), { encoding: "utf8" }, function (err) {
      if (err) { $("saveMsg").textContent = "儲存失敗:" + err.message; $("saveMsg").style.color = "#e06c6c"; }
      else { $("saveMsg").textContent = "已儲存 ✓"; $("saveMsg").style.color = "#2e8b57";
             setTimeout(function () { $("saveMsg").textContent = ""; }, 2500); }
      if (cb) cb(err);
    });
  }
  $("save").addEventListener("click", function () { saveSettings(); });

  // ---------- 一鍵自動剪輯 ----------
  $("run").addEventListener("click", function () {
    if (!selectedVideo) return;
    // 先把目前表單設定存起來(若使用者調過),再跑
    saveSettings(function () { runPipeline(); });
  });

  function runPipeline() {
    $("run").disabled = true;
    $("pick").disabled = true;
    $("log").textContent = "";
    setStatus("處理中,請稍候…(第一次會下載模型,較久)");

    var name = path.basename(selectedVideo, path.extname(selectedVideo));
    var proc = cp.spawn(PYTHON, ["pipeline.py", selectedVideo], { cwd: PROJECT_DIR });
    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });
    proc.on("error", function (e) {
      setStatus("無法啟動 Python:" + e.message + "(檢查 main.js 的 PYTHON / PROJECT_DIR)");
      $("run").disabled = false; $("pick").disabled = false;
    });
    proc.on("close", function (code) {
      $("pick").disabled = false;
      if (code !== 0) { setStatus("處理失敗(代碼 " + code + "),請看下方訊息"); $("run").disabled = false; return; }
      setStatus("剪輯完成,正在匯入 Premiere…");
      var outDir = path.join(PROJECT_DIR, "output", name);
      var xml = toFwd(path.join(outDir, "04_project.xml"));
      var srt = toFwd(path.join(outDir, "04_subtitles.srt"));
      cs.evalScript('prImportEditedProject("' + xml + '","' + srt + '")', function (r) {
        if (r && r.indexOf("OK") === 0) setStatus("完成 ✓ 已匯入剪好的序列與字幕,請在 Premiere 審閱 marker");
        else setStatus("Python 跑完了,但匯入時出錯:" + r);
        $("run").disabled = false;
      });
    });
  }
})();
