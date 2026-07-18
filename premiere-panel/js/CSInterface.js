/*
 * 最小版 CSInterface —— 只保留本面板需要的功能:
 *   evalScript   : 在 Premiere 內執行 ExtendScript(用來匯入結果)
 *   getSystemPath: 取得擴充自身路徑
 * CEP 會在載入面板時把 window.__adobe_cep__ 注入,這裡只是薄薄一層包裝。
 */
function CSInterface() {}

CSInterface.prototype.evalScript = function (script, callback) {
  if (typeof callback !== "function") {
    callback = function () {};
  }
  window.__adobe_cep__.evalScript(script, callback);
};

CSInterface.prototype.getSystemPath = function (pathType) {
  var p = window.__adobe_cep__.getSystemPath(pathType);
  try { p = decodeURIComponent(p); } catch (e) {}
  return p;
};

// 常用的 SystemPath 代號
var SystemPath = {
  EXTENSION: "extension",
  USER_DATA: "userData",
  MY_DOCUMENTS: "myDocuments"
};
