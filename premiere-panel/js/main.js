(function () {
  "use strict";

  // =====================================================================
  //  設定 —— 依你的電腦調整這兩行(其餘不用動)
  // =====================================================================
  //  PROJECT_DIR : pr-autoedit 專案資料夾(裡面有 pipeline.py)
  //  PYTHON      : Python 執行檔完整路徑
  var PROJECT_DIR = "C:\\pr-autoedit";
  var PYTHON = "C:\\Users\\Administrator\\miniconda3\\python.exe";
  // =====================================================================

  var cs = new CSInterface();
  var selectedVideo = null;

  var $pick = document.getElementById("pick");
  var $run = document.getElementById("run");
  var $videoPath = document.getElementById("videoPath");
  var $status = document.getElementById("status");
  var $log = document.getElementById("log");

  function setStatus(t) { $status.textContent = t; }
  function appendLog(t) {
    $log.textContent += t;
    $log.scrollTop = $log.scrollHeight;
  }
  function toFwd(p) { return String(p).replace(/\\/g, "/"); }

  // --- 1. 選擇影片 ---
  $pick.addEventListener("click", function () {
    var res = window.cep.fs.showOpenDialog(
      false, false, "選擇要剪輯的影片", "",
      ["mp4", "mov", "mkv", "avi", "m4v"]
    );
    if (res && res.data && res.data.length) {
      selectedVideo = res.data[0];
      $videoPath.textContent = selectedVideo;
      $run.disabled = false;
      setStatus("已選擇影片,可以開始自動剪輯");
    }
  });

  // --- 2. 一鍵自動剪輯 ---
  $run.addEventListener("click", function () {
    if (!selectedVideo) { return; }
    $run.disabled = true;
    $pick.disabled = true;
    $log.textContent = "";
    setStatus("處理中,請稍候…(第一次會下載模型,較久)");

    var cp = require("child_process");
    var path = require("path");
    var name = path.basename(selectedVideo, path.extname(selectedVideo));

    var proc = cp.spawn(PYTHON, ["pipeline.py", selectedVideo], {
      cwd: PROJECT_DIR
    });

    proc.stdout.on("data", function (d) { appendLog(d.toString()); });
    proc.stderr.on("data", function (d) { appendLog(d.toString()); });

    proc.on("error", function (err) {
      setStatus("無法啟動 Python:" + err.message +
                "(請檢查 main.js 裡的 PYTHON / PROJECT_DIR 路徑)");
      $run.disabled = false;
      $pick.disabled = false;
    });

    proc.on("close", function (code) {
      $pick.disabled = false;
      if (code !== 0) {
        setStatus("處理失敗(代碼 " + code + "),請看下方訊息");
        $run.disabled = false;
        return;
      }
      setStatus("剪輯完成,正在匯入 Premiere…");
      var outDir = path.join(PROJECT_DIR, "output", name);
      var xml = toFwd(path.join(outDir, "04_project.xml"));
      var srt = toFwd(path.join(outDir, "04_subtitles.srt"));

      cs.evalScript(
        'prImportEditedProject("' + xml + '","' + srt + '")',
        function (result) {
          if (result && result.indexOf("OK") === 0) {
            setStatus("完成 ✓ 已匯入剪好的序列與字幕,請在 Premiere 審閱 marker");
          } else {
            setStatus("Python 跑完了,但匯入時出錯:" + result);
          }
          $run.disabled = false;
        }
      );
    });
  });
})();
