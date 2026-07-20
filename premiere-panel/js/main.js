(function () {
  "use strict";

  var cs = new CSInterface();
  var cp = require("child_process");
  var fs = require("fs");
  var path = require("path");

  // =====================================================================
  //  專案資料夾與 Python 在哪?
  //  一般人裝完就該直接能用,所以這裡全自動,不用改任何一行:
  //    1. config/panel.json —— 安裝程式寫進去的(最準)
  //    2. 自動偵測 —— 面板資料夾的上一層就是專案;venv 裡就有 Python
  //    3. 最後退路 —— 交給系統 PATH 上的 python
  //  想手動指定就編輯 config/panel.json 的 project_dir / python。
  // =====================================================================

  // 面板是用 junction 連過去的,要用 realpath 解開才拿得到真正的專案位置
  function detectProjectDir() {
    try {
      var real = fs.realpathSync(cs.getSystemPath(SystemPath.EXTENSION));
      var parent = path.dirname(real);
      if (fs.existsSync(path.join(parent, "pipeline.py"))) return parent;
    } catch (e) {}
    return null;
  }

  function detectPython(dir) {
    var cands = [
      path.join(dir, "venv", "Scripts", "python.exe"),
      path.join(dir, ".venv", "Scripts", "python.exe"),
    ];
    for (var i = 0; i < cands.length; i++) {
      try { if (fs.existsSync(cands[i])) return cands[i]; } catch (e) {}
    }
    return "python";        // 沒有虛擬環境就用系統上的 Python
  }

  var PROJECT_DIR = detectProjectDir() || "C:\\pr-autoedit";
  var PYTHON = null;
  try {
    var cfgFile = path.join(PROJECT_DIR, "config", "panel.json");
    if (fs.existsSync(cfgFile)) {
      var saved = JSON.parse(fs.readFileSync(cfgFile, "utf8"));
      if (saved.project_dir) PROJECT_DIR = saved.project_dir;
      if (saved.python) PYTHON = saved.python;
    }
  } catch (e) {}
  if (!PYTHON) PYTHON = detectPython(PROJECT_DIR);

  var selectedVideo = null;
  var settingsData = null;   // ui_settings.py dump 的結果
  var controls = {};         // key -> 讀值函式
  var fieldMeta = [];        // [{f, wrap}] 供 show_if 連動顯示用
  var groupSections = [];    // [{sec, body}] 供隱藏整個空分組用
  var lastVideo = null;      // 最近一次成功處理的影片(剪輯後工具用)

  var $ = function (id) { return document.getElementById(id); };

  // 正在跑的 Python 子行程。面板被關掉/重新載入時要把它殺掉 ——
  // 不然它會變成沒人管的孤兒行程,繼續佔著顯示卡記憶體不放。
  var running = null;
  function track(proc) {
    running = proc;
    proc.on("close", function () { if (running === proc) running = null; });
    return proc;
  }
  window.addEventListener("beforeunload", function () {
    if (running) { try { running.kill(); } catch (e) {} }
  });

  // 狀態列:文字 + 顏色(busy=黃、ok=綠、err=紅,不給 kind 就是預設藍)
  function setStatus(t, kind) {
    $("status").textContent = t;
    $("status").className = "status" + (kind ? " " + kind : "");
  }

  // log:一行一行加進去,錯誤行標紅、完成行標綠,掃一眼就找得到重點
  var LOG_ERR_RE = /(Traceback|Error|錯誤|失敗|找不到|⚠)/;
  var LOG_OK_RE = /(✓|完成)/;
  var logBuf = "";           // 這次執行的完整訊息,失敗時拿來對照錯誤翻譯表
  function appendLog(t) {
    logBuf += String(t);
    var log = $("log");
    var lines = String(t).split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      if (i > 0) log.appendChild(document.createTextNode("\n"));
      if (!lines[i]) continue;
      var span = document.createElement("span");
      if (LOG_ERR_RE.test(lines[i])) span.className = "lg-err";
      else if (LOG_OK_RE.test(lines[i])) span.className = "lg-ok";
      span.textContent = lines[i];
      log.appendChild(span);
    }
    log.scrollTop = log.scrollHeight;
  }
  function toFwd(p) { return String(p).replace(/\\/g, "/"); }

  // ---------- 完成提示音 ----------
  // 剪一支長片要好幾分鐘,你不會盯著看。用聲音叫你回來。
  // 音是即時合成的,不放音檔進專案(省得帶一個二進位檔)。
  // 成功=兩個上行音、失敗=一個低音。
  function beep(ok) {
    if (!soundOn()) return;
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      var notes = ok ? [[880, 0], [1318.5, 0.13]] : [[311, 0]];
      notes.forEach(function (n) {
        var osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = n[0];
        var t0 = ctx.currentTime + n[1];
        // 淡入淡出,不然會有「喀」的爆音
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.26);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(t0); osc.stop(t0 + 0.3);
      });
      setTimeout(function () { try { ctx.close(); } catch (e) {} }, 1200);
    } catch (e) {}
  }

  // ---------- 錯誤翻譯表 ----------
  // Python 出錯時吐的是整片英文,對非程式背景的人等於沒訊息。
  // 這裡把實際遇過的錯誤翻成「發生什麼事 + 下一步做什麼」。
  // 順序有意義:越具體的規則放越前面,第一個命中的就是答案。
  var ERROR_TABLE = [
    { re: /(CUDA|GPU) out of memory|torch\.cuda\.OutOfMemoryError/i,
      msg: "顯示卡記憶體不夠用了。\n" +
           "  → 到「⚙ 設定 > 辨識 > 辨識模型」把 large-v3 改成 medium,再跑一次。\n" +
           "     (medium 稍微不準一點,但省一半以上的顯卡記憶體)" },
    { re: /No module named ['"]?([\w.]+)/i,
      msg: "少裝了一個套件:$1。\n" +
           "  → 在命令列執行:pip install $1\n" +
           "     (若你用的是 miniconda,請先切到跟面板同一個環境)" },
    { re: /auto-editor/i,
      msg: "剪輯引擎 auto-editor 沒跑成功。\n" +
           "  → 在命令列執行:pip install auto-editor" },
    { re: /\[WinError 2\]|ffmpeg.*(not found|不是內部或外部命令)|'ffmpeg' is not recognized/i,
      msg: "找不到 ffmpeg(處理影音的必要工具)。\n" +
           "  → 用系統管理員身分執行:winget install Gyan.FFmpeg\n" +
           "     裝完要重新開啟 Premiere,面板才吃得到新的 PATH。" },
    { re: /\[WinError 5\]|Permission denied|Errno 13|4294967283/i,
      msg: "檔案被鎖住,寫不進去 —— 幾乎都是 Premiere 正在使用那個影片檔。\n" +
           "  → 在 Premiere 專案面板把這支影片相關的序列關掉(或關掉專案),再按一次。\n" +
           "     程式已經會自動改用新檔名避開,若仍失敗才需要這樣做。" },
    { re: /cudnn|CUDA driver|no kernel image|CUDA error/i,
      msg: "顯示卡的 CUDA 環境有問題(驅動或 PyTorch 版本不合)。\n" +
           "  → 先確認能不能跑:改用 CPU 辨識(較慢但一定會動)——\n" +
           "     編輯 config/settings_local.json,加一行 \"WHISPER_DEVICE\": \"cpu\"" },
    { re: /scan failure|Unable to load plugin|VST/i,
      msg: "降噪外掛載入失敗。\n" +
           "  → 檢查「進階設定 > VST 外掛路徑」是不是指到「內層」那個 .vst3 檔。\n" +
           "     VoiceFX 正確路徑長這樣(注意最後還有一層 .vst3):\n" +
           "     ...\\VoiceFX.vst3\\Contents\\x86_64-win\\VoiceFX.vst3" },
    { re: /No space left|磁碟空間|WinError 112/i,
      msg: "硬碟空間不夠了。\n" +
           "  → 這個工具會在 output 資料夾產生暫存音檔(4K 長片可能好幾 GB)。\n" +
           "     清掉 output 底下用不到的舊影片資料夾即可。" },
    { re: /沒有音軌/,
      msg: "這支影片沒有聲音,本工具沒有東西可以處理。\n" +
           "  → 確認你選的是「有收音」的錄影檔。" }
  ];

  // 從這次的訊息裡找出看得懂的解釋;找不到就回 null(照舊顯示原始訊息)
  function explainError(text) {
    for (var i = 0; i < ERROR_TABLE.length; i++) {
      var m = ERROR_TABLE[i].re.exec(text);
      if (m) {
        return ERROR_TABLE[i].msg.replace(/\$1/g, m[1] || "");
      }
    }
    return null;
  }

  // 失敗時在 log 尾巴補上白話說明
  function explainInto(prefix) {
    var hint = explainError(logBuf);
    appendLog("\n" + (prefix || "── 這是什麼意思 ──") + "\n" +
      (hint || "沒有對應的常見原因。把上面的訊息整段複製下來回報," +
               "特別是含 Error / Traceback 的那幾行。") + "\n");
  }

  // 啟動 Python 失敗時的說明。
  // 舊訊息叫人「去改 main.js 的 PYTHON」,但改版後正確的位置是
  // config/panel.json,照舊訊息做只會白忙一場。這裡直接把目前用的路徑
  // 印出來,你一眼就看得出它找錯地方了。
  function pythonFailMsg(e) {
    return "找不到 Python,沒辦法開始。\n" +
      "  目前用的 Python:" + PYTHON + "\n" +
      "  目前的專案資料夾:" + PROJECT_DIR + "\n" +
      "  路徑不對的話,編輯 " + path.join(PROJECT_DIR, "config", "panel.json") +
      " 裡的 python / project_dir 兩個欄位,存檔後重新載入面板。\n" +
      "  (原始訊息:" + e.message + ")";
  }

  // ---------- 介面偏好(字級、提示音)----------
  // 這兩個是「面板長相」的偏好,不是剪輯設定,所以存在瀏覽器本機就好,
  // 不寫進 settings_local.json,免得跟剪輯參數混在一起。
  function pref(k, v) {
    try {
      if (v === undefined) return window.localStorage.getItem("pr_" + k);
      window.localStorage.setItem("pr_" + k, v);
    } catch (e) {}
    return null;
  }
  function soundOn() { return $("soundOn") ? $("soundOn").checked : true; }

  function applyScale(scale) {
    document.documentElement.style.setProperty("--ui-scale", scale);
    var btns = document.querySelectorAll(".sizebtn");
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("on", btns[i].getAttribute("data-scale") === String(scale));
    }
    pref("ui_scale", scale);
  }
  (function initPrefs() {
    var btns = document.querySelectorAll(".sizebtn");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        applyScale(this.getAttribute("data-scale"));
      });
    }
    applyScale(pref("ui_scale") || "1.25");     // 預設「中」,原本的字實在偏小
    var s = pref("sound_on");
    if (s !== null && $("soundOn")) $("soundOn").checked = (s === "1");
    if ($("soundOn")) {
      $("soundOn").addEventListener("change", function () {
        pref("sound_on", this.checked ? "1" : "0");
        if (this.checked) beep(true);           // 打勾時先讓你聽聽看
      });
    }
  })();

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

  // ---------- 用 Premiere 裡選取的素材 ----------
  var VIDEO_EXT_RE = /\.(mp4|mov|mkv|avi|m4v|mxf|mts|m2ts|wmv)$/i;

  function useVideo(p, note) {
    selectedVideo = p;
    $("videoPath").textContent = p + (note || "");
    $("run").disabled = false;
  }

  $("pickSelected").addEventListener("click", function () {
    setStatus("讀取 Premiere 裡選取的素材…", "busy");
    cs.evalScript("prGetSelectedMedia()", function (r) {
      if (r === "NONE") {
        setStatus("Premiere 裡沒有選取任何素材 —— "
          + "請先到專案面板或時間軸點選一個影片,再按一次", "err");
        return;
      }
      if (!r || r.indexOf("OK ") !== 0) {
        setStatus("讀不到選取的素材:" + r, "err");
        return;
      }
      var p = r.slice(3);
      if (!fs.existsSync(p)) {
        setStatus("這個素材的檔案找不到(可能已被移動或改名):" + p, "err");
        return;
      }
      if (!VIDEO_EXT_RE.test(p)) {
        setStatus("你選的不是影片檔(" + path.basename(p)
          + ")—— 請改選原始錄影檔", "err");
        return;
      }
      // 選到本工具自己產出的影片的話,剪出來的東西會是「剪過的再剪一次」,
      // 幾乎一定不是你要的。這種錯很難自己看出來,所以直接擋下。
      if (/[\\/]output[\\/]/i.test(p) && /01_clean_av/i.test(p)) {
        setStatus("你選到的是本工具產生的影片(01_clean_av),不是原始錄影檔。"
          + "請改選你自己錄的那支原片。", "err");
        return;
      }
      useVideo(p, "(從 Premiere 選取)");
      setStatus("已帶入 Premiere 選取的素材,可以開始自動剪輯", "ok");
    });
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
        fillPresetPicker();
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

    // 任何欄位改動都重算「哪些欄位/分組該顯示」,並自動存檔。
    // 用具名函式:renderForm 會被重複呼叫(例如套用組合),
    // 同一個函式重複註冊會被瀏覽器忽略,不會愈疊愈多。
    [$("formCommon"), $("formAdvanced")].forEach(function (c) {
      c.addEventListener("change", applyShowIf);
      c.addEventListener("input", applyShowIf);
      c.addEventListener("change", autoSave);
      c.addEventListener("input", autoSave);
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
          del.addEventListener("click", function () {
            paths.splice(i, 1); renderRows(); autoSave();
          });
          row.appendChild(pi); row.appendChild(adj); row.appendChild(del);
          input.appendChild(row);
        });
        var add = document.createElement("button");
        add.className = "btn small ghost"; add.textContent = "+ 新增外掛";
        add.addEventListener("click", function () {
          paths.push(""); renderRows(); autoSave();
        });
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
          autoSave();      // 晶片不是表單元件,不會冒泡,要自己叫一次
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

  // ---------- 設定組合 ----------
  var PRESETS_FILE = function () {
    return path.join(PROJECT_DIR, "config", "presets_local.json");
  };

  function fillPresetPicker() {
    var sel = $("presetPick");
    if (!sel || !settingsData) return;
    sel.innerHTML = "";
    var first = document.createElement("option");
    first.value = ""; first.textContent = "選一個設定組合…";
    sel.appendChild(first);
    var mine = settingsData.my_presets || [];
    Object.keys(settingsData.presets || {}).forEach(function (name) {
      var op = document.createElement("option");
      op.value = name;
      op.textContent = name + (mine.indexOf(name) >= 0 ? "(我存的)" : "");
      sel.appendChild(op);
    });
  }

  // 套用組合:組合裡有寫的就用它的值,沒寫的一律回到內建預設。
  // 這樣「套用之後看到的就是這個組合的全貌」,不會殘留上一個組合的設定。
  $("presetApply").addEventListener("click", function () {
    var name = $("presetPick").value;
    if (!name || !settingsData) return;
    var preset = (settingsData.presets || {})[name] || {};
    var defs = settingsData.defaults || {};
    (settingsData.preset_keys || []).forEach(function (k) {
      var v = (k in preset) ? preset[k] : defs[k];
      if (v === undefined || !settingsData.values.hasOwnProperty(k)) return;
      settingsData.values[k] = v;
    });
    renderForm();                 // 用新值重畫表單
    fillPresetPicker();
    $("presetPick").value = name;
    saveSettings(null, "saveMsg");
    $("saveMsg").textContent = "已套用「" + name + "」";
    $("saveMsg").style.color = "#2e8b57";
  });

  $("presetSave").addEventListener("click", function () {
    if (!settingsData) return;
    var name = window.prompt("把目前的剪輯設定存成組合,取個名字:", "");
    if (!name) return;
    name = String(name).trim();
    if (!name) return;
    var cur = collectValues();
    var body = {};
    (settingsData.preset_keys || []).forEach(function (k) {
      if (k in cur) body[k] = cur[k];
    });
    var mine = {};
    try {
      if (fs.existsSync(PRESETS_FILE())) {
        mine = JSON.parse(fs.readFileSync(PRESETS_FILE(), "utf8")) || {};
      }
    } catch (e) {}
    mine[name] = body;
    try {
      fs.writeFileSync(PRESETS_FILE(), JSON.stringify(mine, null, 2), "utf8");
    } catch (e) {
      $("saveMsg").textContent = "存不起來:" + e.message;
      $("saveMsg").style.color = "#e06c6c";
      return;
    }
    settingsData.presets[name] = body;
    if ((settingsData.my_presets || []).indexOf(name) < 0) {
      settingsData.my_presets = (settingsData.my_presets || []).concat(name);
    }
    fillPresetPicker();
    $("presetPick").value = name;
    $("saveMsg").textContent = "已存成「" + name + "」";
    $("saveMsg").style.color = "#2e8b57";
  });

  $("presetDel").addEventListener("click", function () {
    var name = $("presetPick").value;
    if (!name || !settingsData) return;
    if ((settingsData.my_presets || []).indexOf(name) < 0) {
      $("saveMsg").textContent = "內建組合不能刪(只能刪你自己存的)";
      $("saveMsg").style.color = "#e06c6c";
      return;
    }
    var mine = {};
    try { mine = JSON.parse(fs.readFileSync(PRESETS_FILE(), "utf8")) || {}; }
    catch (e) {}
    delete mine[name];
    try { fs.writeFileSync(PRESETS_FILE(), JSON.stringify(mine, null, 2), "utf8"); }
    catch (e) {}
    delete settingsData.presets[name];
    settingsData.my_presets = (settingsData.my_presets || []).filter(
      function (n) { return n !== name; });
    fillPresetPicker();
    $("saveMsg").textContent = "已刪除「" + name + "」";
    $("saveMsg").style.color = "#2e8b57";
  });

  // ---------- 收集表單 -> 物件 ----------
  // 表單上「目前」的所有值(給自動化步驟判斷用)
  function collectValues() {
    var out = {};
    Object.keys(controls).forEach(function (k) {
      var v = controls[k]();
      if (typeof v === "number" && isNaN(v)) return;
      out[k] = v;
    });
    return out;
  }

  function sameAsDefault(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  // 只收「跟內建預設不一樣」的值 —— 這才是要存進 settings_local.json 的東西。
  // 為什麼不整份存:整份存等於把當下的每一個預設值都釘死在你的個人設定裡,
  // 以後程式改良了任何預設(例如冗詞清單加了新詞),你永遠吃不到。
  function collectChangedValues() {
    var defs = (settingsData && settingsData.defaults) || {};
    var all = collectValues();
    var out = {};
    Object.keys(all).forEach(function (k) {
      if (k in defs && sameAsDefault(all[k], defs[k])) return;
      out[k] = all[k];
    });
    return out;
  }

  // ---------- 儲存設定到 settings_local.json ----------
  function saveSettings(cb, msgId) {
    var msg = $(msgId || "saveMsg");
    if (!settingsData) { if (cb) cb(); return; }
    var vals = collectChangedValues();
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
  // ---------- 自動儲存 ----------
  // 改了就存,不用按按鈕。滑條會連續觸發事件,所以延遲一下再寫檔:
  // 拖動過程中不會反覆寫入,放開手約半秒後才存一次。
  var saveTimer = null;
  function autoSave() {
    if (!settingsData) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      saveTimer = null;
      // 兩頁共用同一份設定,哪一頁開著就把訊息顯示在那一頁
      var onAdv = $("page-adv").style.display !== "none";
      saveSettings(null, onAdv ? "saveMsg2" : "saveMsg");
    }, 500);
  }

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
    logBuf = "";
    setStatus("處理中,請稍候…(第一次會下載模型,較久)", "busy");
    appendLog("▶ 已啟動,正在載入程式與模型…(下面沒動靜是正常的,請稍候)\n");

    var name = path.basename(selectedVideo, path.extname(selectedVideo));
    // -u = 不緩衝輸出:Python 的進度訊息才會「即時」出現在下面,
    // 不然會累積到一大段才一次噴出來,看起來像當掉
    var proc = track(cp.spawn(PYTHON, ["-u", "pipeline.py", selectedVideo],
      { cwd: PROJECT_DIR }));
    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });
    proc.on("error", function (e) {
      setStatus("無法啟動 Python,詳見下方訊息", "err");
      appendLog(pythonFailMsg(e) + "\n");
      $("run").disabled = false; $("pick").disabled = false;
    });
    proc.on("close", function (code) {
      $("pick").disabled = false;
      if (code !== 0) {
        setStatus("處理失敗,下方有白話說明", "err");
        explainInto();
        beep(false);
        $("run").disabled = false;
        return;
      }
      setStatus("剪輯完成,正在匯入 Premiere…", "busy");
      var outDir = path.join(PROJECT_DIR, "output", name);
      var xml = toFwd(path.join(outDir, "04_project.xml"));
      var srt = toFwd(path.join(outDir, "04_subtitles.srt"));
      // "1" = 覆蓋模式:重跑同一支影片時,把上次那條同名序列換掉,不愈堆愈多
      cs.evalScript('prImportEditedProject("' + xml + '","' + srt + '","1")', function (r) {
        if (r && r.indexOf("OK") === 0) {
          var n = parseInt(r.split(" ")[1], 10) || 0;
          var base = "完成 ✓ 已匯入序列" +
            (n ? "(已換掉上次的舊序列)" : "") + subsMsg(r);
          setStatus(base, "ok");
          beep(true);
          cleanOldSubtitleCopies(outDir, 3);
          rememberVideo(selectedVideo);
          runAutoSteps(selectedVideo, function (extra) {
            setStatus(base + extra, "ok");
          });
        } else {
          setStatus("Python 跑完了,但匯入時出錯:" + r, "err");
          rememberVideo(selectedVideo);
        }
        $("run").disabled = false;
      });
    });
  }

  // =====================================================================
  //  剪輯後工具 —— 活專案的「隨時可改」按鈕(P3/P4/P5)
  // =====================================================================

  function outDirOf(video) {
    return path.join(PROJECT_DIR, "output",
      path.basename(video, path.extname(video)));
  }

  // 匯入字幕時會複製成帶時間戳的新檔(見 host.jsx 的說明),
  // 這裡順手清掉太舊的副本,只留最近幾份,免得 output 資料夾愈積愈多。
  function cleanOldSubtitleCopies(outDir, keep) {
    try {
      var files = fs.readdirSync(outDir).filter(function (f) {
        return /^0[45]_subtitles.*_\d{6}\.srt$/.test(f);
      }).map(function (f) {
        var p = path.join(outDir, f);
        return { p: p, t: fs.statSync(p).mtime.getTime() };
      }).sort(function (a, b) { return b.t - a.t; });
      files.slice(keep || 3).forEach(function (x) {
        try { fs.unlinkSync(x.p); } catch (e) {}
      });
    } catch (e) {}
  }

  // 把 host.jsx 回傳的字幕結果翻成人話
  function subsMsg(r) {
    if (r.indexOf("SUBS_OK") >= 0) return ";字幕已掛上序列";
    if (r.indexOf("SUBS_IMPORTED") >= 0)
      return ";字幕已匯入專案(請從專案面板拖到時間軸)";
    if (r.indexOf("SUBS_FAIL") >= 0) return ";但字幕匯入失敗";
    return "";
  }

  // 記住最近處理的影片:跑完出現「剪輯後工具」,面板重開也還在
  function rememberVideo(video) {
    if (!video) return;
    lastVideo = video;
    try { window.localStorage.setItem("pr_last_video", video); } catch (e) {}
    $("afterSec").style.display = "block";
    $("afterVideo").textContent = "目前影片:" + path.basename(video);
  }
  (function restoreLast() {
    var v = null;
    try { v = window.localStorage.getItem("pr_last_video"); } catch (e) {}
    if (v && fs.existsSync(v) && fs.existsSync(outDirOf(v))) {
      rememberVideo(v);
      // 順便接續上次的選擇:重開面板不用重選,直接就能重跑或用剪輯後工具
      selectedVideo = v;
      $("videoPath").textContent = v + "(上次處理的影片,可直接用)";
      $("run").disabled = false;
    }
  })();

  // 剪輯後工具區塊的摺疊
  $("afterHead").addEventListener("click", function () {
    var body = $("afterBody");
    var open = body.style.display === "none";
    body.style.display = open ? "block" : "none";
    $("afterToggle").textContent = open ? "▾" : "▸";
  });

  function afterSay(t, ok) {
    var m = $("afterMsg");
    m.textContent = t;
    m.style.color = ok ? "#2e8b57" : "#e06c6c";
  }
  function setAfterButtons(enabled) {
    ["openReport", "rebuild", "applyVst", "subsFromSeq",
     "clearCache"].forEach(function (id) {
      if ($(id)) $(id).disabled = !enabled;
    });
  }

  // ---------- 清除快取 ----------
  $("clearCache").addEventListener("click", function () {
    if (!lastVideo) return;
    setAfterButtons(false);
    afterSay("清除中…", true);
    cp.execFile(PYTHON,
      ["-m", "modules.workspace", "clear", outDirOf(lastVideo), "asr"],
      { cwd: PROJECT_DIR },
      function (err, stdout, stderr) {
        var msg = String(stdout || stderr || "").trim();
        afterSay(msg || (err ? "清除失敗" : "已清除"), !err);
        setAfterButtons(true);
      });
  });

  // ---------- 取得目前設定值 ----------
  // 表單展開過就用表單上的「當下」值(可能還沒存檔),沒展開過才問 Python。
  function withValues(cb) {
    if (settingsData && settingsData.values) {
      var v = {}, k;
      for (k in settingsData.values) v[k] = settingsData.values[k];
      var live = collectValues();
      for (k in live) v[k] = live[k];
      cb(v);
      return;
    }
    cp.execFile(PYTHON, ["ui_settings.py", "dump"],
      { cwd: PROJECT_DIR, maxBuffer: 4 * 1024 * 1024 },
      function (err, stdout) {
        if (err) { cb(null); return; }
        try { cb(JSON.parse(stdout).values); } catch (e) { cb(null); }
      });
  }

  // ---------- 開啟審閱報告(用系統預設瀏覽器) ----------
  function openReportFor(video, quiet) {
    var report = path.join(outDirOf(video), "04_report.html");
    if (!fs.existsSync(report)) {
      if (!quiet) afterSay("找不到報告檔(要先跑過一次剪輯)", false);
      return false;
    }
    try {
      cp.spawn("cmd", ["/c", "start", "", report], { windowsHide: true });
      if (!quiet) afterSay("已在瀏覽器開啟報告 ✓", true);
      return true;
    } catch (e) {
      if (!quiet) afterSay("開啟失敗:" + e.message, false);
      return false;
    }
  }

  $("openReport").addEventListener("click", function () {
    if (!lastVideo) return;
    openReportFor(lastVideo, false);
  });

  // ---------- P3:重算剪輯(快)→ 匯入新序列 ----------
  $("rebuild").addEventListener("click", function () {
    if (!lastVideo) return;
    setAfterButtons(false);
    // 文案不能寫死「不重跑辨識」:改了辨識或聲音設定時,程式會自動重跑
    // 那一段(要幾分鐘)。講死了你會以為當掉。
    afterSay("用目前設定重算中…(一般幾秒;若你改了辨識或聲音設定,"
      + "會自動重跑那一段,需要幾分鐘)", true);
    logBuf = "";
    appendLog("▶ 重算已啟動:用新設定重新決策。\n"
      + "  (剪輯類設定=幾秒;辨識或聲音類設定有改動=自動重跑該步驟,較久)\n");
    saveSettings(function () {
      // --stamp:序列名加時間。重算刻意保留舊序列讓你比較,
      // 全部同名就分不出哪條是剛剛那次了。
      var proc = track(cp.spawn(PYTHON,
        ["-u", "pipeline.py", lastVideo, "--skip-audio", "--stamp"],
        { cwd: PROJECT_DIR }));
      proc.stdout.on("data", function (d) { appendLog(d.toString()); });
      proc.stderr.on("data", function (d) { appendLog(d.toString()); });
      proc.on("error", function (e) {
        afterSay("無法啟動 Python,詳見下方訊息", false);
        appendLog(pythonFailMsg(e) + "\n");
        setAfterButtons(true);
      });
      proc.on("close", function (code) {
        if (code !== 0) {
          afterSay("重算失敗,下方有白話說明", false);
          explainInto();
          beep(false);
          setAfterButtons(true); return;
        }
        var outDir = outDirOf(lastVideo);
        var xml = toFwd(path.join(outDir, "04_project.xml"));
        var srt = toFwd(path.join(outDir, "04_subtitles.srt"));
        // 重算鈕刻意「不」覆蓋(第三個參數 "0"):留著舊序列可以兩條互相比較、
        // 覺得新的剪太兇隨時回去用舊的。要乾淨就手動刪掉不要的那條。
        cs.evalScript('prImportEditedProject("' + xml + '","' + srt + '","0")',
          function (r) {
            if (r && r.indexOf("OK") === 0) {
              var base = "已匯入新序列 ✓(名稱帶這次的時間;舊序列還在,"
                + "可以兩條互相比較,不喜歡新的就刪掉它)" + subsMsg(r);
              afterSay(base, true);
              beep(true);
              cleanOldSubtitleCopies(outDir, 3);
              runAutoSteps(lastVideo, function (extra, ok) {
                afterSay(base + extra, ok !== false);
              });
            } else { afterSay("重算完成,但匯入出錯:" + r, false); }
            setAfterButtons(true);
          });
      });
    });
  });

  // ---------- P4:幫目前序列掛降噪(QE 實驗;失敗教用音軌混音器) ----------
  // 從 VST 鏈第一個外掛的檔名推效果名(VoiceFX.vst3 -> VoiceFX)
  function effectNameFrom(values) {
    var chain = (values && values.VST_CHAIN) || [];
    if (!chain.length) return null;
    var base = String(chain[0]).replace(/\\/g, "/").split("/").pop();
    return base.replace(/\.vst3$/i, "");
  }

  var MIXER_HINT = "改用這個做法(更穩也更省資源):視窗 > 音軌混音器,"
    + "A1 軌最上面的效果插槽選 VoiceFX。整軌一次搞定,隨時可調。";

  // 把降噪掛到目前序列的每個片段。done(成功與否, 給人看的訊息)
  //
  // ⚠️ 這是「每個片段各掛一個」。VoiceFX 是 NVIDIA 的 AI 降噪,每個實例都佔
  // 顯示卡記憶體,片段一多就會把 VRAM 吃爆、Premiere 卡到不能用。
  // 所以會先問 Premiere 有幾個片段,超過上限就拒絕。
  function applyDenoise(values, done) {
    var name = effectNameFrom(values);
    if (!name) {
      done(false, "設定裡沒有 VST 外掛路徑。" + MIXER_HINT);
      return;
    }
    var max = values && values.DENOISE_PER_CLIP_MAX;
    if (typeof max !== "number") max = 20;
    // 音樂段不掛降噪(降噪是衝著人聲設計的,會把音樂當噪音削掉)
    cs.evalScript('prApplyAudioEffect("' + name + '","音樂",' + max + ')',
      function (r) {
        if (r && r.indexOf("OK") === 0) {
          done(true, "降噪已掛到 " + r.split(" ")[1] + " 個聲音片段 ✓ "
            + "到「效果控制」隨時調整,不滿意可 Ctrl+Z 復原");
        } else if (r && r.indexOf("TOOMANY") === 0) {
          done(false, "這條序列有 " + r.split(" ")[1] + " 個片段,"
            + "一個一個掛會產生同樣數量的 AI 降噪實例,"
            + "把顯示卡記憶體吃爆、Premiere 會卡死,所以沒有動手。" + MIXER_HINT);
        } else if (r === "NOFX") {
          done(false, "Premiere 效果清單裡找不到「" + name + "」。" + MIXER_HINT);
        } else {
          done(false, "掛效果失敗:" + r + " " + MIXER_HINT);
        }
      });
  }

  $("applyVst").addEventListener("click", function () {
    setAfterButtons(false);
    afterSay("嘗試把降噪掛到目前序列…", true);
    withValues(function (v) {
      applyDenoise(v, function (ok, msg) {
        afterSay(msg, ok);
        setAfterButtons(true);
      });
    });
  });

  // ---------- 剪完之後自動接手的事 ----------
  // 兩件本來都要你手動按的事:打開報告、把降噪掛上去。
  // 降噪只有在「沒烘進音檔」時才需要掛 —— 那種情況下新序列是原始聲音,
  // 不掛等於沒降噪,而每剪一次就要手動按一次實在很煩。
  function runAutoSteps(video, say) {
    withValues(function (v) {
      if (!v) return;
      if (v.AUTO_OPEN_REPORT !== false) openReportFor(video, true);

      // 降噪沒烘進音檔時,新序列聽到的是原始錄音 —— 提醒你掛一次。
      // 刻意「不」自動掛:那是每片段一個實例,會把顯示卡記憶體吃爆。
      var wantsDenoise = v.AUDIO_MODE === "vst" && v.VST_BAKE === false
        && v.VST_CHAIN && v.VST_CHAIN.length;
      if (wantsDenoise) say(";尚未降噪 —— " + MIXER_HINT, true);
    });
  }

  // ---------- P5:用目前序列的實際版面產生字幕 ----------
  $("subsFromSeq").addEventListener("click", function () {
    if (!lastVideo) return;
    setAfterButtons(false);
    afterSay("讀取目前序列的版面…", true);
    var outDir = outDirOf(lastVideo);
    // 中繼檔放在 _work/ 子資料夾(見 modules/workspace.py),
    // 最外層只留你會打開的東西
    var workDir = path.join(outDir, "_work");
    try { if (!fs.existsSync(workDir)) fs.mkdirSync(workDir); } catch (e) {}
    var layout = toFwd(path.join(workDir, "05_layout.json"));
    cs.evalScript('prDumpSequenceLayout("' + layout + '")', function (r) {
      if (!r || r.indexOf("OK") !== 0) {
        afterSay("讀不到序列版面:" + r, false); setAfterButtons(true); return;
      }
      afterSay("依序列版面對位字幕中…", true);
      appendLog("▶ 字幕對位已啟動…\n");
      cp.execFile(PYTHON, ["-u", "-m", "modules.live_subs", layout, outDir],
        { cwd: PROJECT_DIR, maxBuffer: 4 * 1024 * 1024 },
        function (err, stdout, stderr) {
          appendLog(String(stdout || "") + String(stderr || ""));
          if (err) {
            afterSay("字幕對位失敗,下方有白話說明", false);
            explainInto();
            setAfterButtons(true); return;
          }
          var srt = toFwd(path.join(outDir, "05_subtitles_final.srt"));
          // 走跟主流程同一條路:複製成新檔名再匯入,否則 Premiere 會沿用
          // 專案裡的舊字幕、看起來像「沒有重新生」
          cs.evalScript('prImportCaptionsToActive("' + srt + '")', function (r2) {
            if (r2 && r2.indexOf("SUBS_FAIL") < 0 && r2.indexOf("ERROR") !== 0) {
              afterSay("字幕已對準剪完的時間軸" + subsMsg(r2), true);
              cleanOldSubtitleCopies(outDir, 3);
            } else { afterSay("字幕產好了,但匯入出錯:" + r2, false); }
            setAfterButtons(true);
          });
        });
    });
  });
})();
