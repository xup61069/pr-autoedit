/*
 * 在 Premiere Pro 內執行的 ExtendScript。
 * 面板跑完 Python 後,呼叫這裡把剪好的專案(FCP7 XML)與字幕匯入目前專案。
 */

function prImportEditedProject(xmlPath, srtPath) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案,請先新建或開啟一個專案";
        }
        var proj = app.project;

        var xmlFile = new File(xmlPath);
        if (!xmlFile.exists) {
            return "ERROR: 找不到剪輯專案檔:" + xmlPath;
        }

        // 匯入 FCP7 XML(會建立一個剪好的序列)
        // importFiles(paths, suppressUI, targetBin, importAsNumberedStills)
        proj.importFiles([xmlPath], true, proj.rootItem, false);

        // 有字幕的話一併匯入(失敗不影響主流程)
        if (srtPath) {
            var srtFile = new File(srtPath);
            if (srtFile.exists) {
                try {
                    proj.importFiles([srtPath], true, proj.rootItem, false);
                } catch (e2) { /* 字幕匯入失敗就略過 */ }
            }
        }

        return "OK";
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}
