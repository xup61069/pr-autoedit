# 如何把專案推上 GitHub(逐步版)

寫給「知道 GitHub 是什麼、但沒實際推過專案」的人。照著做,每一步都有指令。

---

## 一次性準備

### 1. 裝 Git
到 https://git-scm.com/download/win 下載安裝,一路下一步即可。
裝完打開 cmd,輸入確認:
```
git --version
```

### 2. 設定你的 Git 身分(只做一次)
```
git config --global user.name "你的名字"
git config --global user.email "你的GitHub註冊信箱"
```

### 3. 在 GitHub 建一個空的 repository
1. 登入 github.com,右上角 + → New repository
2. Repository name 填 `pr-autoedit`
3. **不要**勾選 "Add a README"、"Add .gitignore"、"Add license"
   (我們專案裡已經有了,勾了會衝突)
4. 按 Create repository
5. 建好後頁面會顯示一串網址,像 `https://github.com/你的帳號/pr-autoedit.git`,
   等下會用到,先複製起來

---

## 把專案推上去

在 cmd 進入專案資料夾:
```
cd /d D:\pr-autoedit
```

依序執行以下指令(一行一行來):

```
git init
git add .
git commit -m "初始版本:PR 自動剪輯工具"
git branch -M main
git remote add origin https://github.com/你的帳號/pr-autoedit.git
git push -u origin main
```

最後一步會跳出登入視窗,用瀏覽器授權你的 GitHub 帳號即可。
推完後重新整理 GitHub 頁面,就會看到所有檔案上去了。

---

## 之後每次改動後更新

不管是你自己改,還是叫 AI 幫你改完,更新到 GitHub 只要三行:

```
git add .
git commit -m "簡短描述這次改了什麼"
git push
```

---

## 重要:上傳前先開啟 LICENSE 改一個地方

打開 `LICENSE` 檔案,把第 3 行的 `<你的名字或 GitHub 帳號>` 換成你的名字,
再上傳。這是 MIT 授權要求的著作權標示。

---

## 常見問題

**push 時要我輸入帳號密碼,但密碼一直錯**
→ GitHub 現在不接受密碼,要用瀏覽器授權(第一次 push 會自動跳出),
   或用 Personal Access Token。最簡單是裝 Git 時內建的 Git Credential Manager
   會幫你處理,照跳出的視窗授權就好。

**不小心把 venv 或影片檔傳上去了**
→ 檢查 `.gitignore` 有沒有在專案根目錄。若已誤傳,執行:
   ```
   git rm -r --cached venv output
   git commit -m "移除不該版控的檔案"
   git push
   ```

**想讓別人一鍵下載**
→ 你的 repo 頁面右側 Releases → Create a new release,打個版本號(如 v1.0),
   GitHub 會自動打包成 zip 供人下載。
