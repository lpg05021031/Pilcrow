# RUNBOOK — 一頁版（2026-11-06）

從零到可開控制台，五步。之後所有維運都在 `http://<主機>:8080/console`。
歷史版本（S1 之前五個行程、十個 Phase 的那本）在 `docs/RUNBOOK-2026-08-legacy.md`，
只當考古用。

## 1. 安裝（一台乾淨 Linux，Python ≥ 3.10）

```bash
sudo useradd -r -s /usr/sbin/nologin svc-review          # 服務帳號（已有就跳過）
sudo git clone <這個 repo> /opt/pr-review-suite && cd /opt/pr-review-suite
sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements.txt semgrep ruff
sudo venv/bin/python -m shared.install                     # 建目錄、寫空設定、寫 unit、跑 doctor
```

`install` 做的事全部冪等：`/etc/pr-review-suite/{config.toml,secrets.env}`、
`/var/lib/pr-review-suite/`、`/etc/systemd/system/review-orchestrator.service`。
內網有 proxy 就在 pip 前加 `HTTPS_PROXY=…`。

## 2. 填 secrets，起服務

```bash
sudo vi /etc/pr-review-suite/secrets.env    # ADO_PAT、LANGFUSE_PUBLIC_KEY / SECRET_KEY；要登入控制台就設 ORCH_API_KEY
sudo systemctl daemon-reload && sudo systemctl enable --now review-orchestrator
```

secrets 只住這個檔（600）；其他旋鈕都在控制台改，不用再碰主機。
systemd EnvironmentFile 不支援行內註解，等號右邊不可有空白。

## 3. 設定精靈

開 `http://<主機>:8080/console/setup`，七步由上而下綠燈：LLM gateway → Langfuse →
Azure DevOps → 服務本體 → 本地 clone → 團隊規則 → 知識迴圈（選配）。
每一步就是 doctor 對應的檢查，旁邊「設定 <group>」直接跳到那一組表單，Apply 即生效。

- **本地 clone**：Sources 頁「新增 repo」貼 clone URL 即可（目錄名 = PR URL 裡
  `_git/<這段>` 的小寫）；之後 repo-sync job 每 10 分鐘保鮮。
- **團隊規則**：`RULES_ROOT` 指向規則 repo 的本機 clone（`rules/common/` 必載，
  `rules/<repo小寫>/` 命中才載）。Sources 頁可預覽某個 repo 會載到哪些檔。
- **知識迴圈**：`KNOWLEDGE_ROOT` 指向一顆本地 git repo（空的也行）；harvest job 每
  30 分鐘裁決與落地。節奏與 reject / keep 見 README §4。

## 4. 接上 Open WebUI

Open WebUI → Settings → Connections → OpenAI API：URL `http://<主機>:8080/v1`，
key = `ORCH_API_KEY`（未設則隨便填）。模型清單會出現 `pr-reviewer`。貼 PR 連結就會
review。Langfuse prompt 要先播種一次：`venv/bin/python -m eval.seed_langfuse`。

## 5. 日常

| 想知道 | 去哪 |
|---|---|
| 系統現在怎樣 | Overview（doctor 一張表、三個資料域、最近 review、jobs、最近寫入） |
| 改旋鈕 | Settings → Apply，不用 restart（host / port 類除外）；改動紀錄在頁底 |
| 餵料新不新 | Sources（每顆 clone 的 sha、Sync now、預覽 rules / knowledge 注入） |
| 剛才那條 review | Reviews（findings、吃到了什麼、報告、trace 連結；也能直接跑一條） |
| 帳本在等誰 | Knowledge（待裁決、暫存、digest 預覽、reject / keep、立刻 harvest） |
| 主機上一行看完 | `venv/bin/python -m shared.doctor --pr-url <一條真 PR>` |

真發留言的三顆開關（`AUTO_POST` × `DRY_RUN`、`KNOWLEDGE_POST`）出廠全安全側；在
Settings 翻向真發側要勾「我確定」。建議節奏：影子期 → 翻 `KNOWLEDGE_POST` → 翻
`AUTO_POST` + `DRY_RUN=false`。

## 6. 升級

```bash
cd /opt/pr-review-suite && sudo -u svc-review git pull && sudo venv/bin/pip install -r requirements.txt
sudo venv/bin/python -m shared.install        # 冪等：只補新目錄 / 新鍵，不動既有值
sudo systemctl restart review-orchestrator && venv/bin/python -m shared.doctor
```

從 S1 之前的佈局（五個行程、四份 env）升上來：先 `disable --now` 舊的
`pr-review-ado / -reader / -results / -repo-sync.timer / -knowledge-harvest.timer` 並刪 unit 檔；
舊 `suite.env` 可以留著（env 永遠覆寫 toml），但留著就不能在控制台改那些值——
`venv/bin/python -m shared.settings --migrate` 把非 secret 的值搬進 config.toml 後把它移走。

## 7. 出事

1. **先跑 doctor**。每個 FAIL 列都寫了該看什麼；Overview 與 Setup 頁是同一份。
2. **服務起不來、journal 第一行「設定值有誤」**：哪一顆、收到什麼都在那行；修
   secrets.env 或 config.toml 再 restart。
3. **static_findings 恆為 0**：semgrep / ruff 不在 unit 的 PATH（樣板已含 venv/bin）。
4. **repo-sync 一直失敗**：`venv/bin/python -m orchestrator.jobs repo-sync` 看那一行；
   多半是 PAT、SSL（`ADO_VERIFY_SSL` 指系統 CA 合併包）或 proxy。
5. **review 很慢**：v2 是 4–6 次 LLM 呼叫；先降 `REVIEW_MAX_DIMENSIONS`，看 gateway 有沒有限流。
6. **eval**：`venv/bin/python -m eval.run_eval <run 名>`（exit 2 = preflight 擋、1 = gate 擋）；
   Langfuse 的 Remote Experiment 可一鍵打 `/eval-webhook?pipeline=v1|v2`。
7. **code graph（選配）**：`docs/RUNBOOK-2026-08-legacy.md` Phase 8，還是舊的做法。
