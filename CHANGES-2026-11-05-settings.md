# 變更說明 — 2026-11-05（設定收斂）

維運 refinement 第一步：**64 個環境變數、約 90 個讀取點、4 份 env 檔**收成
**一個 schema、一個讀取點、一份 env 檔**。變數名一個都沒改，既有部署的 env
內容原樣可用。80 條測試全綠（原 68 + 新 12），全離線、零 LLM。

---

## 一、新增 `shared/settings.py`（唯一讀取點）

pydantic-settings。12 組（ado / tools / llm / langfuse / orchestrator / review /
static / repos / graph / rules / knowledge / eval），每個欄位的 alias 就是既有的
環境變數名，附型別、預設值、範圍、說明。

- `settings()` 惰性單例、`reset()` 清快取；`fail_fast()` 給服務啟動用。
- 空字串 = 未設（`KNOWLEDGE_ROOT=""` 仍是 run_eval 關掉知識迴圈的正式手法）。
- **壞值就炸**：`REVIEW_MAX_FINDINGS=abc` 以前靜默退回 30，現在啟動即報
  「哪一顆、收到什麼」。app 的 lifespan、三個 tool server 的 `__main__`、
  harvester 的 `main()` 都掛了 `fail_fast()`。
- 衍生判斷收進模型，只寫一次：`knowledge.enabled`、`graph.enabled`
  （url 與 tool 都有才算）、`rules.enabled`、`static.semgrep_enabled`、
  `repos.root_for(repo)`、`tools.ado_url`（未設 URL 就由 port 推得）。
- `knobs()`：七組會影響 review 結果的旋鈕一次 dump，`core.run_review` 把它
  放進 trace metadata 的 `config`——新增旋鈕自動被記錄，不用再手塞 stats。
- CLI：`--show`（生效值 + 來源 + 遮罩 + 注意事項）、`--check`（只驗證）、
  `--example`（由 schema 生 env 樣板）。

## 二、13 個檔案的 getenv 全部改走 settings()

orchestrator：app、core、pipeline、llm、clients、context、rules、static_pass、
knowledge、knowledge_digest、knowledge_harvest；shared/azdo；tools 三個 server；
eval 的 run_eval / run_distill_eval / preflight。

行為刻意保留的點：
- `llm.py` 的 `AsyncOpenAI` client 從模組載入時建立改成第一次呼叫才建
  （`_llm()`），env 不再在 import 時被偷讀。
- `app.py` 的 `_DEDUPE_TTL` 常數改成 `_ttl()`，測試可以 monkeypatch。
- `run_eval` 對照組仍用改 `os.environ["KNOWLEDGE_ROOT"]` 的手法，但每次改完
  呼叫 `reset_settings()`。

行為刻意改掉的點：
- 三個 tool server 的 **port 預設 8091/8092/8093 → 8094/8095/8096**。程式碼原本
  的預設跟 README、RUNBOOK、systemd 樣板全部對不上；既有部署都是明設的，
  不受影響。
- 新名字 `ADO_TOOLS_PORT` / `READER_TOOLS_PORT` / `RESULT_TOOLS_PORT`：單一 env
  檔裡三個服務不能共用 `PORT`。舊名 `PORT` 仍認（一服務一檔的舊佈局照跑）。
- `ADO_TOOLS_URL` 等三顆未設時由對應 port 推得，不再各自硬寫一個預設 URL。
- `STATIC_TIMEOUT` 壞值以前退回 90 秒、`_int_env` 壞值退回預設——現在一律
  在啟動時報錯。

## 三、部署面

- `deploy/env/` 四份手寫 example 退場，換成 **`suite.env.example`**（298 行，
  由 `--example` 生成；註解來自 Field 的 description，勿手改）。
- 十個 systemd 樣板的 `EnvironmentFile=` 全部指 `/etc/pr-review-suite/suite.env`；
  harvest unit 從兩份 EnvironmentFile 變一份；repo-sync 從 `Environment=REPOS_ROOT`
  變讀同一份檔。
- RUNBOOK Phase 4 新增 4.0 / 4.0b：四合一的指令與 `--check` / `--show` 驗證；
  Troubleshooting 新增第 8 條（啟動即「設定值有誤」）。README §6 改成指令 +
  短表。

## 四、測試

- `tests/conftest.py`：autouse fixture 把 `monkeypatch.setenv/delenv` 綁上
  `settings.reset()`，既有 60+ 條測試零改動。
- `tests/test_settings.py` 12 條：預設值與舊程式一致、env 覆寫、空字串、
  壞值、三態與 switch 解析、port 推 URL 與舊名 `PORT`、enabled 旗標、
  knobs 無 secrets、**67 個舊變數名一個都不能漏**、example 往返、
  warnings、fail_fast。

## 五、`shared/doctor.py`（第二條 PR）

RUNBOOK 全部手動判讀的程式版：`python -m shared.doctor [--pr-url URL] [--no-network] [--json]`。
八組檢查各回 OK / WARN / FAIL / SKIP：settings（解析 + warnings_for）、python
（3.10+、八個依賴版本、semgrep/ruff 在不在 PATH）、paths（REPOS_ROOT 的 clone 與
HEAD、RULES_ROOT 的 md/semgrep 規則數與目錄名對不對、KNOWLEDGE_ROOT 的頁數與
pending 數）、services（orchestrator /healthz、①②③ port、graph）、langfuse
（/api/public/health + 用 key 打 /api/public/projects）、llm（/models 並對帳
LLM_MODEL / _ADVERSARY / _DIGEST 在不在清單）、ado（PAT 有沒有設；給 --pr-url
才真打）、systemd（LoadState / ActiveState；常駐組怕 dead、定時組怕 failed）。
退出碼 0 / 1（有 FAIL）/ 2（設定解析不過）。四個探針（tcp / http / which / run）
集中一處，`tests/test_doctor.py` 11 條全離線。

## 六、沒做的事（留給下一步）

- `pr-review-suite-full.sh` / `pr-review-suite-knowledge-loop.sh` 兩支單檔部署包
  仍是舊內容，需要重生。
- 五處 `read_text()` / `write_text()` 沒帶 `encoding="utf-8"`，在 Windows 預設
  cp950 下會炸（本機跑測試要 `PYTHONUTF8=1`）；Linux 不受影響。
- ①②③ 併回 orchestrator 行程內（settings 做完後這一步變簡單）。
