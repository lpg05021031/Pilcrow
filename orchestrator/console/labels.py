"""設定頁每個欄位的人話標籤（環境變數名 → 短標籤）。

schema 的 description 是「解釋」，不是「名字」；表單左欄要的是名字。這張表是手寫的，
新增欄位沒寫就退回環境變數名。
"""
LABELS: dict[str, str] = {
    # ado
    "ADO_PAT": "Personal Access Token",
    "ADO_VERIFY_SSL": "憑證驗證 / CA bundle",
    "ADO_DIFF_MAX_FILES": "一條 PR 最多納入幾個檔",
    "ADO_DIFF_MAX_FILE_BYTES": "單檔內容上限（bytes）",
    "ADO_DIFF_FETCH_CONCURRENCY": "抓檔併發度",
    # posting
    "DRY_RUN": "review 留言只預覽、不真發",
    "KNOWLEDGE_POST": "知識稿真的貼到 PR",
    # llm
    "LLM_BASE_URL": "Gateway 位址",
    "LLM_API_KEY": "API key",
    "LLM_MODEL": "主模型",
    "LLM_MODEL_ADVERSARY": "Adversary 模型",
    "LLM_MODEL_DIGEST": "Digest 分析模型",
    "LLM_TIMEOUT_S": "單次呼叫逾時（秒）",
    # langfuse
    "LANGFUSE_HOST": "Langfuse 位址",
    "LANGFUSE_PUBLIC_KEY": "Public key",
    "LANGFUSE_SECRET_KEY": "Secret key",
    # orchestrator
    "REVIEW_PIPELINE": "Pipeline 版本",
    "AUTO_POST": "review 完自動回貼",
    "REVIEW_DEDUPE_TTL": "重複觸發的去重秒數",
    "ORCH_API_KEY": "控制台 / API 的鑰匙",
    "ORCH_URL": "服務自己的位址（harvester 用）",
    "DATA_DIR": "資料目錄（review 歷史）",
    # jobs
    "REPO_SYNC_INTERVAL_MIN": "repo 同步間隔（分）",
    "HARVEST_INTERVAL_MIN": "知識收割間隔（分）",
    "REPO_SYNC_GRAPH_REBUILD": "同步後重建 code graph",
    # review
    "REVIEW_MAX_DIMENSIONS": "最多開幾個審查面向",
    "REVIEW_MAX_PARALLEL": "LLM 併發上限",
    "REVIEW_LLM_BASE_CONF": "LLM finding 基礎信心",
    "REVIEW_MIN_CONFIDENCE": "信心門檻（低於即過濾）",
    "REVIEW_MAX_FINDINGS": "報告最多幾條 finding",
    "REVIEW_ADVERSARY": "啟用 adversary 對抗段",
    "ADVERSARY_CHUNK": "Adversary 每次審幾條",
    "REVIEWER_RULES_MAX_CHARS": "每科規則文字上限",
    "REVIEWER_SLICES_MAX_CHARS": "每科 diff 切片總預算",
    "REVIEWER_FILE_SLICE_MAX_CHARS": "單檔切片上限",
    # static
    "STATIC_SEMGREP": "semgrep",
    "STATIC_RUFF": "ruff",
    "STATIC_TIMEOUT": "掃描逾時（秒）",
    "STATIC_MISSING_TEST": "確定性 MISSING-TEST",
    # repos
    "REPOS_ROOT": "本地 clone 根目錄",
    "CONTEXT_LINES": "hunk 周邊取幾行 base",
    "CONTEXT_MAX_CHARS": "base 與 graph 上下文預算",
    # graph
    "GRAPH_TOOLS_URL": "code graph MCP 位址",
    "GRAPH_TOOL_BLAST": "影響半徑工具名",
    "GRAPH_TOOL_BLAST_ARGS": "工具參數樣板",
    "GRAPH_REPO": "graph 綁定的 repo",
    # rules
    "RULES_ROOT": "規則 repo 根目錄",
    "RULES_SUBDIR": "規則子目錄名",
    "RULES_COMMON_DIR": "共用規則目錄名",
    "RULES_MAX_CHARS": "規則總量上限",
    # knowledge
    "KNOWLEDGE_ROOT": "知識帳本根目錄",
    "KNOWLEDGE_BRANCHES": "進帳本的目標分支",
    "KNOWLEDGE_MAX_CHARS": "注入區塊上限",
    "DISTILL_DIFF_MAX_CHARS": "蒸餾看的 diff 預算",
    "KNOWLEDGE_REDISTILL": "stale 稿用最終 diff 重蒸",
    "KNOWLEDGE_CYCLE_SOURCE": "驗收 PR 的 source 分支",
    "KNOWLEDGE_CYCLE_TARGET": "驗收 PR 的 target 分支",
    "KNOWLEDGE_DIGEST": "收口時貼 digest",
    "KNOWLEDGE_DIGEST_LLM": "digest 的 LLM 顧問段",
    "KNOWLEDGE_DIGEST_MAX_FLAGS": "顧問旗標上限",
    "KNOWLEDGE_RETIRE_ON_CONTRADICTION": "實作推翻規則時拔掉它",
    # eval
    "EVAL_KNOWLEDGE_ROOT": "知識題的種子帳本",
    "EVAL_KNOWLEDGE_AB": "跑對照組",
    "EVAL_MIN_RECALL": "recall 下限",
    "EVAL_MAX_NOISE": "噪音率上限",
    "EVAL_MAX_FORBIDDEN": "禁語率上限",
    "EVAL_MIN_KNOWLEDGE_DELTA": "knowledge_delta 下限",
    "EVAL_MIN_DISTILL_SCORE": "蒸餾分數下限",
}
