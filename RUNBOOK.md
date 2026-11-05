# pr-review-suite v2 部署 Runbook（細版）

適用：已照原 README 部署過 v1 的主機（`/opt/pr-review-suite` + venv、
`/etc/pr-review-suite/*.env`、四個 systemd 服務、Langfuse :3000）。
每一步都附「預期輸出」與「判讀」。任何一步卡住，先看文末 Troubleshooting。

---

## Phase 0：前置盤點（約 5 分鐘）

**0.1 定義本文件的標準執行姿勢**（每開一個新 shell 貼一次）：

```bash
prs() { sudo -u svc-review bash -c "set -a; source /etc/pr-review-suite/suite.env; set +a; export PATH=/opt/pr-review-suite/venv/bin:\$PATH; cd /opt/pr-review-suite; $*"; }
```

意義：以服務帳號、載入正式 env、**把 venv/bin 排進 PATH**（後面 static pass
找 semgrep/ruff 靠它）、站在專案根目錄執行。之後文件裡 `prs <指令>` 都指這個。
限制：參數別帶引號；帶引號的長指令請自行展開成完整 `sudo -u … bash -c '…'`。

**0.2 Python 版本門檻**（v2 用了 3.10+ 語法，過不了這關全部免談）：

```bash
prs venv/bin/python --version
```

預期 `Python 3.10` 以上。低於 3.10 → 先重建 venv 再回來。

**0.3 現況確認**：

```bash
systemctl is-active review-orchestrator pr-review-ado pr-review-reader pr-review-results
curl -s localhost:8080/healthz
```

預期四個 `active` + `{"ok":true}`。

**0.4 全量快照**（installer 自帶逐檔備份，這是雙保險、一鍵還原用）：

```bash
sudo tar czf /root/pr-review-suite-pre-v2-$(date +%F).tgz /opt/pr-review-suite /etc/pr-review-suite
```

---

## Phase 1：套用檔案

**1.1** 把 `pr-review-suite-v2.sh` 放上主機（scp / 內網檔案伺服器皆可）。
若經過 Windows 剪貼簿，先驗屍（血淚 #2）：

```bash
file pr-review-suite-v2.sh        # 要看到 Bourne-Again shell script
grep -c $'\r' pr-review-suite-v2.sh   # 要是 0；不是 0 → sed -i 's/\r$//' 治療
```

**1.2** 套用 + 還權：

```bash
sudo bash pr-review-suite-v2.sh /opt/pr-review-suite
sudo chown -R svc-review: /opt/pr-review-suite
```

預期：41 行 `寫入 …`，結尾
`完成：41 個檔案。被覆寫的舊版備份在 /opt/pr-review-suite/.v2-backup-<時間戳>`。

**1.3** 匯入健檢（語法/相依一次驗完）：

```bash
prs venv/bin/python -c "import orchestrator.pipeline, orchestrator.core, orchestrator.app, eval.run_eval, eval.preflight; print('import ok')"
```

預期 `import ok`。報 `ModuleNotFoundError: fastapi/fastmcp/...` →
`prs venv/bin/pip install -r requirements.txt` 補齊後重驗。

---

## Phase 2：靜態掃描依賴（semgrep / ruff）

**2.1** 裝進 venv（如需 proxy，在 pip 前加 `HTTPS_PROXY=http://proxy.corp:3128`）：

```bash
prs venv/bin/pip install semgrep ruff
prs semgrep --version
prs ruff --version
```

預期各印一行版本號。裝不動也能繼續（static 段會自動跳過並在 stats 註記），
但強烈建議裝——pr-002 那類注入從「賭模型」變「必中」全靠它。

**2.2 ⚠ 服務端 PATH 地雷**：systemd 跑 `venv/bin/uvicorn` 時 **PATH 不含
venv/bin**，`shutil.which("semgrep")` 會找不到 → 服務裡 static 段永遠跳過。
解法是給 unit 加 PATH override：

```bash
sudo systemctl edit review-orchestrator
```

在打開的檔案寫入：

```ini
[Service]
Environment=PATH=/opt/pr-review-suite/venv/bin:/usr/local/bin:/usr/bin:/bin
```

存檔即可（`daemon-reload` 由 Phase 4 一起做）。CLI 側不受影響——`prs` 已含。

---

## Phase 3：本地 clone 佈局 + sync timer

**3.1** 目錄命名規則先搞對：`/opt/repos/<repo小寫>/`，`<repo>` 必須等於
PR URL 裡 `_git/<這段>` 的**小寫**。拿一條真 PR URL 驗證你要用的名字：

```bash
echo "<PR URL>" | grep -oP '_git/\K[^/]+' | tr 'A-Z' 'a-z'
```

**3.2** 一次性 git 全域設定（svc-review 身分；公司 CA + proxy，血淚 #3）：

```bash
sudo -u svc-review git config --global http.sslCAInfo /etc/ssl/certs/ca-certificates.crt
# 有 proxy 才需要：
sudo -u svc-review git config --global http.proxy http://proxy.corp:3128
```

**3.3** clone（checkout **target branch**，通常 main）。PAT 認證兩選一：

```bash
sudo mkdir -p /opt/repos && sudo chown svc-review: /opt/repos
# 方案 A（簡單，PAT 進 .git/config，僅 svc-review 可讀，內網可接受）：
sudo -u svc-review git clone --branch main \
  "https://anything:<ADO_PAT>@<ADO主機>/<collection>/<project>/_git/<repo>" \
  /opt/repos/<repo小寫>
# 方案 B（PAT 不落地）：credential.helper store，第一次 clone 互動輸入一次
```

**3.4** 關鍵驗證——timer 之後做的就是這件事，先手動確認它不會卡互動：

```bash
sudo -u svc-review git -C /opt/repos/<repo小寫> pull --ff-only
```

預期 `Already up to date.`。跳出要帳密 → 回 3.3；SSL 錯 → 回 3.2。

**3.5** timer 上線：

```bash
sudo cp /opt/pr-review-suite/deploy/systemd/pr-review-repo-sync.service.example /etc/systemd/system/pr-review-repo-sync.service
sudo cp /opt/pr-review-suite/deploy/systemd/pr-review-repo-sync.timer.example  /etc/systemd/system/pr-review-repo-sync.timer
# 若之後裝 code-review-graph 進 venv，這個 service 也要 PATH（現在順手做掉）：
sudo systemctl edit pr-review-repo-sync   # 寫入與 2.2 相同的 [Service] Environment=PATH=…
sudo systemctl daemon-reload
sudo systemctl enable --now pr-review-repo-sync.timer
sudo systemctl start pr-review-repo-sync.service     # 手動觸發一次
journalctl -u pr-review-repo-sync.service -n 20 --no-pager
```

預期每個 repo 一行 `[repo-sync] <名字> -> <sha>`。
`pull 失敗` → 回 3.4 的兩個病因。

---

## Phase 4：env 合一 + 重啟

**4.0 四份 env 檔合成一份**（只做一次；之前沒有四份檔的新機器直接從 4.0b 開始）。
變數名一個都沒改，所以舊檔內容原樣可用；唯一新名字是三個 port——舊檔各自的
`PORT=` 在單一檔裡會互撞，要改成 `ADO_TOOLS_PORT` / `READER_TOOLS_PORT` /
`RESULT_TOOLS_PORT`（舊名 `PORT` 仍認，但只適合舊式一服務一檔）：

```bash
cd /etc/pr-review-suite
sudo cat orchestrator.env ado.env reader.env results.env \
  | grep -vE '^(HOST|PORT|ADO_PAT|ADO_VERIFY_SSL|HTTPS_PROXY)=' > suite.env.tmp
sudo grep -hE '^(ADO_PAT|ADO_VERIFY_SSL|HTTPS_PROXY)=' ado.env | sort -u >> suite.env.tmp
printf 'HOST=127.0.0.1\nADO_TOOLS_PORT=8094\nREADER_TOOLS_PORT=8095\nRESULT_TOOLS_PORT=8096\n' >> suite.env.tmp
sudo mv suite.env.tmp suite.env && sudo chmod 600 suite.env && sudo chown root:root suite.env
```

**4.0b 驗證這份檔**（壞值、重複鍵、已淘汰的名字，一次全列）：

```bash
prs venv/bin/python -m shared.settings --check
prs venv/bin/python -m shared.settings --show
```

預期 `設定 OK`，`--show` 每一列的來源欄是 `env:<名字>` 或 `default`；
末段「注意」若列出 `REPO_ROOT 已淘汰` 或 `偵測到舊式 PORT`，照字面修。
確認無誤後把四份舊檔移走（不要留在原位，systemd 樣板已經不再讀它們）。

**4.1** `suite.env` 追加（**註解獨立成行、等號右邊零尾巴**，血淚 #1）：

```bash
REVIEW_PIPELINE=v1
REPOS_ROOT=/opt/repos
```

沒做過 rules 那輪的話，`RULES_ROOT=/opt/pr-review-rules` 也一併補上。
v2 其他旋鈕（REVIEW_MIN_CONFIDENCE 等）先**全用預設**，不要現在調。
完整清單與每顆的說明：`deploy/env/suite.env.example`（由 schema 生成）。

**4.2** 若 `suite.env` 裡還有 `REPO_ROOT=…`（舊版單 repo），整行刪掉；
`REPOS_ROOT` 已在 4.1。`--show` 的注意段會提醒。

**4.2b** 十個 systemd 樣板的 `EnvironmentFile=` 已全部指向 `suite.env`；
已部署的 unit 實檔請同步改（`sudo systemctl edit --full <unit>` 或重 cp 樣板）。

**4.3** 重啟 + 活體對帳（你們自己的招）：

```bash
sudo systemctl daemon-reload
sudo systemctl restart review-orchestrator pr-review-reader
for s in review-orchestrator pr-review-reader; do
  echo "== $s"; sudo cat /proc/$(systemctl show -p MainPID --value $s)/environ | tr '\0' '\n' | grep -E '^(REVIEW_PIPELINE|REPOS_ROOT|RULES_ROOT|PATH)='
done
curl -s localhost:8080/healthz
```

判讀：orchestrator 要看到 `REVIEW_PIPELINE=v1`、`REPOS_ROOT`、PATH 開頭是
venv/bin；reader 要看到 `REPOS_ROOT`。少哪個 = 哪個 env/override 沒生效
（restart 而非 reload？CRLF？override 檔存了嗎？）。

---

## Phase 5：播種 prompts + preflight + v1 回歸基線

**5.1** 播種（現在會推四份 prompt）：

```bash
prs venv/bin/python -m eval.seed_langfuse
```

預期：

```
prompt 'pr-review-system' 已推上 Langfuse（label: production）
prompt 'pr-review-planner' 已推上 …
prompt 'pr-review-reviewer' 已推上 …
prompt 'pr-review-adversary' 已推上 …
dataset 'pr-review-golden' 寫入 2 條 items —— 去 UI 的 Datasets 確認
```

注意：重跑 seed 是**新增** items 不是覆蓋——dataset 裡出現重複的 pr-002
就去 UI 把舊 shape 那條 archive 掉（scorer 相容，留著也只是灌水題）。

**5.2** Preflight：

```bash
prs venv/bin/python -m eval.preflight
```

預期 `Preflight 通過` + 統計行（v2 幾條/舊版幾條/乾淨負樣本幾條）。
列出問題 → 照訊息去 UI 整形那幾條 item，修完重跑。

**5.3** v1 回歸（證明蓋檔沒動到舊路徑）：

```bash
prs venv/bin/python -m eval.run_eval v1-post-patch
```

預期輸出樣貌：

```
run 'v1-post-patch'：N 條（v2 X／舊 Y／乾淨 Z）
  recall 平均 0.xx｜噪音率 0.xx｜禁語率 0.xx
  配置：prompt v4 × rules <sha> × <模型名>
→ UI：Datasets → pr-review-golden → Runs 看逐條分數與 trace
```

判讀：recall 要跟你們之前的基線同一個量級。差很多 → 先停，
看 UI 裡哪條 item 掉分、點進 trace 查，別急著往下。

---

## Phase 6：v2 先過 eval（完全不碰服務）

eval 走 CLI 自己的環境，所以 A/B 不需要動任何服務——這是設計好的。

**6.1** 連跑兩輪定 v2 基線 + 雜訊底：

```bash
prs REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval v2-a
prs REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval v2-b
```

判讀三件事：
1. **pr-002 的 SECURITY 必中**（static 抓的，confidence 1.0）——沒中去看
   run 輸出有沒有 `static_notes`（十之八九是 semgrep 沒進 PATH，回 2.2）。
2. v2-a vs v2-b 的分差 = 你們的**雜訊底線**，記下來。
3. UI Runs 勾 `v1-post-patch` + `v2-a` 對比：recall 應 ≥ v1；
   noise 若升 → 之後調 `REVIEW_MIN_CONFIDENCE`（0.55→0.65）再跑一輪比。

**6.2** 點開 v2 任一 item 的 trace，瀑布應長這樣：
`run-review → plan → review-dimension ×N（平行）→ adversary → synthesis`，
metadata 有 `dimensions / static_findings / llm_findings / adversary=ok(…)`。
`plan=fallback` 偶發正常（降級=固定四科）；**每條都 fallback** → gateway 吐
不出乾淨 JSON，點開 plan 的 generation 看原始輸出，去 Langfuse UI 改
`pr-review-planner` 的措辭（零改碼）再跑。

**6.3** 離線 eval 裡 `base_available=false`、graph 全跳過是**預期行為**
（fixtures 只有 diff）——base/graph 的效果要等 Phase 7 真 PR 才看得到。

---

## Phase 7：服務切 v2 + 真 PR 驗證

**7.1** `suite.env` 把 `REVIEW_PIPELINE=v1` 改成 `v2` →
`sudo systemctl restart review-orchestrator` → 4.3 的活體對帳再跑一次確認。

**7.2** 真 PR 打一發：

```bash
curl -s -X POST localhost:8080/review -H 'content-type: application/json' \
  -d '{"pr_url":"<PR URL>"}' | head -c 1200
```

判讀 markdown：開頭 `## 🤖 PR Review`、**沒有 JSON 塊**、結尾 footer
`pipeline v2 · 面向 N · static S · llm L · adversary ok(…) · rules <sha>`。

**7.3** 去 Langfuse 看這條 trace 的 metadata，重點三個旗標：
`base_files > 0`（本地 clone 接通了）、`rules_sha` 有值、
`adversary=ok(…)`。`base_available=false` → repo 目錄名對不上：
比對 trace 裡 `fetch_pr_metadata` 輸出的 `repo` 值和 `ls /opt/repos/`。

**7.4** Open WebUI 貼同一條 PR：串流旁白照舊（🔍📋📄🤖），最後吐出的是
v2 的乾淨 markdown。注意 dedupe：TTL 內重貼是重播快取，屬正常。

**7.5** 回滾姿勢背起來：env 改回 `REVIEW_PIPELINE=v1` + restart，十秒完事。

---

## Phase 8：code graph ④（選配，隨時可補）

以下的工具名、URL 路徑、參數樣板都已對 code-review-graph 實機驗證過。
關鍵事實：它的 `serve` 有**原生 `--http` 模式**（MCP over Streamable HTTP），
所以不需要任何橋接——直接就是 clients.py 認得的那種 HTTP MCP server。

**8.1 安裝（裝進同一個 venv；PATH 已在 2.2/3.5 一次解掉）**：

```bash
prs venv/bin/pip install code-review-graph
prs code-review-graph --version
```

注意：它的相依會把 opentelemetry 拉到比 semgrep 要求更新的版本，pip 可能
印相容性警告——實測兩者都能正常執行，警告可忽略；潔癖版是
`pip install semgrep ruff code-review-graph` 三個一起裝讓 resolver 自己喬。

**8.2 首次全量建圖**（graph DB 落在該 repo 的 `.code-review-graph/`）：

```bash
sudo -u <服務user> bash -c 'export PATH=<安裝根>/venv/bin:$PATH; cd <REPOS_ROOT>/<repo小寫> && code-review-graph build'
```

預期最後一行 `Full build: N files, X nodes, Y edges`。之後不用再手動——
3.5 的 timer 每輪 pull 完會自動 `build`（增量，只重解析有改的檔）。
`igraph not available` 的 INFO 行無害（社群偵測退用檔案模式）。

**8.3 起 MCP server（systemd）**：

```bash
sudo cp <安裝根>/deploy/systemd/pr-review-graph.service.example /etc/systemd/system/pr-review-graph.service
sudo vi /etc/systemd/system/pr-review-graph.service   # 換掉三個 <佔位符>：服務user／安裝根／REPOS_ROOT+repo
sudo systemctl daemon-reload
sudo systemctl enable --now pr-review-graph
systemctl is-active pr-review-graph
journalctl -u pr-review-graph -n 5 --no-pager
```

unit 的 ExecStart 骨架（example 檔裡就是這個）：

```
<安裝根>/venv/bin/code-review-graph serve \
    --repo <REPOS_ROOT>/<repo小寫> \
    --http --host 127.0.0.1 --port 8097 \
    --tools get_impact_radius_tool,get_review_context_tool,query_graph_tool
```

`--tools` 是白名單：它總共有 30 個 MCP tools，只放行 pipeline 用得到的。
判讀：journal 出現 uvicorn 的啟動行即可。**用 curl 打 `/mcp` 會回 406，
這是正常的**（MCP over HTTP 要特定 Accept header）——健康檢查請用 8.4
的 list tools，別被 406 騙去 debug。設計取捨：unit 刻意**不開**
`--auto-watch`，讓 3.5 的 timer 當 graph 的唯一寫入者（單一寫手，
避免和 timer 的 build 併發搶 SQLite）。

**8.4 驗收 + 填 env**。先 list tools：

```bash
prs venv/bin/python - <<'EOF'
import asyncio
from fastmcp import Client
async def main():
    async with Client("http://127.0.0.1:8097/mcp") as c:
        for t in await c.list_tools():
            print(t.name)
asyncio.run(main())
EOF
```

預期正好列出白名單那三個。接著在 suite.env 打開（值已實測）：

```bash
GRAPH_TOOLS_URL=http://127.0.0.1:8097/mcp
GRAPH_TOOL_BLAST=get_impact_radius_tool
GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files}'
```

（`$files` 會被代入成變更檔路徑陣列；該工具吃 repo 相對路徑，我們的
anatomy 給的正是這個。想餵更完整的審查上下文可改用
`get_review_context_tool`，換之前先用 list tools 確認它的參數名。）

**多 repo 自動切庫（實測可行）**：不必每 repo 一個 serve——工具支援
per-call `repo_root`，單一實例即可照 PR 自動切到對應 clone：

```bash
GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files, "repo_root": "<REPOS_ROOT>/$repo_dir"}'
```

（`$repo_dir` 代入「repo 名小寫、無引號」；此模式 **GRAPH_REPO 留空**；
serve 啟動時的 `--repo` 只是預設庫，隨便指主力那顆即可。前提：目標 repo
要先 onboard 且 build 過圖——repo-sync timer 對 REPOS_ROOT 下所有 clone
自動建圖，onboard 即涵蓋；未 onboard 的 repo 呼叫會失敗並優雅降級。）
然後 `sudo systemctl restart review-orchestrator` + 4.3 活體對帳看到
三個 GRAPH_ 變數。

**8.5 端到端驗收**：真 PR 再 `curl localhost:8080/review` 一發，
到 Langfuse 看 trace metadata——`graph_used=true`，reviewer 的輸入裡
會多一段 `### code graph（受影響範圍）` 開頭是
`Blast radius for N changed file(s): …`。`graph_used=false` 時看
`graph_note`：`GRAPH_TOOLS_URL 未設定`＝env 沒進服務（回 4.3）；
`graph 呼叫失敗`＝server 沒起或 port 不對（回 8.3 journal）。

**8.6 已知邊界**：目前 env 只有一個 `GRAPH_TOOLS_URL`＝先服務單一主力
repo；要擴多 repo 就每 repo 複製一份 unit（各自 `--repo` 和 port），
orchestrator 端 per-repo 選 URL 屬之後的小改。另外老提醒不變：先往
golden set 加 2–3 條跨檔案例，再跑 `GRAPH_TOOLS_URL` 有／無兩輪 eval，
graph 的貢獻才量得出來（離線 eval 本身查不到 graph，屬預期）。

---

## Phase 9：收尾

1. CI gate 上線姿勢：`prs EVAL_MIN_RECALL=0.7 EVAL_MAX_NOISE=0.2 EVAL_MAX_FORBIDDEN=0 REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval ci-$(date +%m%d-%H%M)`（exit 0 過／1 gate 擋／2 preflight 擋）。
2. 從今天起的迭代紀律：一輪只改一件事（prompt 版 / 旋鈕 / 規則），
   run metadata 自動記配置；「改善」要超過 6.1 記下的雜訊底才算贏。
3. 收編習慣照舊：漏抓/誤報必收、乾淨 PR 當負樣本、攢一批一起加、
   加完重跑一輪當新基線。

---

## Troubleshooting（v2 專屬，接在原 README 血淚之後）

1. **服務裡 static_findings 恆為 0，stats 有「semgrep：未啟用或未安裝」**
   → unit 的 PATH override 沒生效。`/proc/<pid>/environ` 看 PATH 是否以
   venv/bin 開頭；沒有 → `systemctl edit` 的 override 存對地方了嗎、
   daemon-reload + restart 了嗎。
2. **plan 每次都 fallback／adversary 常態 degraded** → gateway 對「只輸出
   JSON」的服從度差。看該 span 的 generation 原始輸出；解法順序：
   Langfuse UI 收緊 planner/adversary prompt → 換模型（LLM_MODEL）→
   接受降級（fallback 四科仍優於 v1）。
3. **production trace `base_available=false`** → `REPOS_ROOT` 沒進服務 env，
   或 `/opt/repos/` 目錄名 ≠ PR URL repo 段的小寫。用 3.1 的指令對名字。
4. **repo-sync journal 出現 pull 失敗** → svc-review 的 git 憑證/CA 問題，
   手動重跑 3.4 那條指令看真錯誤。
5. **`run_eval` exit 2** → preflight 列了兇手 item，去 UI 整形；
   **exit 1** → 三道 gate 之一沒過，輸出最後一行寫明是哪道。
6. **v2 比 v1 慢很多** → 正常（1 次呼叫變 4–6 次）。聊天體驗靠串流旁白
   撐；嫌太慢先降 `REVIEW_MAX_DIMENSIONS=3`、確認 `REVIEW_MAX_PARALLEL`
   沒被 gateway 限流卡住（journal 看 429/timeout）。
7. **reader 起不來** → suite.env 改壞（CRLF/尾巴空白）或 REPOS_ROOT
   打錯字；先 `prs venv/bin/python -m shared.settings --check`，
   再 `journalctl -u pr-review-reader -n 30`，一定有一行說人話。
8. **任何服務起不來、journal 第一行是「設定值有誤」** → 它會列出哪一顆變數、
   收到什麼值；這是 `fail_fast()` 在啟動時擋下的，修 suite.env 再 restart。

---

## Phase 10 — 知識迴圈（K 段，選配）

**三元組**：reviewer 對 PR 的理解（distiller 輸入）＋ user 回饋（Author note）＋
PR 終態（落地裁決）。新增服務數 0。

### 10.1 初始化 knowledge repo（本地）

```bash
mkdir -p <KNOWLEDGE_ROOT> && cd <KNOWLEDGE_ROOT> && git init
git config user.email bot@local && git config user.name knowledge-bot
# 手寫 3–5 頁種子（照 deploy/knowledge/TEMPLATE.md；模組級放 _module/）
git add -A && git commit -m "seed"
```

### 10.2 env 與 prompt

```bash
# suite.env（同一份；下面的 KNOWLEDGE_POST 也在這裡）：
KNOWLEDGE_ROOT=<路徑>
KNOWLEDGE_BRANCHES=release/*,master
# 驗收 PR 的長相（source → target）——符合者不蒸餾、作為週期收口儀式
KNOWLEDGE_CYCLE_SOURCE=release/*
KNOWLEDGE_CYCLE_TARGET=master
# stale 重蒸（draft 之後又有 push → 用最終 diff 重蒸一次）；false = 直接丟稿
KNOWLEDGE_REDISTILL=true
# 重蒸端點的鑰匙與位址（harvester 讀同一份 env）——用重蒸就把 key 設起來，
# /knowledge/redistill 有 LLM 花費與寫檔語意，不是純讀
ORCH_API_KEY=<一組隨機字串>
ORCH_URL=http://127.0.0.1:8080
# 試點開關，先 false = 影子模式：
KNOWLEDGE_POST=false
```

`restart review-orchestrator pr-review-results` → **re-seed**（`pr-review-distiller`
與 `pr-review-reviewer` 兩份 prompt 都要上 Langfuse；後者帶 `knowledge_ref` 合約）。

⚠ 設了 `ORCH_API_KEY` 之後，聊天室（Open WebUI）與腳本呼叫 `/review`
都要帶 `Authorization: Bearer <key>`；`/eval-webhook` 刻意不驗（見 README）。

### 10.3 harvest timer

```bash
sudo cp deploy/systemd/pr-review-knowledge-harvest.service.example \
     /etc/systemd/system/pr-review-knowledge-harvest.service
sudo cp deploy/systemd/pr-review-knowledge-harvest.timer.example \
     /etc/systemd/system/pr-review-knowledge-harvest.timer
# 改 <你的user> / <安裝根> 佔位符
sudo systemctl daemon-reload && sudo systemctl enable --now pr-review-knowledge-harvest.timer
```

### 10.4 驗證

review 一條會碰到種子頁實體的 PR → Coverage 表出現
`| Knowledge context | N entr… matched |`（預算不足時同一格會標
`M invariants only, K omitted`）；有 finding 引到條目時再多一句
`N finding(s) cite a ledger entry`。手動跑一次
`prs venv/bin/python -m orchestrator.knowledge_harvest` 看 journal 五路分類。
PAT 權限對帳：拿 suite.env 的 ADO_PAT GET 一次 threads，通了即結案。

新蒸的稿要能自我描述（`_pending/<repo>/pr-<id>.json`）：
`evidence_verified: true`、`distill_dropped: []`（有值代表有 decision 因為
引不出證據被丟掉，草稿留言 footer 會寫出來）、`distill_slices_skipped: []`
（有值代表 diff 大到超過 `DISTILL_DIFF_MAX_CHARS`）。

### 10.4b stale 重蒸的驗證（Phase 5）

```bash
# 端點活著（設了 ORCH_API_KEY 就要帶）
curl -X POST localhost:8080/knowledge/redistill \
     -H 'Authorization: Bearer <key>' -H 'Content-Type: application/json' \
     -d '{"pr_url":"https://<...>/_git/<repo>/pullrequest/<id>"}'
# → 202 + {"ok": true}；蒸餾在背景跑，看 journalctl -u review-orchestrator
```

真實動線：小 PR 貼了草稿之後再 push 一次、然後 complete →
下一輪 timer 的 journal 會出現「請求重蒸（第 1/2 次，commit xxxxxxxx）」→
orchestrator 那邊「重蒸完成 → _pending 覆寫」→ 再下一輪「staged
（silent·redistilled）」。作者先前的回覆逐字帶進重蒸材料，更新稿**回覆進原
thread**（不開新 thread），開頭有 `Draft updated after new commits`。

失敗模式一律有界：POST 打不通或背景蒸餾失敗 → 下一輪再試一次，兩次都不成
就 stale 丟稿（journal 寫「重蒸兩次未成」）；重蒸期間又有新 push → 記世代變更
後丟稿。任何一條 PR 的重蒸 LLM 支出 ≤ 2 次。

### 10.4c 量尺（Phase 2 的兩個數）

```bash
prs venv/bin/python -m eval.run_eval knowledge-baseline
# 輸出末段：
#   knowledge_recall 有知識 0.60（5 題）｜對照組 0.20（5 題）｜**knowledge_delta +0.40**
prs venv/bin/python -m eval.run_distill_eval distill-baseline   # 蒸餾考卷
```

線上那個數在 trace metadata：`knowledge_grounded` / `knowledge_ids_injected`
（引用率 = 上界，只證明 reviewer 讀到了）。離線那個是 `knowledge_delta`
（下界）。**放行條件**：連續兩輪 delta > 0，或單輪 ≥ 2/5——一次僥倖命中不算。
delta = 0 的診斷順序：① 連單頁題都 0 → consumption 問題，先迭代 reviewer
prompt（Knowledge 區塊的位置與措辭）；② 只有 distractor 題 0 → 排序/預算問題；
③ 兩者都修完仍為 0 → K 段進維護模式，把時間還給主線 review。

### 10.5 節奏（週期驗收制）

小 PR 進 release：蒸餾＋貼稿（不催核准）→ completed 後暫存 `_cycle/`
（reject / abandoned 當場淘汰；stale 走重蒸補救，兩次不成才淘汰；作者回覆
逐字入 Author note）。暫存稿同週期即可被後續 review 注入（標 staged）。

release → master 驗收 PR：active 時 harvester 貼一則 **cycle digest**
（驗收窗）；之後每有新暫存稿進池就補一則 **delta**（只列新進）。任何人可回
`reject <EntityName>`（整條）、`reject PR12`（整條、不撞名）、`reject PR12-I1`
（只丟那一行）→ completed 時批次落地（provenance `cycle/explicit|silent`，從未
出現在 digest 的條目帶 `+unlisted`）＋ `_cycles/<日期>-PR<id>.md` 週期收據。
驗收 PR 本身豁免蒸餾；abandoned 則暫存保留等下一輪。

digest 開頭的「🔎 Needs a look」是 LLM 顧問段（`KNOWLEDGE_DIGEST_LLM`，prompt
`pr-review-digest`）：整週期一起看、標矛盾 / 近重複 / 該註銷沒點名、建議
`reject <ID>`。它只是提案——落地不讀它，關掉或失敗都退回純清單。

影子試點：`KNOWLEDGE_POST=false` 時小 PR 不被打擾（稿乾跑），
digest 由 harvester 直發不受此開關管——第一個對外可見物就是收口那一則。

**首見水位（收口路徑）**：「哪些驗收 PR 已經結算過」只記在
`_state/cycle.json`，而它住在 `KNOWLEDGE_ROOT` 底下。帳本重新 clone、`_state`
掉了、或測試時清空 KB，這份記憶就沒了，但 ADO 的歷史一條都沒少——收割器跟
ADO 要的是「最近 100 條 completed PR」，沒有時間條件。因此**首次看到某個
repo 時，當下已經 completed 的驗收 PR 一律標為已收口、不回填落地**，journal
會印「首見，N 條歷史驗收 PR 標為已收口」。暫存稿留在池裡，等下一條**真正
完成**的驗收 PR 才收。這與 watermark 掃描是同一套規則。

代價講明：如果你是在某個 release 週期進行到一半才啟用（或剛好在那條驗收 PR
完成之後才第一次跑），那一輪的收口會被吞掉，該批暫存稿要等下一條驗收 PR。
用 `_state/cycle.json` 手動把某條 PR 的 `closed` 拿掉，就能讓它下一輪照收。

### 10.6 記憶層維運節奏

**規則怎麼被拔掉**：三條路，全部都是「畫掉 + History 留行」，永不刪除。
① distiller 明示註銷（PR description 講明反轉 → `retires_id`）；
② **實作推翻**：review 報了違反（finding 帶 `knowledge_ref`）而 PR 照樣完成 →
digest 的「⚠ Invariants contradicted by merged code」紅區列出 → 收口時沉默即拔，
任何人回 `keep PR12-I1` 否決（旋鈕 `KNOWLEDGE_RETIRE_ON_CONTRADICTION=false`
可整段關掉）；③ 人工直接改 markdown（帳本就是 git repo，人寫的段落會前滾保留）。
已畫掉的行不再進 distiller 的目錄，也不再注入給 reviewer——只留在頁面上給人看血緣。

**Supersede 語意**：條目可宣告廢除既有規則（distiller 只在 PR 明示反轉時才填、
不推論），以 `retires_id`（例 `PR12-I1`）精確點名；沒有 ID 的舊行才用逐字複製。
digest 以「⚠ Rules being retired」獨立紅區呈現——註銷比新增風險高，
掃的人先看這段。落地時被廢頁**頂端蓋戳 + History 留行、內容保留**：
註銷是標記，不是蒸發；不同意照樣 `reject` 廢除方條目即可。

**月度 staleness 抽樣（10 分鐘）**：從 Langfuse traces 撈近月
`knowledge_injected` 名單 → 隨機抽 5 條實際被注入過的條目 → 人工判「還成立嗎」
→ 記衰減率。這是記憶層唯一的健康儀表板；連續兩月衰減率高再啟動
reconciliation 自動化（顯式 confirmed/superseded 裁決），空帳本期不建。

### 附錄 X — 確定性層單元測試

```bash
venv/bin/pip install pytest   # 一次性（dev 依賴，不進 requirements 核心）
venv/bin/python -m pytest -q tests/
```

純函式鎖（anatomy / 邊界比對 / 行號驗證 / 切片預算 / MISSING-TEST
跨語言 / K1 閘門矩陣 / 落地累積+未知段前滾 / reject 邊界與歧義 /
watermark 首見 / digest state merge / 快取 commit 語意 / meta 盾 /
條目 ID 冪等 / ID 註銷與文字去重 / 撞名 skip 回 2-tuple / digest 首次→delta→
+unlisted / reject 三粒度 / digest 驗證與渲染 / 分析降級 / atomic write
/ **evidence 驗證矩陣** / **蒸餾切片優先序** / **primary 錨點 fallback**
/ **注入 id 名單與 knowledge_ref 執法** / **tier 排序與分級降載**
/ **蒸餾考卷計分（含三題陷阱的判法）** / **重蒸請求戳與重試上限**
/ **重蒸覆寫與舊痕跡作廢** / **thread 回覆而非開新 thread**）。
全離線零 LLM；改任何確定性函式，先過這關再跑 eval。
