# Master-Token 認證操作步驟(Headless 環境)

## 背景

`notebooklm-py` 的 `master_token.json` 是**長期有效的全帳戶憑證**(不會像 cookie 一樣過期,
且可以從中重新 mint 出 session cookie)。因此唯一需要瀏覽器的一次性動作,
放在**有桌面環境的機器**上做,之後把 token 檔案 scp 到這台 headless VM 即可。

⚠️ **安全性警告**: master token 是完整帳戶的持久憑證,能 mint 多種 Google 服務的 OAuth。
**請使用專用的 Google 帳號**(不是你的主帳號)。token 若外洩,唯一補救是
Google 帳戶 → 安全性 → 你的裝置 → 移除該裝置/工作階段。

## Step 1 — 在有桌面環境的機器執行

### Windows 版(你的桌面是 Windows)

在 PowerShell(或 cmd)執行:

```powershell
# 0. 若尚未安裝 Python: 到 python.org 安裝 3.11+,或
#    winget install Python.Python.3.12

# 1. 安裝含 headless(bootstrap token)與 browser(開瀏覽器)的 extras
pip install "notebooklm-py[headless,browser]"

# 2. 安裝 Playwright 瀏覽器(login 開啟瀏覽器需要)
playwright install chromium

# 3. 產生 master token(注意: 一般 `notebooklm login` 不會產生
#    master_token.json,一定要加 --master-token 旗標)
notebooklm login --master-token --account <你的Google帳號@gmail.com>
```

執行時會自動開啟瀏覽器(Google 的 EmbeddedSetup 流程),完成登入授權後,
CLI 會把 token 寫到 **Windows 路徑**:

```
C:\Users\<你的Windows使用者>\.notebooklm\profiles\default\master_token.json
```

驗證檔案存在:

```powershell
dir C:\Users\<你的Windows使用者>\.notebooklm\profiles\default\master_token.json
```

> 如果 `notebooklm` 指令找不到,改用完整路徑執行:
> `python -m notebooklm.notebooklm_cli login --master-token --account ...`(或重新開 PowerShell 讓 PATH 生效)

### Unix 版(若改用其他有桌面的 Linux/Mac)

```bash
pip install "notebooklm-py[headless,browser]"
playwright install chromium
notebooklm login --master-token --account <你的Google帳號@gmail.com>
# token 寫到 ~/.notebooklm/profiles/default/master_token.json
```

## Step 2 — 複製到 Ubuntu VM

### Windows → VM(內建 OpenSSH 的 scp)

在 PowerShell(或 cmd)執行 — 本機路徑用**雙引號包住**:

```powershell
scp "C:\Users\<你的Windows使用者>\.notebooklm\profiles\default\master_token.json" `
    <vm-使用者>@<vm-IP>:~/.notebooklm/profiles/default/master_token.json
```

(cmd 則把尾端 `` ` `` 換成 `^` 或不換行直接打成一整行)

> 若 VM 的 SSH 沒開: `sudo apt install openssh-server && sudo systemctl enable --now ssh`
> 若不想用命令列: 用 **WinSCP** 或 **MobaXterm**(圖形介面)拖曳檔案到
> `/home/<vm-使用者>/.notebooklm/profiles/default/` 即可

### 在 VM 上執行(確保目錄與權限正確)

```bash
mkdir -p ~/.notebooklm/profiles/default
chmod 600 ~/.notebooklm/profiles/default/master_token.json
```

## Step 3 — 在 VM 上 bootstrap session cookie

> 注意: 若環境變數 `NOTEBOOKLM_MASTER_TOKEN_JSON` 有設定,`login` 會拒絕執行,
> 必須先 `unset`。

```bash
unset NOTEBOOKLM_MASTER_TOKEN_JSON
notebooklm login --master-token-refresh   # 從 master token mint 出 storage_state.json
notebooklm auth check --test --json       # 預期 "status": "ok"
```

或者直接執行自動化腳本(它會自動完成 Step 3 的所有檢查):

```bash
source /home/ian/github-project/notebooklm-py/.venv/bin/activate
python /home/ian/github-project/notebooklm-py/scripts/check_auth.py
```

## 錯誤排除

| 症狀 | 原因 / 處理 |
|---|---|
| `login` 拒絕執行 | 環境變數 `NOTEBOOKLM_MASTER_TOKEN_JSON` 被設定 → 先 `unset` |
| `auth check --test` 回傳非 ok | token 與帳號不符、或 cookie 尚未 refresh → 重跑 Step 3 |
| `--account` 帳號不符被拒 | profile 中已有別的帳號 session → 用 `-p <新profile名>` 或 `--force` |
| scp 後權限錯誤 | `chmod 600` 未執行 → 重跑 Step 2 的 VM 端指令 |

## 之後每次要重跑 POC

master token 不輪換,所以 **Step 1–2 只需要做一次**。
之後每次執行前只需:

```bash
notebooklm login --master-token-refresh && python scripts/check_auth.py
```
