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
  var fieldMeta = [];        // [{f, wrap, body}] 供 show_if 連動顯示用
  var groupSections = [];    // [{sec, body}] 供隱藏整個空分組用

  var $ = function (id) { return document.getElementById(id); };
  function setStatus(t) { $("status").textContent = t; }
  function appendLog(t) { $("log").textContent += t; $("log").scrollTop = $("log").scrollHeight; }
  function toFwd(p) { return String(p).replace(/\\/g, "/"); }

  // ---------- 頁面切換 ----------
  $("toAdv").addEventListener("click", function () {
    $("page-main").style.display = "none";
    $("page-adv").style.display = "block";
    window.scrollTo(0, 0);
  });
  $("backHead").addEventListener("click", function () {
    $("page-adv").style.display = "none";
    $("page-main").style.display = "block";
    window.scrollTo(0, 0);
  });

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

  // ---------- 設定區摺疊(第一次展開才載入) ----------
  $("settingsHead").addEventListener("click", function () {
    var body = $("settingsBody");
    var open = body.style.display === "none";
    body.style.display = open ? "block" : "none";
    $("settingsToggle").textContent = open ? "▾" : "▸";
    if (open && !settingsData) { loadSettings(); }
  });

  // ---------- 讀取目前設定並產生表單 ----------
  function loadSettings() {
    $("formCommon").textContent = "讀取設定中…";
    cp.execFile(PYTHON, ["ui_settings.py", "dump"],
      { cwd: PROJECT_DIR, maxBuffer: 4 * 1024 * 1024 },
      function (err, stdout) {
        if (err) { $("formCommon").textContent = "讀取設定失敗:" + err.message; return; }
        try { settingsData = JSON.parse(stdout); }
        catch (e) { $("formCommon").textContent = "設定格式解析失敗"; return; }
        renderForm();
      });
  }

  function renderForm() {
    $("formCommon").innerHTML = "";
    $("formAdvanced").innerHTML = "";
    controls = {};
    fieldMeta = [];
    groupSections = [];
    var groupBody = {};       // "tier|group" -> body div(讓同組欄位歸到同一區塊)
    var collapsed = settingsData.collapsed_groups || [];

    settingsData.fields.forEach(function (f) {
      var container = (f.tier === "advanced") ? $("formAdvanced") : $("formCommon");
      var gkey = f.tier + "|" + (f.group || "");
      var body = groupBody[gkey];
      if (!body) {
        body = makeGroup(container, f.group || "", collapsed.indexOf(f.group) >= 0);
        groupBody[gkey] = body;
      }
      var wrap = controlFor(f, settingsData.values[f.key]);
      body.appendChild(wrap);
      fieldMeta.push({ f: f, wrap: wrap });
    });

    // 任何欄位改動都重算「哪些欄位/分組該顯示」
    [$("formCommon"), $("formAdvanced")].forEach(function (c) {
      c.addEventListener("change", applyShowIf);
      c.addEventListener("input", applyShowIf);
    });
    applyShowIf();
  }

  // 產生一個(可折疊的)分組區塊,回傳放欄位用的 body 元素
  function makeGroup(container, name, startCollapsed) {
    var sec = document.createElement("div");
    sec.className = "group";
    var body = document.createElement("div");
    body.className = "groupBody";
    if (name) {
      var head = document.createElement("div");
      head.className = "groupHead";
      var tog = document.createElement("span");
      tog.className = "groupToggle";
      var title = document.createElement("span");
      title.textContent = name;
      head.appendChild(tog); head.appendChild(title);
      function setOpen(open) {
        body.style.display = open ? "block" : "none";
        tog.textContent = open ? "▾" : "▸";
        head.setAttribute("data-open", open ? "1" : "0");
      }
      head.addEventListener("click", function () {
        setOpen(head.getAttribute("data-open") !== "1");
      });
      setOpen(!startCollapsed);
      sec.appendChild(head);
    }
    sec.appendChild(body);
    container.appendChild(sec);
    groupSections.push({ sec: sec, body: body });
    return body;
  }

  // 依 show_if 規則,決定每個欄位/分組是否顯示(隨相依欄位的值連動)
  function applyShowIf() {
    fieldMeta.forEach(function (m) {
      var show = true;
      var cond = m.f.show_if;
      if (cond) {
        Object.keys(cond).forEach(function (k) {
          var cur = controls[k] ? controls[k]() : undefined;
          if (cond[k].indexOf(cur) < 0) show = false;
        });
      }
      m.wrap.style.display = show ? "" : "none";
    });
    // 整個分組的欄位都被藏起來時,連分組標題一起收掉
    groupSections.forEach(function (g) {
      var anyVisible = Array.prototype.some.call(g.body.children, function (ch) {
        return ch.style.display !== "none";
      });
      g.sec.style.display = anyVisible ? "" : "none";
    });
  }

  // 依欄位型別產生一個控制項,登記讀值函式到 controls[key]
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
      input = document.createElement("div");
      input.className = "numrow";
      var range = document.createElement("input");
      range.type = "range";
      var num = document.createElement("input");
      num.type = "number";
      // 滑條一律套上下限;數字框:soft 欄位不套上下限(可超出範圍手動輸入)
      if (f.min !== undefined) { range.min = f.min; if (!f.soft) num.min = f.min; }
      if (f.max !== undefined) { range.max = f.max; if (!f.soft) num.max = f.max; }
      if (f.step !== undefined) { range.step = f.step; num.step = f.step; }
      function clampNum(v) {
        v = parseFloat(v);
        if (isNaN(v)) v = (f.default !== undefined ? f.default
                           : (f.min !== undefined ? f.min : 0));
        if (!f.soft) {                       // 硬上下限才夾;soft 欄位放行
          if (f.min !== undefined && v < f.min) v = f.min;
          if (f.max !== undefined && v > f.max) v = f.max;
        }
        return v;
      }
      range.value = value; num.value = value;
      // 滑條拉動 -> 同步數字框;數字框輸入 -> 同步滑條(超出範圍時滑條自動停在端點)
      range.addEventListener("input", function () { num.value = range.value; });
      num.addEventListener("input", function () { range.value = num.value; });
      num.addEventListener("change", function () {
        var c = clampNum(num.value); num.value = c; range.value = c;
      });
      // 點兩下恢復預設值
      if (f.default !== undefined) {
        var reset = function () { num.value = f.default; range.value = f.default; };
        range.addEventListener("dblclick", reset);
        num.addEventListener("dblclick", reset);
        range.title = "點兩下恢復預設(" + f.default + ")"
          + (f.soft ? ";數字框可手動超出滑條範圍" : "");
        num.title = range.title;
      }
      input.appendChild(range); input.appendChild(num);
      controls[f.key] = function () { return clampNum(num.value); };

    } else if (f.type === "combo") {
      input = document.createElement("input");
      input.type = "text";
      input.setAttribute("list", "dl_" + f.key);
      input.value = value;
      var dl = document.createElement("datalist");
      dl.id = "dl_" + f.key;
      (f.options || []).forEach(function (o) {
        var op = document.createElement("option"); op.value = o; dl.appendChild(op);
      });
      wrap.appendChild(dl);
      controls[f.key] = function () { return input.value.trim(); };

    } else if (f.type === "list") {
      input = document.createElement("input");
      input.type = "text";
      input.value = (value || []).join("、");
      controls[f.key] = function () {
        return input.value.split(/[、,，\n]/).map(function (s) { return s.trim(); })
          .filter(function (s) { return s; });
      };

    } else if (f.type === "vstlist") {
      input = document.createElement("div");
      var paths = (value || []).slice();
      function renderRows() {
        input.innerHTML = "";
        paths.forEach(function (p, i) {
          var row = document.createElement("div");
          row.className = "vstrow";
          var pi = document.createElement("input");
          pi.type = "text"; pi.value = p; pi.placeholder = ".vst3 完整路徑";
          pi.addEventListener("input", function () { paths[i] = pi.value; });
          var adj = document.createElement("button");
          adj.className = "btn small ghost"; adj.textContent = "調整";
          adj.title = "打開這個外掛的介面調參數";
          adj.style.display = "none"; // 先藏起來,確認外掛真的有視窗介面才顯示
          adj.addEventListener("click", function () { openVst(paths[i]); });
          // 問這個外掛有沒有視窗介面:有才顯示「調整」;沒有(如 VoiceFX)就提示改用滑條
          if (p && p.trim()) {
            probeVstCaps(p.trim(), function (caps) {
              if (caps && caps.has_editor) {
                adj.style.display = "";
              } else if (caps && caps.ok) {
                pi.title = "這個外掛沒有視窗介面,請用下方「降噪:消除什麼 / 強度」調整";
              }
            });
          }
          var del = document.createElement("button");
          del.className = "btn small ghost"; del.textContent = "✕";
          del.addEventListener("click", function () { paths.splice(i, 1); renderRows(); });
          row.appendChild(pi); row.appendChild(adj); row.appendChild(del);
          input.appendChild(row);
        });
        var add = document.createElement("button");
        add.className = "btn small ghost"; add.textContent = "+ 新增外掛";
        add.addEventListener("click", function () { paths.push(""); renderRows(); });
        input.appendChild(add);
      }
      renderRows();
      controls[f.key] = function () {
        return paths.map(function (s) { return String(s).trim(); })
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
      if (typeof v === "number" && isNaN(v)) return;
      out[k] = v;
    });
    return out;
  }

  // ---------- 儲存設定到 settings_local.json ----------
  function saveSettings(cb, msgId) {
    var msg = $(msgId || "saveMsg");
    if (!settingsData) { if (cb) cb(); return; }
    var vals = collectValues();
    var dst = path.join(PROJECT_DIR, "config", "settings_local.json");
    fs.writeFile(dst, JSON.stringify(vals, null, 2), { encoding: "utf8" }, function (err) {
      if (msg) {
        if (err) { msg.textContent = "儲存失敗:" + err.message; msg.style.color = "#e06c6c"; }
        else {
          msg.textContent = "已儲存 ✓"; msg.style.color = "#2e8b57";
          setTimeout(function () { msg.textContent = ""; }, 2500);
        }
      }
      if (cb) cb(err);
    });
  }
  $("save").addEventListener("click", function () { saveSettings(null, "saveMsg"); });
  $("save2").addEventListener("click", function () { saveSettings(null, "saveMsg2"); });

  // ---------- 問外掛有沒有視窗介面(決定要不要顯示「調整」鈕) ----------
  var vstCapsCache = {}; // 路徑 -> caps,同一路徑只問一次
  function probeVstCaps(p, cb) {
    if (vstCapsCache[p]) { cb(vstCapsCache[p]); return; }
    var out = "";
    try {
      var proc = cp.spawn(PYTHON, ["vst_tool.py", "caps", p], { cwd: PROJECT_DIR });
      proc.stdout.on("data", function (d) { out += d.toString(); });
      proc.on("error", function () { cb(null); });
      proc.on("close", function () {
        var caps = null;
        try { caps = JSON.parse(out.trim().split(/\r?\n/).pop()); } catch (e) {}
        if (caps) vstCapsCache[p] = caps;
        cb(caps);
      });
    } catch (e) { cb(null); }
  }

  // ---------- 打開 VST 外掛介面調參數 ----------
  function openVst(p) {
    var msg = $("saveMsg2");
    function say(t, ok) { if (msg) { msg.textContent = t; msg.style.color = ok ? "#2e8b57" : "#e06c6c"; } }
    if (!p || !p.trim()) { say("請先填 .vst3 路徑", false); return; }
    say("外掛介面開啟中…調整後關閉那個視窗即會儲存", true);
    var proc = cp.spawn(PYTHON, ["vst_tool.py", "open", p.trim()], { cwd: PROJECT_DIR });
    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });
    proc.on("error", function (e) { say("無法啟動:" + e.message, false); });
    proc.on("close", function (code) {
      if (code === 0) { say("VST 參數已儲存 ✓,下次剪輯自動套用", true); }
      else if (code === 2) { say("這個外掛沒有視窗介面,請改用下方「降噪:消除什麼 / 強度」調整", false); }
      else { say("調整結束(代碼 " + code + ",見下方訊息)", false); }
    });
  }

  // ---------- 一鍵自動剪輯 ----------
  $("run").addEventListener("click", function () {
    if (!selectedVideo) return;
    saveSettings(function () { runPipeline(); }, "saveMsg");
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
