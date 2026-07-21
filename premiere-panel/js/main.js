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

  // 選到的影片。可以是好幾個 —— 錄影軟體錄長片會自動切檔,一堂課常常是
  // 三四個檔,選起來會接成「一支」處理(一條序列、一份字幕、一份報告)。
  // selectedVideo 永遠是清單裡的第一個,舊的程式碼照舊用它就對了。
  var selectedVideos = [];
  var selectedVideo = null;
  var settingsData = null;   // ui_settings.py dump 的結果
  var controls = {};         // key -> 讀值函式
  var fieldMeta = [];        // [{f, wrap}] 供 show_if 連動顯示用
  var groupSections = [];    // [{sec, body}] 供隱藏整個空分組用
  // 最近一次成功處理的來源(剪輯後工具用)。永遠是「一份清單」——
  // 合併處理時不只一個檔,而重算剪輯要把整份原封不動再傳一次。
  var lastVideos = [];

  var $ = function (id) { return document.getElementById(id); };

  // 正在跑的 Python 子行程。面板被關掉/重新載入時要把它殺掉 ——
  // 不然它會變成沒人管的孤兒行程,繼續佔著顯示卡記憶體不放。
  var running = null;
  var stopping = false;      // 使用者按了停止(用來分辨「停止」與「失敗」)

  function track(proc) {
    running = proc;
    stopping = false;
    showStop(true);
    proc.on("close", function () {
      // 行程結束就把進度條收掉 —— 留著一條停在 47% 的進度條,
      // 會讓人以為還在跑
      if (running === proc) { running = null; showStop(false); hideProgress(); }
    });
    return proc;
  }

  function showStop(on) {
    var b = $("stop");
    if (!b) return;
    b.style.display = on ? "" : "none";
    if (on) b.disabled = false;
  }

  /*
   * 停掉整棵行程樹。
   *
   * ⚠️ 不能只用 proc.kill():在 Windows 上它只結束直接的子行程(python.exe),
   * Python 底下再開的 ffmpeg 會變成孤兒繼續跑 —— 繼續寫輸出檔、繼續佔著
   * 檔案鎖,使用者看到的就是「按了停止卻好像沒停」,而且下次重跑還會
   * 因為檔案被鎖住而失敗。taskkill 的 /T 會連子孫一起收掉。
   */
  function killTree(proc, done) {
    if (!proc) { if (done) done(); return; }
    var fired = false;
    function once() { if (!fired) { fired = true; if (done) done(); } }
    try {
      var t = cp.spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"],
        { windowsHide: true });
      t.on("close", once);
      t.on("error", function () {          // 沒有 taskkill 就退回原本的做法
        try { proc.kill(); } catch (e) {}
        once();
      });
    } catch (e) {
      try { proc.kill(); } catch (e2) {}
      once();
    }
  }

  window.addEventListener("beforeunload", function () {
    if (running) killTree(running);
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

  // ---------- 進度條 ----------
  // Python 在最花時間的幾步印出 `  [進度] 語音轉錄 45% 12.3/27.0 分`。
  // 這裡把它畫成一條進度條,而不是一直往訊息區堆字 ——
  // 長片的轉錄會印出上百行進度,全部堆進去會把真正的訊息淹掉。
  // 格式要跟 modules/progress.py 的 PREFIX 一致。
  var PROGRESS_RE = /^\s*\[進度\]\s+(.+?)\s+(\d+)%\s*(.*)$/;

  function showProgress(stage, pct, detail) {
    var wrap = $("progWrap");
    if (!wrap) return;
    wrap.style.display = "block";
    $("progStage").textContent = stage + (detail ? "  " + detail : "");
    $("progPct").textContent = pct + "%";
    var fill = $("progFill");
    fill.style.width = pct + "%";
    fill.classList.toggle("done", pct >= 100);
  }

  function hideProgress() {
    var wrap = $("progWrap");
    if (!wrap) return;
    wrap.style.display = "none";
    $("progFill").style.width = "0%";
    $("progFill").classList.remove("done");
  }

  function appendLog(t) {
    logBuf += String(t);
    var log = $("log");
    // 先把進度行挑掉再渲染。不能在迴圈裡 continue 就算數 ——
    // 換行分隔是「除了第一行都補一個」,進度行就算不印文字,
    // 還是會留下一個空行,長片跑完訊息區會多出上百個空行。
    var lines = String(t).split(/\r?\n/);
    var keep = [];
    for (var i = 0; i < lines.length; i++) {
      var m = PROGRESS_RE.exec(lines[i]);
      if (m) {
        showProgress(m[1], parseInt(m[2], 10), m[3]);
        continue;
      }
      keep.push(lines[i]);
    }
    for (var j = 0; j < keep.length; j++) {
      if (j > 0) log.appendChild(document.createTextNode("\n"));
      if (!keep[j]) continue;
      var span = document.createElement("span");
      if (LOG_ERR_RE.test(keep[j])) span.className = "lg-err";
      else if (LOG_OK_RE.test(keep[j])) span.className = "lg-ok";
      span.textContent = keep[j];
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
      msg: "顯示卡記憶體不足。\n" +
           "  → 到「⚙ 設定 > 辨識 > 辨識模型」把 large-v3 改成 medium,再跑一次\n" +
           "     (medium 稍微不準,但省一半以上的顯卡記憶體)" },
    { re: /No module named ['"]?([\w.]+)/i,
      msg: "缺少套件:$1。\n" +
           "  → 在命令列執行:pip install $1\n" +
           "     (用 miniconda 的話,先切到跟面板同一個環境)" },
    { re: /auto-editor/i,
      msg: "剪輯引擎 auto-editor 執行失敗。\n" +
           "  → 在命令列執行:pip install auto-editor" },
    { re: /\[WinError 2\]|找不到 ffmpeg|ffmpeg.*(not found|不是內部或外部命令)|'ffmpeg' is not recognized/i,
      msg: "找不到 ffmpeg(處理影音的必要工具)。\n" +
           "  → 用系統管理員身分執行:winget install Gyan.FFmpeg\n" +
           "     裝完要重開 Premiere,面板才吃得到新的 PATH" },
    { re: /\[WinError 5\]|Permission denied|Errno 13|4294967283/i,
      msg: "檔案被鎖住 —— 幾乎都是 Premiere 正在使用那支影片。\n" +
           "  → 把這支影片相關的序列關掉(或關掉專案),再按一次\n" +
           "     程式本來就會自動改用新檔名避開,還是失敗才需要這樣做" },
    { re: /cudnn|CUDA driver|no kernel image|CUDA error/i,
      msg: "顯示卡的 CUDA 環境有問題(驅動或 PyTorch 版本不合)。\n" +
           "  → 先改用 CPU 辨識確認跑得動(較慢但一定會動):\n" +
           "     ⚙ 設定 > 進階設定 > 辨識效能 > 「用什麼跑辨識」改成 cpu,\n" +
           "     同一區的「辨識運算精度」改成 int8,再跑一次" },
    { re: /float16|compute type|efficient_attention/i,
      msg: "顯示卡跑不動這個運算精度。\n" +
           "  → ⚙ 設定 > 進階設定 > 辨識效能 > 「辨識運算精度」\n" +
           "     改成 int8_float16,再跑一次" },
    // 「VST」這個字在正常訊息裡也會出現(「載入 1 個 VST 外掛並處理...」),
    // 所以規則要綁在「失敗」的字眼上,不能只看到 VST 就認領
    { re: /scan failure|Unable to load plugin|failed to load.*vst|vst.*(load|scan).*fail/i,
      msg: "降噪外掛載入失敗。\n" +
           "  → 檢查「進階設定 > VST 外掛路徑」有沒有指到內層那顆 .vst3\n" +
           "     正確路徑長這樣(最後還有一層 .vst3):\n" +
           "     ...\\VoiceFX.vst3\\Contents\\x86_64-win\\VoiceFX.vst3" },
    { re: /No space left|磁碟空間|WinError 112/i,
      msg: "硬碟空間不足。\n" +
           "  → output 資料夾會產生暫存音檔(4K 長片可能好幾 GB),\n" +
           "     清掉底下用不到的舊影片資料夾即可" },
    { re: /沒有音軌/,
      msg: "這支影片沒有聲音,沒有東西可以處理。\n" +
           "  → 確認你選的是有收音的錄影檔" }
  ];

  /*
   * 只在「出事的那一段」裡找線索,不要拿整份 log 去比對。
   *
   * 這是踩過的坑:一次執行的 log 裡有大量「正常」的訊息,而那些訊息裡就
   * 含著規則要找的關鍵字。實際發生過 —— 混音那一步壞掉,面板卻回答
   * 「降噪外掛載入失敗,去檢查 VST 路徑」,因為前面第一步印過一行
   * 「載入 1 個 VST 外掛並處理...」,那是成功的訊息,卻剛好命中 VST 規則。
   * 使用者於是跑去改一個根本沒壞的設定。
   *
   * 給錯答案比不給答案更糟:不給答案他會把訊息貼出來問,給錯答案他會照做,
   * 然後在錯的方向上耗很久。所以寧可回 null。
   *
   * 做法:從最後一個 Traceback(或最後幾行)開始看。Python 的錯誤一定在
   * 尾巴,前面那些都是已經跑成功的步驟。
   */
  function errorTail(text) {
    var s = String(text || "");
    var i = s.lastIndexOf("Traceback (most recent call last)");
    if (i >= 0) return s.slice(i);
    // 沒有 traceback(例如 sys.exit 印一段話就結束):看最後 25 行就好
    var lines = s.split(/\r?\n/);
    return lines.slice(Math.max(0, lines.length - 25)).join("\n");
  }

  // 從這次的訊息裡找出看得懂的解釋;找不到就回 null(照舊顯示原始訊息)
  function explainError(text) {
    var tail = errorTail(text);
    for (var i = 0; i < ERROR_TABLE.length; i++) {
      var m = ERROR_TABLE[i].re.exec(tail);
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
  // 檔名的「自然排序」:讓 part2 排在 part10 前面。
  // 跟 Python 那邊 sources.natural_key 是同一套規則 —— 面板顯示的順序
  // 必須就是實際接合的順序,不然你在畫面上確認過的東西是假的。
  function naturalKey(p) {
    var name = String(p).split(/[\\/]/).pop().toLowerCase();
    return name.replace(/\d+/g, function (n) {
      return String(n.length) + n;      // 位數前綴:字串比較就等於數值比較
    });
  }

  function sortNatural(paths) {
    return paths.slice().sort(function (a, b) {
      var ka = naturalKey(a), kb = naturalKey(b);
      return ka < kb ? -1 : (ka > kb ? 1 : 0);
    });
  }

  // 把選到的檔設成目前的來源,並把清單畫出來
  function useVideos(paths, note) {
    selectedVideos = paths.slice();
    selectedVideo = selectedVideos[0] || null;
    renderVideoList(note);
    $("run").disabled = !selectedVideo;
  }

  // 多檔時要把順序「顯示出來、而且可以調」。
  // 錄影軟體切出來的 _0001/_0002 照檔名排是對的,但你自己錄的三段
  // (開頭 / 正片 / 結尾)就不一定 —— 順序錯了會接出一支前後顛倒的片,
  // 而那要等到匯進 Premiere 才看得出來。所以一定要看得到。
  function renderVideoList(note) {
    var box = $("videoPath");
    box.innerHTML = "";
    if (!selectedVideos.length) {
      box.textContent = "尚未選擇影片";
      return;
    }
    if (selectedVideos.length === 1) {
      box.textContent = selectedVideos[0] + (note || "");
      return;
    }
    var head = document.createElement("div");
    head.className = "vhead";
    head.textContent = "接成一支處理(" + selectedVideos.length + " 個檔,"
      + "由上往下的順序)" + (note || "");
    box.appendChild(head);

    selectedVideos.forEach(function (p, i) {
      var row = document.createElement("div");
      row.className = "vrow";
      var idx = document.createElement("span");
      idx.className = "vidx";
      idx.textContent = (i + 1) + ".";
      var nm = document.createElement("span");
      nm.className = "vname";
      nm.textContent = p.split(/[\\/]/).pop();
      nm.title = p;
      row.appendChild(idx);
      row.appendChild(nm);

      function mover(label, delta, disabled) {
        var b = document.createElement("button");
        b.className = "btn small ghost vmove";
        b.textContent = label;
        b.disabled = disabled;
        b.addEventListener("click", function () {
          var t = selectedVideos[i];
          selectedVideos[i] = selectedVideos[i + delta];
          selectedVideos[i + delta] = t;
          selectedVideo = selectedVideos[0];
          renderVideoList();
        });
        return b;
      }
      row.appendChild(mover("↑", -1, i === 0));
      row.appendChild(mover("↓", 1, i === selectedVideos.length - 1));

      var del = document.createElement("button");
      del.className = "btn small ghost vmove";
      del.textContent = "✕";
      del.addEventListener("click", function () {
        selectedVideos.splice(i, 1);
        selectedVideo = selectedVideos[0] || null;
        renderVideoList();
        $("run").disabled = !selectedVideo;
      });
      row.appendChild(del);
      box.appendChild(row);
    });
  }

  $("pick").addEventListener("click", function () {
    // 第一個參數 = 允許多選。錄長片被切成好幾個檔時,一次全選起來即可。
    var res = window.cep.fs.showOpenDialog(true, false,
      "選擇要剪輯的影片(可多選,會照檔名順序接成一支)", "",
      ["mp4", "mov", "mkv", "avi", "m4v"]);
    if (res && res.data && res.data.length) {
      useVideos(sortNatural(res.data));
      setStatus(selectedVideos.length > 1
        ? "已選擇 " + selectedVideos.length + " 個檔,會接成一支處理。"
          + "確認一下順序對不對,再按開始"
        : "已選擇影片,可以開始自動剪輯");
    }
  });

  // ---------- 用 Premiere 裡選取的素材 ----------
  var VIDEO_EXT_RE = /\.(mp4|mov|mkv|avi|m4v|mxf|mts|m2ts|wmv)$/i;

  function useVideo(p, note) {
    useVideos([p], note);
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
        setStatus("這是本工具產生的影片(01_clean_av),不是原始錄影檔。"
          + "請改選你自己錄的原片", "err");
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
    // 教學類型編輯器是「搬」到類型欄位底下的(才會就近出現,不用捲到最下面),
    // 所以重畫表單前要先把它移回安全的地方 —— 不然下一行的 innerHTML=""
    // 會把它連同裡面的按鈕一起清掉,之後就再也叫不出來。
    $("settingsBody").appendChild($("vocabEditor"));
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

    // 註:以前這裡還有一個 "combo"(可打字下拉)型別,沒有任何欄位在用,
    // 而它是拿 <datalist> 做的 —— 那個元素在 CEP 的舊瀏覽器核心不可靠
    // (實測會整個不顯示)。留著等於埋一個陷阱給下一個加欄位的人:
    // 他看到有這個型別就會用,然後花很久查「為什麼下拉是空的」。
    // 要可打字的欄位請用 list 或 select。

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
                pi.title = "這個外掛沒有視窗介面,請用下方「降噪:消除對象 / 強度」調整";
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
      // 「編輯類型」:內建詞庫不見得剛好收到你常講的詞,讓你自己改、自己開新類型
      var edit = document.createElement("span");
      edit.className = "cat edit";
      edit.addEventListener("click", toggleVocabEditor);
      input.appendChild(edit);
      vocabChip = edit;
      syncVocabChip(vocabEditorOpen());
      controls[f.key] = function () { return picked.slice(); };
    }

    wrap.appendChild(label);
    wrap.appendChild(input);
    if (f.hint) {
      var h = document.createElement("div");
      h.className = "fhint"; h.textContent = f.hint;
      wrap.appendChild(h);
    }
    // 編輯器就近放在類型晶片底下:展開時一眼就看得到,
    // 不會像放在表單最後面那樣「按了好像沒反應」(其實是開在螢幕外)。
    if (f.type === "category") wrap.appendChild($("vocabEditor"));
    return wrap;
  }

  // ---------- 教學類型編輯器 ----------
  // 你改的詞存進 config/vocab_local.json(不進版控,更新專案不會被蓋)。
  // 同名 = 蓋掉內建那一類;新名字 = 多一類。內建那份永遠留著,隨時還原得回去。
  var VOCAB_FILE = function () {
    return path.join(PROJECT_DIR, "config", "vocab_local.json");
  };

  function readVocabLocal() {
    try {
      if (fs.existsSync(VOCAB_FILE())) {
        return JSON.parse(fs.readFileSync(VOCAB_FILE(), "utf8")) || {};
      }
    } catch (e) {}
    return {};
  }

  function writeVocabLocal(obj) {
    fs.writeFileSync(VOCAB_FILE(), JSON.stringify(obj, null, 2), "utf8");
  }

  // 估算提示詞長度。權重與上限都是從 Python 那邊帶過來的(ui_settings 的
  // vocab_budget),不在這裡自己抄一份數字 —— 抄了就會有「面板說放得下、
  // 實際卻被模型砍掉」這種查不出來的落差。
  function estTokens(s) {
    var b = (settingsData && settingsData.vocab_budget) || {};
    var a = b.ascii || 0.5, c = b.cjk || 1.4, n = 0;
    for (var i = 0; i < s.length; i++) {
      n += s.charCodeAt(i) < 128 ? a : c;
    }
    return Math.floor(n + 0.5);
  }

  function parseWords(text) {
    return String(text || "").split(/[\n,,、]+/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
  }

  function vocabSay(msg, good) {
    var el = $("vocabMsg");
    el.textContent = msg || "";
    el.style.color = good ? "#2e8b57" : "#e06c6c";
  }

  // 邊打邊算:這一類單獨勾選時,提示詞會用掉多少、還剩多少。
  // 超過上限的部分 Whisper 直接看不到,而且不會有任何錯誤訊息,
  // 所以一定要在使用者還在打字的時候就講清楚。
  function updateVocabBudget() {
    var b = (settingsData && settingsData.vocab_budget) || {};
    var total = b.total || 223;
    var fixed = (b.demo || 0) + (b.wrapper || 0);
    var words = parseWords($("vocabWords").value);
    var used = fixed + estTokens(words.join("、"));
    var left = total - used;
    var el = $("vocabBudget");
    el.textContent = words.length + " 個詞,約用掉 " + used + " / " + total
      + " 額度(固定開銷 " + fixed + "),還剩 " + left;
    el.className = "budget" + (left < 0 ? " over" : (left < 10 ? " tight" : ""));
    if (left < 0) {
      el.textContent += " ⚠ 太長了,排在後面的詞模型會看不到";
    } else if (left < 10) {
      el.textContent += " ⚠ 很擠,再加詞就會被砍";
    }
  }

  function fillVocabPicker(keep) {
    var sel = $("vocabPick");
    var vocab = (settingsData && settingsData.vocab_presets) || {};
    var builtin = (settingsData && settingsData.builtin_vocab) || {};
    sel.innerHTML = "";
    Object.keys(vocab).forEach(function (name) {
      var op = document.createElement("option");
      op.value = name;
      op.textContent = name + (name in builtin ? "" : "(我的)");
      sel.appendChild(op);
    });
    if (keep && vocab[keep]) sel.value = keep;
  }

  function loadVocabInto(name) {
    var vocab = (settingsData && settingsData.vocab_presets) || {};
    $("vocabWords").value = (vocab[name] || []).join("、");
    updateVocabBudget();
    vocabSay("");
  }

  // 「編輯類型」那顆晶片要看得出來現在是開還是關 —— 不然使用者按了之後
  // 會不確定到底有沒有反應(尤其編輯器展開在下面、被其他欄位擠出視線時)。
  var vocabChip = null;
  function syncVocabChip(open) {
    if (!vocabChip) return;
    vocabChip.textContent = open ? "▾ 收起編輯" : "✎ 編輯類型";
    if (open) vocabChip.classList.add("open");
    else vocabChip.classList.remove("open");
  }

  function vocabEditorOpen() {
    return $("vocabEditor").className.indexOf("open") >= 0;
  }

  function setVocabEditorOpen(open) {
    var box = $("vocabEditor");
    if (open) box.classList.add("open");
    else box.classList.remove("open");
    syncVocabChip(open);
  }

  // 回傳「切換後是不是展開狀態」,讓晶片文字跟著同步
  function toggleVocabEditor() {
    if (!settingsData) return false;
    if (vocabEditorOpen()) {
      setVocabEditorOpen(false);
      return false;
    }
    setVocabEditorOpen(true);
    var cur = (controls["VOCAB_CATEGORIES"] ?
      controls["VOCAB_CATEGORIES"]() : [])[0];
    fillVocabPicker(cur);
    loadVocabInto($("vocabPick").value);
    // 展開在畫面外等於沒展開,捲進來讓它一定看得到
    try { $("vocabEditor").scrollIntoView({ block: "nearest" }); } catch (e) {}
    return true;
  }

  $("vocabPick").addEventListener("change", function () {
    loadVocabInto(this.value);
  });
  $("vocabWords").addEventListener("input", updateVocabBudget);

  $("vocabNew").addEventListener("click", function () {
    var name = window.prompt("新類型的名字(例如:直播、烘焙、木工):", "");
    if (!name) return;
    name = String(name).trim();
    if (!name) return;
    if ((settingsData.vocab_presets || {})[name]) {
      vocabSay("已經有「" + name + "」這一類了,直接在上面選它來改", false);
      return;
    }
    settingsData.vocab_presets[name] = [];
    fillVocabPicker(name);
    $("vocabWords").value = "";
    updateVocabBudget();
    vocabSay("新類型「" + name + "」已建立,把詞填進去再按儲存", true);
  });

  $("vocabSave").addEventListener("click", function () {
    var name = $("vocabPick").value;
    if (!name) return;
    var words = parseWords($("vocabWords").value);
    var mine = readVocabLocal();
    mine[name] = words;
    try {
      writeVocabLocal(mine);
    } catch (e) {
      vocabSay("存不起來:" + e.message, false);
      return;
    }
    settingsData.vocab_presets[name] = words;
    if ((settingsData.categories_available || []).indexOf(name) < 0) {
      settingsData.categories_available.push(name);
      renderForm();                 // 新類型要在晶片列出現
      setVocabEditorOpen(true);     // renderForm 會重畫晶片,重新開回來
      fillVocabPicker(name);
    }
    vocabSay("已儲存「" + name + "」(" + words.length + " 個詞)"
      + ",下次剪輯自動重新辨識", true);
  });

  $("vocabReset").addEventListener("click", function () {
    var name = $("vocabPick").value;
    var builtin = (settingsData.builtin_vocab || {})[name];
    if (!builtin) {
      vocabSay("「" + name + "」是你自己新增的,沒有內建版本可以還原", false);
      return;
    }
    var mine = readVocabLocal();
    delete mine[name];
    try { writeVocabLocal(mine); } catch (e) {
      vocabSay("還原失敗:" + e.message, false); return;
    }
    settingsData.vocab_presets[name] = builtin.slice();
    $("vocabWords").value = builtin.join("、");
    updateVocabBudget();
    vocabSay("「" + name + "」已還原成內建的 " + builtin.length + " 個詞", true);
  });

  $("vocabDel").addEventListener("click", function () {
    var name = $("vocabPick").value;
    if ((settingsData.builtin_vocab || {})[name]) {
      vocabSay("內建類型不能刪(可以按「還原成內建」,或不要勾選它)", false);
      return;
    }
    var mine = readVocabLocal();
    delete mine[name];
    try { writeVocabLocal(mine); } catch (e) {
      vocabSay("刪不掉:" + e.message, false); return;
    }
    delete settingsData.vocab_presets[name];
    settingsData.categories_available =
      (settingsData.categories_available || []).filter(function (n) {
        return n !== name;
      });
    renderForm();
    setVocabEditorOpen(true);
    fillVocabPicker();
    loadVocabInto($("vocabPick").value);
    vocabSay("已刪除「" + name + "」", true);
  });

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
    say("外掛介面開啟中…調整後關閉視窗即儲存", true);
    var proc = cp.spawn(PYTHON, ["vst_tool.py", "open", p.trim()], { cwd: PROJECT_DIR });
    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });
    proc.on("error", function (e) { say("無法啟動:" + e.message, false); });
    proc.on("close", function (code) {
      if (code === 0) { say("VST 參數已儲存 ✓,下次剪輯自動套用", true); }
      else if (code === 2) { say("這個外掛沒有視窗介面,請改用下方「降噪:消除對象 / 強度」調整", false); }
      else { say("調整結束(代碼 " + code + ",見下方訊息)", false); }
    });
  }

  // ---------- 一鍵自動剪輯 ----------
  $("run").addEventListener("click", function () {
    if (!selectedVideo) return;
    saveSettings(function () { runPipeline(); }, "saveMsg");
  });

  // ---------- 停止 ----------
  // 停掉正在跑的那一個。凡是會跑比較久的 Python 步驟都要經過 track():
  // 一鍵剪輯、重算剪輯、用目前序列產生字幕 —— 三個都算。
  // (產字幕以前是用 execFile 起的,沒有 track,停止鈕根本不會出現,
  //  而這行註解卻已經寫著「產字幕也算」了。註解說謊比沒有註解更糟。)
  // 按鈕先鎖住:taskkill 收整棵樹要一點時間,連按只會讓人以為沒反應。
  $("stop").addEventListener("click", function () {
    if (!running) return;
    stopping = true;
    $("stop").disabled = true;
    setStatus("停止中…", "busy");
    appendLog("\n■ 已要求停止,正在收掉 Python 與它開的 ffmpeg…\n");
    killTree(running);
  });

  function runPipeline() {
    $("run").disabled = true;
    $("pick").disabled = true;
    $("log").textContent = "";
    logBuf = "";
    hideProgress();
    setStatus("處理中…(第一次要下載模型,較久)", "busy");
    appendLog("▶ 已啟動,正在載入程式與模型…(這段沒動靜是正常的)\n");

    var videos = selectedVideos.slice();
    var name = outputNameOf(videos);
    // -u = 不緩衝輸出:Python 的進度訊息才會「即時」出現在下面,
    // 不然會累積到一大段才一次噴出來,看起來像當掉。
    // 多個檔一起傳進去,Python 那邊會接成一支處理(見 modules/sources.py)。
    var proc = track(cp.spawn(PYTHON,
      ["-u", "pipeline.py"].concat(videos), { cwd: PROJECT_DIR }));
    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });
    proc.on("error", function (e) {
      setStatus("無法啟動 Python,詳見下方訊息", "err");
      appendLog(pythonFailMsg(e) + "\n");
      $("run").disabled = false; $("pick").disabled = false;
    });
    proc.on("close", function (code) {
      $("pick").disabled = false;
      // 使用者自己按停止的,不是失敗:不要跳錯誤說明、不要嗶錯誤音,
      // 那會讓人以為是程式壞了。
      if (stopping) {
        setStatus("已停止", "");
        appendLog("\n■ 已停止。這次沒有產出序列;"
          + "再按一次「一鍵自動剪輯」會從頭跑,"
          + "已經辨識完的部分會沿用,不必重來。\n");
        $("run").disabled = false;
        return;
      }
      if (code !== 0) {
        setStatus("處理失敗,說明在下方", "err");
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
          rememberVideo(videos);
          runAutoSteps(videos, function (extra) {
            setStatus(base + extra, "ok");
          });
        } else {
          setStatus("Python 跑完了,但匯入時出錯:" + r, "err");
          rememberVideo(videos);
        }
        $("run").disabled = false;
      });
    });
  }

  // =====================================================================
  //  剪輯後工具 —— 活專案的「隨時可改」按鈕(P3/P4/P5)
  // =====================================================================

  // output/ 底下的資料夾名稱。**必須跟 Python 的 sources.VideoSource.name
  // 算出完全一樣的字串** —— 面板是靠這個名字去找報告、字幕、XML 的。
  // 對不上的話面板會說「找不到報告」,而 Python 那邊其實好好地產出來了,
  // 只是放在另一個名字的資料夾裡。
  function outputNameOf(paths) {
    var list = [].concat(paths);
    var stem = path.basename(list[0], path.extname(list[0]));
    return list.length > 1 ? stem + "_合併" + list.length + "支" : stem;
  }

  function outDirOf(video) {
    return path.join(PROJECT_DIR, "output", outputNameOf(video));
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

  // 記住最近處理的影片:跑完出現「剪輯後工具」,面板重開也還在。
  //
  // ⚠️ 要記「整份清單」而不是只記第一個檔。「重算剪輯」是重跑一次
  // pipeline.py,只傳第一個檔的話,重算出來的是「只有第一段」的短片,
  // 但按鈕上寫的是「用新設定重算」—— 你會以為是設定改壞了。
  function rememberVideo(videos) {
    var list = [].concat(videos || []).filter(Boolean);
    if (!list.length) return;
    lastVideos = list;
    try {
      window.localStorage.setItem("pr_last_videos", JSON.stringify(list));
    } catch (e) {}
    $("afterSec").style.display = "block";
    $("afterVideo").textContent = "目前影片:"
      + (list.length > 1
         ? path.basename(list[0]) + " 等 " + list.length + " 個檔(已接成一支)"
         : path.basename(list[0]));
  }

  (function restoreLast() {
    var list = null;
    try {
      var raw = window.localStorage.getItem("pr_last_videos");
      if (raw) list = JSON.parse(raw);
      // 舊版只存單一路徑,升級上來的人要接得住
      if (!list) {
        var one = window.localStorage.getItem("pr_last_video");
        if (one) list = [one];
      }
    } catch (e) {}
    if (!list || !list.length) return;
    var allThere = list.every(function (p) { return fs.existsSync(p); });
    if (!allThere || !fs.existsSync(outDirOf(list))) return;
    rememberVideo(list);
    // 順便接續上次的選擇:重開面板不用重選,直接就能重跑或用剪輯後工具
    useVideos(list, list.length > 1 ? "" : "(上次處理的影片,可直接用)");
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
    if (!lastVideos.length) return;
    setAfterButtons(false);
    afterSay("清除中…", true);
    cp.execFile(PYTHON,
      ["-m", "modules.workspace", "clear", outDirOf(lastVideos), "asr"],
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
    if (!lastVideos.length) return;
    openReportFor(lastVideos, false);
  });

  // ---------- P3:重算剪輯(快)→ 匯入新序列 ----------
  $("rebuild").addEventListener("click", function () {
    if (!lastVideos.length) return;
    setAfterButtons(false);
    // 文案不能寫死「不重跑辨識」:改了辨識或聲音設定時,程式會自動重跑
    // 那一段(要幾分鐘)。講死了你會以為當掉。
    afterSay("重算中…(一般幾秒;改過辨識或聲音設定的話會自動重跑那一段,"
      + "需要幾分鐘)", true);
    logBuf = "";
    appendLog("▶ 重算已啟動:用新設定重新決策。\n"
      + "  (剪輯類設定=幾秒;辨識或聲音類設定有改動=自動重跑該步驟,較久)\n");
    saveSettings(function () {
      // --stamp:序列名加時間。重算刻意保留舊序列讓你比較,
      // 全部同名就分不出哪條是剛剛那次了。
      var proc = track(cp.spawn(PYTHON,
        ["-u", "pipeline.py"].concat(lastVideos)
          .concat(["--skip-audio", "--stamp"]),
        { cwd: PROJECT_DIR }));
      proc.stdout.on("data", function (d) { appendLog(d.toString()); });
      proc.stderr.on("data", function (d) { appendLog(d.toString()); });
      proc.on("error", function (e) {
        afterSay("無法啟動 Python,詳見下方訊息", false);
        appendLog(pythonFailMsg(e) + "\n");
        setAfterButtons(true);
      });
      proc.on("close", function (code) {
        if (stopping) {
          afterSay("已停止,舊序列沒有被動到", true);
          setAfterButtons(true); return;
        }
        if (code !== 0) {
          afterSay("重算失敗,說明在下方", false);
          explainInto();
          beep(false);
          setAfterButtons(true); return;
        }
        var outDir = outDirOf(lastVideos);
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
              runAutoSteps(lastVideos, function (extra, ok) {
                afterSay(base + extra, ok !== false);
              });
            } else { afterSay("重算完成,但匯入出錯:" + r, false); }
            setAfterButtons(true);
          });
      });
    });
  });

  // ---------- P4:幫目前序列掛人聲處理(QE 實驗;失敗教用音軌混音器) ----------
  // 預設掛 Premiere 內建的降噪 -> EQ -> 壓縮器:人人都有、不必安裝、
  // 純 CPU 不吃顯卡記憶體,而且就是「基本音效 > 對話」在做的那三件事。
  var VOICE_FX_FALLBACK = [
    ["DeNoise", "消除雜訊", "降噪"],
    ["Parametric Equalizer", "參數等化器", "參數式等化器"],
    ["Dynamics", "Dynamics Processing", "動態", "動態處理"]
  ];

  // 設定裡的效果鏈壓成 ExtendScript 吃的字串:效果之間 ||、候選名稱之間 |
  // 這串會被塞進 evalScript 的字串引數裡,所以反斜線和雙引號要先跳脫
  // ——效果名稱現在是使用者可以自己改的,不能假設裡面很乾淨。
  function voiceChainOf(values) {
    var chain = (values && values.PREMIERE_VOICE_FX) || VOICE_FX_FALLBACK;
    var out = [];
    for (var i = 0; i < chain.length; i++) {
      // 允許只寫一個名字的簡寫寫法
      var c = chain[i];
      out.push((typeof c === "string" ? [c] : c).join("|"));
    }
    return out.join("||").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function fxLabelsOf(values) {
    var chain = (values && values.PREMIERE_VOICE_FX) || VOICE_FX_FALLBACK;
    var out = [];
    for (var i = 0; i < chain.length; i++) {
      var c = chain[i];
      out.push(typeof c === "string" ? c : c[0]);
    }
    return out.join(" → ");
  }

  var MIXER_HINT = "建議改用音軌混音器:A1 軌效果插槽依序選 DeNoise → "
    + "參數等化器 → 動態(插槽有五格,三個放得下)。"
    + "整軌一次搞定,片段再多都一樣快,隨時可調或關掉比較。";

  // 把人聲處理掛到目前序列的每個片段。done(成功與否, 給人看的訊息)
  //
  // ⚠️ 這是「每個片段各掛一組」。片段一多,再乘上鏈裡的三個效果,
  // 時間軸會明顯變頓,所以會先問 Premiere 有幾個片段,超過上限就拒絕。
  function applyVoiceFx(values, done) {
    var max = values && values.DENOISE_PER_CLIP_MAX;
    if (typeof max !== "number") max = 20;
    // 音樂段不掛(降噪是衝著人聲設計的,會把音樂當噪音削掉)
    cs.evalScript('prApplyVoiceChain("' + voiceChainOf(values) + '","音樂",'
      + max + ')',
      function (r) {
        if (r && r.indexOf("OK") === 0) {
          var p = r.split(" ");
          done(true, "人聲處理已掛到 " + p[1] + " 個聲音片段 ✓ "
            + "(" + String(p[3] || "").split("|").join(" → ") + ")"
            + "。到「效果控制」隨時調整,不滿意可 Ctrl+Z 復原");
        } else if (r && r.indexOf("TOOMANY") === 0) {
          done(false, "這條序列有 " + r.split(" ")[1] + " 個片段,"
            + "一個一個掛會產生好幾千個效果實例,時間軸會變得很頓,"
            + "所以沒有動手。" + MIXER_HINT);
        } else if (r && r.indexOf("NOFX") === 0) {
          // 效果名稱會跟著 Premiere 的介面語言翻譯,對不上就把這台實際有的
          // 名稱印到訊息區,才看得出是名字不同還是真的沒這個效果
          var miss = String(r.substring(4) || "").split("|").join("、");
          cs.evalScript("prListAudioEffects()", function (list) {
            if (list && list.indexOf("OK ") === 0) {
              appendLog("\n這台 Premiere 有的音訊效果:\n"
                + list.substring(3).split("|").join("、") + "\n");
            }
            done(false, "在你的 Premiere 裡找不到這些效果:" + miss
              + "(效果名稱會跟著介面語言不同)。可用的效果清單已印在下方訊息區,"
              + "把正確的名字填進設定的 PREMIERE_VOICE_FX 就會認得。" + MIXER_HINT);
          });
        } else {
          done(false, "掛效果失敗:" + r + " " + MIXER_HINT);
        }
      });
  }

  $("applyVst").addEventListener("click", function () {
    setAfterButtons(false);
    withValues(function (v) {
      afterSay("嘗試把人聲處理掛到目前序列(" + fxLabelsOf(v) + ")…", true);
      applyVoiceFx(v, function (ok, msg) {
        afterSay(msg, ok);
        setAfterButtons(true);
      });
    });
  });

  // ---------- 剪完之後自動接手的事 ----------
  // 兩件本來都要你手動按的事:打開報告、把人聲處理掛上去。
  // 人聲處理只有在「沒烘進音檔」時才需要掛 —— 那種情況下新序列是原始聲音,
  // 不掛等於沒處理,而每剪一次就要手動按一次實在很煩。
  function runAutoSteps(video, say) {
    withValues(function (v) {
      if (!v) return;
      if (v.AUTO_OPEN_REPORT !== false) openReportFor(video, true);

      // 沒烘進音檔時,新序列聽到的是原始錄音 —— 提醒你掛一次。
      // 判斷只看「有沒有烘進去」:要掛什麼是 Premiere 那邊的事,
      // 跟設定裡有沒有填 VST 路徑無關(預設根本不用 VST,用內建效果)。
      // 刻意「不」自動掛:那是每片段一組,片段一多時間軸會變頓。
      var wantsVoiceFx = v.AUDIO_MODE === "vst" && v.VST_BAKE === false;
      if (wantsVoiceFx) say(";聲音還沒處理 —— " + MIXER_HINT, true);
    });
  }

  // ---------- P5:用目前序列的實際版面產生字幕 ----------
  $("subsFromSeq").addEventListener("click", function () {
    if (!lastVideos.length) return;
    setAfterButtons(false);
    afterSay("讀取目前序列的版面…", true);
    var outDir = outDirOf(lastVideos);
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
      // 用 spawn + track 而不是 execFile:track 才會讓「停止」鈕出現、
      // 也才停得掉。長片的字幕對位要跑一陣子,按了停止卻沒反應
      // 跟當掉沒兩樣(以前這裡是 execFile,停止鈕根本不會出現,
      // 而旁邊的註解還寫著「產字幕也算」)。
      var proc = track(cp.spawn(PYTHON,
        ["-u", "-m", "modules.live_subs", layout, outDir],
        { cwd: PROJECT_DIR }));
      proc.stdout.on("data", function (d) { appendLog(d.toString()); });
      proc.stderr.on("data", function (d) { appendLog(d.toString()); });
      proc.on("error", function (e) {
        afterSay("無法啟動 Python,詳見下方訊息", false);
        appendLog(pythonFailMsg(e) + "\n");
        setAfterButtons(true);
      });
      proc.on("close", function (code) {
          // 停止分支一定要排在離開碼判斷「前面」:被 taskkill 收掉的行程
          // 離開碼也不是 0,不先分辨就會把「使用者按停止」報成失敗
          if (stopping) {
            afterSay("已停止,字幕沒有重新產生(原本的還在)", true);
            setAfterButtons(true); return;
          }
          if (code !== 0) {
            afterSay("字幕對位失敗,說明在下方", false);
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
