"""全套系統唯一的設定讀取點 —— 64 個環境變數的 schema、預設值與說明住這裡。

為什麼放 shared/：shared 是「笨層」，orchestrator / tools / eval / harvester
都可以 import 它，而它不 import 任何業務模組（也不 import langfuse）。

設計決定：
  變數名不動       每個欄位的 alias 就是既有的環境變數名；部署好的 env 檔
                   一個字都不用改。日後要改名，在這裡加 AliasChoices 即可。
  env 仍是來源     測試用 monkeypatch.setenv、run_eval 直接改 os.environ 做
                   對照組 —— 這些都要繼續成立，所以不引入 TOML/YAML。
  惰性 + 可清快取  settings() 第一次呼叫才讀；reset() 清掉。tests/conftest.py
                   把 monkeypatch.setenv 綁上 reset()，既有測試零改動。
  空字串 = 未設    KNOWLEDGE_ROOT="" 是 run_eval 關掉知識迴圈的正式手法；
                   所有欄位對空字串一律當「未設、用預設值」。
  壞值就炸         型別不對（REVIEW_MAX_FINDINGS=abc）在啟動時報錯，不再
                   靜默退回預設值。app / tools / harvester 啟動時呼叫 fail_fast()。
  衍生判斷收進來   knowledge.enabled / graph.enabled / static.semgrep_enabled
                   這類「未設 = 停用」只寫一次。

用法：
  from shared.settings import settings
  settings().review.max_findings

  python -m shared.settings --show      目前生效值與來源（secrets 遮罩）
  python -m shared.settings --example   由 schema 產生 suite.env.example
  python -m shared.settings --check     只驗證；壞值回傳碼 1
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal

from pydantic import (AliasChoices, BaseModel, BeforeValidator, Field,
                      SecretStr, ValidationError, model_validator)
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# ---------- 共用的欄位型別 ----------

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def _lower(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _csv(v: Any) -> Any:
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    return v


def _tri_ssl(v: Any) -> Any:
    """ADO_VERIFY_SSL：true/false 之外的字串當 CA bundle 路徑。"""
    if isinstance(v, str):
        low = v.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        return v.strip()
    return v


def _switch(v: Any) -> Any:
    """STATIC_SEMGREP / STATIC_RUFF：auto | on | off（true/false 同義）。"""
    if isinstance(v, str):
        low = v.strip().lower()
        if low in _TRUE:
            return "on"
        if low in _FALSE:
            return "off"
        return low
    return v


Stripped = Annotated[str, BeforeValidator(_strip)]
Lowered = Annotated[str, BeforeValidator(_lower)]
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_csv)]
VerifySsl = Annotated[bool | str, BeforeValidator(_tri_ssl)]
Switch = Annotated[Literal["auto", "on", "off"], BeforeValidator(_switch)]


class _Group(BaseSettings):
    """每一組設定的基底：直接讀環境變數、認 alias、忽略不認識的鍵。"""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True,
                                      frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """環境變數設成空字串 = 沒設（用預設值）。"""
        if isinstance(data, dict):
            return {k: v for k, v in data.items()
                    if not (isinstance(v, str) and v.strip() == "")}
        return data


# ---------- 各組 ----------

class Ado(_Group):
    """Azure DevOps 連線與 diff 抓取（① ado_pr_tools、③ review_result_tools、harvester 共用）。"""

    pat: SecretStr = Field(
        SecretStr(""), alias="ADO_PAT",
        description="Azure DevOps Personal Access Token。唯讀服務至少 Code > Read；"
                    "③ 要真發留言需 Code > Read & Write。全套只填這一次。",
        json_schema_extra={"example": "changeme", "required": True})
    verify_ssl: VerifySsl = Field(
        True, alias="ADO_VERIFY_SSL",
        description="true / false，或 CA bundle 路徑（公司自簽 CA 指向系統合併包，"
                    "例如 /etc/ssl/certs/ca-certificates.crt）。")
    diff_max_files: int = Field(
        300, alias="ADO_DIFF_MAX_FILES", ge=1,
        description="一條 PR 最多納入幾個檔案。超過的檔案完全不會被抓下來——"
                    "LLM、semgrep/ruff、確定性 MISSING-TEST 都看不到。")
    diff_max_file_bytes: int = Field(
        600000, alias="ADO_DIFF_MAX_FILE_BYTES", ge=1,
        description="單檔內容上限（bytes），任一側超過就整檔略過並留註記。")
    diff_fetch_concurrency: int = Field(
        8, alias="ADO_DIFF_FETCH_CONCURRENCY", ge=1,
        description="抓檔併發度。每檔要抓 base/head 兩份；先確認 proxy/ADO 沒有連線數限制。")


class Tools(_Group):
    """①②③ 三個 MCP 工具服務的綁定位址，以及 orchestrator 怎麼找到它們。"""

    host: Stripped = Field(
        "127.0.0.1", alias="HOST",
        description="三個工具服務綁定的位址；只給同機 orchestrator 用就留 loopback。")
    ado_port: int = Field(
        8094, validation_alias=AliasChoices("ADO_TOOLS_PORT", "PORT"),
        description="① ado_pr_tools 的 port。")
    reader_port: int = Field(
        8095, validation_alias=AliasChoices("READER_TOOLS_PORT", "PORT"),
        description="② repo_reader_tools 的 port。")
    result_port: int = Field(
        8096, validation_alias=AliasChoices("RESULT_TOOLS_PORT", "PORT"),
        description="③ review_result_tools 的 port。")
    ado_url_override: str | None = Field(
        None, alias="ADO_TOOLS_URL",
        description="orchestrator 呼叫 ① 的 URL；未設 = http://127.0.0.1:<ADO_TOOLS_PORT>/mcp。")
    reader_url_override: str | None = Field(
        None, alias="READER_TOOLS_URL",
        description="orchestrator 呼叫 ② 的 URL；未設 = 由 READER_TOOLS_PORT 推得。")
    result_url_override: str | None = Field(
        None, alias="RESULT_TOOLS_URL",
        description="orchestrator 呼叫 ③ 的 URL；未設 = 由 RESULT_TOOLS_PORT 推得。")
    dry_run: bool = Field(
        True, alias="DRY_RUN",
        description="③ 收到 review 稿是否只預覽不真發。內測期間保持 true（安全氣囊）。")
    knowledge_post: bool = Field(
        False, alias="KNOWLEDGE_POST",
        description="③ 收到知識蒸餾稿是否真貼到 PR。跟 DRY_RUN 刻意分家，可先行試點。")

    @property
    def ado_url(self) -> str:
        return self.ado_url_override or f"http://127.0.0.1:{self.ado_port}/mcp"

    @property
    def reader_url(self) -> str:
        return self.reader_url_override or f"http://127.0.0.1:{self.reader_port}/mcp"

    @property
    def result_url(self) -> str:
        return self.result_url_override or f"http://127.0.0.1:{self.result_port}/mcp"


class Llm(_Group):
    """OpenAI 相容的 LLM gateway。"""

    base_url: Stripped = Field(
        "http://localhost:8000", alias="LLM_BASE_URL",
        description="填到「/chat/completions 的上一層」（帶不帶 /v1 以實測為準）。",
        json_schema_extra={"example": "http://llm-gateway.internal", "required": True})
    api_key: SecretStr = Field(
        SecretStr("not-needed"), alias="LLM_API_KEY",
        description="gateway 不驗 key 就留 not-needed。")
    model: Stripped = Field(
        "gpt-4o-mini", alias="LLM_MODEL",
        description="planner / reviewer / distiller 用的模型。",
        json_schema_extra={"example": "changeme", "required": True})
    model_adversary: str | None = Field(
        None, alias="LLM_MODEL_ADVERSARY",
        description="adversary 專用模型（換一顆腦去自我同意偏差）；未設 = LLM_MODEL。")
    model_digest: str | None = Field(
        None, alias="LLM_MODEL_DIGEST",
        description="收口 digest 顧問分析專用模型；未設 = LLM_MODEL。")
    timeout_s: float = Field(
        180.0, alias="LLM_TIMEOUT_S", gt=0,
        description="單次 LLM 呼叫逾時秒數；逾時重試一次後把例外交還呼叫端走降級。")


class Langfuse(_Group):
    """Langfuse（trace / prompt 版控 / eval）。SDK 自己讀這三個變數；這裡只為了 --show 與 doctor 看得見。"""

    host: Stripped = Field("http://localhost:3000", alias="LANGFUSE_HOST",
                           description="Langfuse 位址。",
                           json_schema_extra={"required": True})
    public_key: Stripped = Field("", alias="LANGFUSE_PUBLIC_KEY",
                                 description="pk-lf-…",
                                 json_schema_extra={"example": "pk-lf-changeme", "required": True})
    secret_key: SecretStr = Field(SecretStr(""), alias="LANGFUSE_SECRET_KEY",
                                  description="sk-lf-…",
                                  json_schema_extra={"example": "sk-lf-changeme", "required": True})


class Orchestrator(_Group):
    """review-orchestrator（8080）的服務層旋鈕。"""

    pipeline: Annotated[Literal["v1", "v2"], BeforeValidator(_lower)] = Field(
        "v1", alias="REVIEW_PIPELINE",
        description="v1 = 單次 LLM 呼叫（回滾用）；v2 = anatomy→static→plan→平行 reviewer"
                    "→adversary→synthesis。切 v2 前先跑過 eval 基線。")
    auto_post: bool = Field(
        False, alias="AUTO_POST",
        description="review 完是否呼叫 ③ 回貼。需同時 DRY_RUN=false 才會真發（雙保險）。")
    dedupe_ttl: float = Field(
        120.0, alias="REVIEW_DEDUPE_TTL", ge=0,
        description="同一條 PR 在幾秒內重複觸發時，重播快取而不重跑。")
    api_key: SecretStr = Field(
        SecretStr(""), alias="ORCH_API_KEY",
        description="設了就要求 Bearer；/eval-webhook 刻意不驗。用到 K 段重蒸就必須設"
                    "（/knowledge/redistill 有 LLM 花費與寫檔語意）。harvester 讀同一個值。")
    url: Stripped = Field(
        "http://127.0.0.1:8080", alias="ORCH_URL",
        description="harvester 打重蒸請求的位址（同機 loopback；不走 proxy）。")

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


class Review(_Group):
    """v2 pipeline 旋鈕。全部進 trace metadata；改任何一顆先過 eval。"""

    max_dimensions: int = Field(
        8, alias="REVIEW_MAX_DIMENSIONS", ge=2,
        description="planner 最多開幾科（Coverage Sweep 可額外 +1）。開多科比把單科預算拉大好。")
    max_parallel: int = Field(
        6, alias="REVIEW_MAX_PARALLEL", ge=1,
        description="reviewer / adversary 的 LLM 併發上限。")
    llm_base_conf: float = Field(
        0.7, alias="REVIEW_LLM_BASE_CONF", ge=0, le=1,
        description="LLM finding 的基礎信心；adversary confirmed ×1.25 / rejected ×0.35。")
    min_confidence: float = Field(
        0.55, alias="REVIEW_MIN_CONFIDENCE", ge=0, le=1,
        description="低於此分的 finding 在 synthesis 被過濾。")
    max_findings: int = Field(
        30, alias="REVIEW_MAX_FINDINGS", ge=1,
        description="報告輸出上限（純輸出，不影響任何 prompt 長度）。")
    adversary: bool = Field(
        True, alias="REVIEW_ADVERSARY",
        description="關掉 = 跳過對抗段（A/B 用）。")
    adversary_chunk: int = Field(
        6, alias="ADVERSARY_CHUNK", ge=1,
        description="adversary 每次呼叫最多審幾條（分塊防注意力稀釋）。")
    reviewer_rules_max_chars: int = Field(
        20000, alias="REVIEWER_RULES_MAX_CHARS", ge=0,
        description="每個 reviewer 收到的規則文字上限。")
    reviewer_slices_max_chars: int = Field(
        30000, alias="REVIEWER_SLICES_MAX_CHARS", ge=0,
        description="每一科的 diff 切片總預算；超預算的檔降級 static-only 並進 slices_skipped。"
                    "⚠ 改這個數會改變 golden set 的語意。")
    reviewer_file_slice_max_chars: int = Field(
        6000, alias="REVIEWER_FILE_SLICE_MAX_CHARS", ge=0,
        description="單一檔案的切片上限（超過就檔內截斷）。")


class Static(_Group):
    """確定性靜態掃描（semgrep / ruff / MISSING-TEST）。"""

    semgrep: Switch = Field(
        "auto", alias="STATIC_SEMGREP",
        description="auto = 裝了就跑；on = 要跑（沒裝仍只能跳過並註記）；off = 不跑。"
                    "注意 systemd 的 PATH 要含 venv/bin 才找得到。")
    ruff: Switch = Field("auto", alias="STATIC_RUFF", description="同 STATIC_SEMGREP。")
    timeout: int = Field(
        300, alias="STATIC_TIMEOUT", ge=1,
        description="掃描逾時秒數。journal 出現 timed out 就往上調。")
    missing_test: bool = Field(
        True, alias="STATIC_MISSING_TEST",
        description="確定性 MISSING-TEST（diff 有新宣告卻零測試檔 → conf 1.0 保底）。")

    @property
    def semgrep_enabled(self) -> bool:
        return self.semgrep != "off" and shutil.which("semgrep") is not None

    @property
    def ruff_enabled(self) -> bool:
        return self.ruff != "off" and shutil.which("ruff") is not None


class Repos(_Group):
    """本地乾淨 clone（base 上下文）與 ② repo_reader 的 repo 佈局。"""

    repos_root: Path | None = Field(
        None, alias="REPOS_ROOT",
        description="REPOS_ROOT/<repo小寫>/ 一 repo 一顆 clone（target branch），"
                    "repo-sync timer 保鮮。未設 = 沒有 base 上下文，review 照跑。",
        json_schema_extra={"example": "/opt/repos"})
    repo_root: Path | None = Field(
        None, alias="REPO_ROOT",
        description="【已淘汰】舊版單 repo 模式，只有 ② 還認。改用 REPOS_ROOT。")
    context_lines: int = Field(
        6, alias="CONTEXT_LINES", ge=0,
        description="每個 hunk 從 base 取舊行號附近 ±N 行。")
    context_max_chars: int = Field(
        24000, alias="CONTEXT_MAX_CHARS", ge=0,
        description="base 上下文與 graph 摘要的預算。會進「每一科」的 reviewer prompt。")

    def root_for(self, repo: str | None) -> Path | None:
        """REPOS_ROOT/<repo小寫>/，存在才回；否則 None（呼叫端優雅降級）。"""
        if not self.repos_root or not repo:
            return None
        p = (self.repos_root / repo.strip().lower()).resolve()
        return p if p.is_dir() else None


class Graph(_Group):
    """④ code graph（選配）。GRAPH_TOOLS_URL 與 GRAPH_TOOL_BLAST 都設了才啟用。"""

    tools_url: str | None = Field(
        None, alias="GRAPH_TOOLS_URL",
        description="code-review-graph serve --http 的 MCP 位址。",
        json_schema_extra={"example": "http://127.0.0.1:8097/mcp"})
    tool_blast: str | None = Field(
        None, alias="GRAPH_TOOL_BLAST",
        description="影響半徑工具名（裝好後 list tools 確認）。",
        json_schema_extra={"example": "get_impact_radius_tool"})
    tool_blast_args: str = Field(
        '{"files": $files}', alias="GRAPH_TOOL_BLAST_ARGS",
        description="參數 JSON 樣板；$files / $repo / $repo_dir 會被代入。含 $ 與空白，"
                    "被 shell source 時要用單引號。多 repo 自動切庫："
                    '{"changed_files": $files, "repo_root": "<REPOS_ROOT>/$repo_dir"}')
    repo: str | None = Field(
        None, alias="GRAPH_REPO",
        description="graph server 綁定的 repo 名（小寫）；設了之後其他 repo 自動跳過 graph。"
                    "多 repo 切庫模式留空。")

    @property
    def enabled(self) -> bool:
        return bool(self.tools_url and self.tool_blast)

    @property
    def bound_repo(self) -> str:
        return (self.repo or "").strip().lower()


class Rules(_Group):
    """團隊規則 repo（本機 clone）。RULES_ROOT 未設 = 規則功能整個靜默停用。"""

    root: Path | None = Field(
        None, alias="RULES_ROOT",
        description="規則 repo 的本機 clone；每次 review 用到的 commit SHA 會進 trace。",
        json_schema_extra={"example": "/opt/pr-review-rules"})
    subdir: Stripped = Field("rules", alias="RULES_SUBDIR",
                             description="規則目錄名（<RULES_ROOT>/rules/）。")
    common_dir: Stripped = Field("common", alias="RULES_COMMON_DIR",
                                 description="每次 review 必載的子目錄名。")
    max_chars: int = Field(60000, alias="RULES_MAX_CHARS", ge=0,
                           description="組進 prompt 的規則總量上限（字元）。")

    @property
    def enabled(self) -> bool:
        return self.root is not None


class Knowledge(_Group):
    """知識迴圈（K 段，選配）。KNOWLEDGE_ROOT 未設 = 注入與蒸餾皆靜默停用。"""

    root: Path | None = Field(
        None, alias="KNOWLEDGE_ROOT",
        description="本地 knowledge repo 路徑（harvester timer 是唯一 commit 者）。",
        json_schema_extra={"example": "/opt/pr-review-knowledge"})
    branches: CsvList = Field(
        ["master", "main"], alias="KNOWLEDGE_BRANCHES",
        description="進帳本的目標分支（逗號分隔、支援萬用字元，例：master,release/*）。")
    max_chars: int = Field(
        18000, alias="KNOWLEDGE_MAX_CHARS", ge=0,
        description="Stage 04 注入區塊總上限；同樣會進 reviewer prompt。")
    distill_diff_max_chars: int = Field(
        100000, alias="DISTILL_DIFF_MAX_CHARS", ge=0,
        description="蒸餾看得到的 diff 切片預算；超預算的檔會留註記並進 distill_slices_skipped。")
    redistill: bool = Field(
        True, alias="KNOWLEDGE_REDISTILL",
        description="stale 重蒸（draft 之後又有 push → 用最終 diff 重蒸一次，≤2 次）。"
                    "false = 回到 stale 直接丟稿。")
    cycle_source: Stripped = Field(
        "release/*", alias="KNOWLEDGE_CYCLE_SOURCE",
        description="週期驗收 PR 的 source 分支樣式；符合者不蒸餾、作為收口儀式。")
    cycle_target: Stripped = Field(
        "master", alias="KNOWLEDGE_CYCLE_TARGET",
        description="週期驗收 PR 的 target 分支。")
    digest: bool = Field(
        True, alias="KNOWLEDGE_DIGEST",
        description="收口 digest 要不要貼到驗收 PR（false = 全暗影子模式：照樣落地、不發留言）。")
    digest_llm: bool = Field(
        True, alias="KNOWLEDGE_DIGEST_LLM",
        description="digest 的「Needs a look」顧問段（LLM 提案，落地路徑不讀）。false = 純清單。")
    digest_max_flags: int = Field(
        12, alias="KNOWLEDGE_DIGEST_MAX_FLAGS", ge=1,
        description="顧問旗標上限（防洗版）。")
    retire_on_contradiction: bool = Field(
        True, alias="KNOWLEDGE_RETIRE_ON_CONTRADICTION",
        description="實作推翻規則時收口是否拔掉那條不變式（沉默即拔；回 keep <ID> 否決）。"
                    "false = 只提案不執行。")

    @property
    def enabled(self) -> bool:
        return self.root is not None and self.root.is_dir()


class Eval(_Group):
    """離線考試（run_eval / run_distill_eval）。門檻未設 = report-only。"""

    knowledge_root: Path | None = Field(
        None, alias="EVAL_KNOWLEDGE_ROOT",
        description="知識依賴題的種子帳本；未設 = eval/fixtures/knowledge。")
    knowledge_ab: bool = Field(
        True, alias="EVAL_KNOWLEDGE_AB",
        description="對照組（同一批知識題再跑一次 KNOWLEDGE_ROOT=\"\"）；關掉就沒有 delta。")
    min_recall: float = Field(0.0, alias="EVAL_MIN_RECALL", ge=0, le=1,
                              description="CI gate：must_catch_recall 下限。")
    max_noise: float | None = Field(None, alias="EVAL_MAX_NOISE", ge=0,
                                    description="CI gate：noise rate 上限。")
    max_forbidden: float | None = Field(None, alias="EVAL_MAX_FORBIDDEN", ge=0,
                                        description="CI gate：forbidden-hit rate 上限。")
    min_knowledge_delta: float | None = Field(
        None, alias="EVAL_MIN_KNOWLEDGE_DELTA",
        description="knowledge_delta 的門檻。先 report-only，基線穩定兩輪再設。")
    min_distill_score: float | None = Field(
        None, alias="EVAL_MIN_DISTILL_SCORE", ge=0, le=1,
        description="蒸餾考卷的門檻，同樣先 report-only。")


# ---------- 總表 ----------

class Settings(BaseModel):
    model_config = {"frozen": True}

    ado: Ado = Field(default_factory=Ado)
    tools: Tools = Field(default_factory=Tools)
    llm: Llm = Field(default_factory=Llm)
    langfuse: Langfuse = Field(default_factory=Langfuse)
    orchestrator: Orchestrator = Field(default_factory=Orchestrator)
    review: Review = Field(default_factory=Review)
    static: Static = Field(default_factory=Static)
    repos: Repos = Field(default_factory=Repos)
    graph: Graph = Field(default_factory=Graph)
    rules: Rules = Field(default_factory=Rules)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    eval: Eval = Field(default_factory=Eval)

    def knobs(self) -> dict[str, Any]:
        """進 trace metadata 的旋鈕快照（影響 review 結果的那幾組；無 secrets）。"""
        out: dict[str, Any] = {}
        for grp in (self.orchestrator, self.review, self.static,
                    self.repos, self.graph, self.rules, self.knowledge):
            for name, info in type(grp).model_fields.items():
                v = getattr(grp, name)
                if isinstance(v, SecretStr):
                    continue
                out[env_names(info)[0]] = str(v) if isinstance(v, Path) else v
        return out


GROUP_ORDER = ("ado", "tools", "llm", "langfuse", "orchestrator", "review",
               "static", "repos", "graph", "rules", "knowledge", "eval")

_cached: Settings | None = None


def settings() -> Settings:
    """惰性單例。環境變數變了要先 reset()（tests/conftest.py 已自動綁上）。"""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset() -> None:
    global _cached
    _cached = None


def fail_fast() -> Settings:
    """服務啟動時呼叫：壞值立刻以人話報錯並退出，而不是在第一條 review 中段才炸。"""
    try:
        reset()
        return settings()
    except ValidationError as e:
        print("設定值有誤，服務不啟動：", file=sys.stderr)
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            print(f"  {loc}: {err['msg']}（收到 {err.get('input')!r}）", file=sys.stderr)
        raise SystemExit(1)


# ---------- schema 反射：--show / --example / doctor 共用 ----------

def env_names(info: FieldInfo) -> list[str]:
    """一個欄位認得的環境變數名（第一個是正名）。"""
    va = info.validation_alias
    if isinstance(va, AliasChoices):
        return [c if isinstance(c, str) else ".".join(map(str, c.path))
                for c in va.choices]
    if isinstance(va, str):
        return [va]
    return [info.alias] if info.alias else []


def iter_fields() -> Iterator[tuple[str, type[_Group], str, FieldInfo]]:
    """(group 屬性名, group 類, 欄位名, FieldInfo)，依 GROUP_ORDER。"""
    for gname in GROUP_ORDER:
        gcls = Settings.model_fields[gname].annotation
        for fname, info in gcls.model_fields.items():
            yield gname, gcls, fname, info


def _render_value(v: Any) -> str:
    if isinstance(v, SecretStr):
        return v.get_secret_value()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ",".join(v)
    if v is None:
        return ""
    return str(v)


def _mask(v: Any) -> str:
    if isinstance(v, SecretStr):
        raw = v.get_secret_value()
        return "(unset)" if not raw else f"***{raw[-4:]}" if len(raw) > 8 else "***"
    return _render_value(v) or "(unset)"


def example_text() -> str:
    """由 schema 產出 suite.env.example（四個行程共用一份）。"""
    lines = [
        "# pr-review-suite 全套設定 —— 由 `python -m shared.settings --example` 產生，",
        "# 不要手改這個 .example；改 shared/settings.py 的 Field 再重生。",
        "# 複製為 /etc/pr-review-suite/suite.env（chmod 600），十個 systemd unit 都指這一份。",
        "#",
        "# systemd 的 EnvironmentFile 不支援行內註解：註解一律獨立成行，",
        "# 等號右邊不可有空白或 # 尾巴（血淚：URL 會出現 %20）。含 $ 或空白的值用單引號。",
        "# 註解掉的行代表「未設 = 預設值 / 停用」；要用再打開。",
        "",
    ]
    for gname in GROUP_ORDER:
        gcls = Settings.model_fields[gname].annotation
        lines.append(f"# {'=' * 72}")
        lines.append(f"# [{gname}] {(gcls.__doc__ or '').strip()}")
        lines.append(f"# {'=' * 72}")
        for fname, info in gcls.model_fields.items():
            names = env_names(info)
            extra = info.json_schema_extra or {}
            example = extra.get("example") if isinstance(extra, dict) else None
            default = info.get_default(call_default_factory=True)
            lines.append("")
            for d in (info.description or "").split("。"):
                d = d.strip()
                if d:
                    lines.append(f"# {d}。" if not d.endswith(("）", ")", "\"")) else f"# {d}")
            if len(names) > 1:
                lines.append(f"# （舊名 {', '.join(names[1:])} 仍認）")
            val = example if example is not None else _render_value(default)
            needs_quote = any(ch in val for ch in " $") if val else False
            shown = f"'{val}'" if needs_quote else val
            required = isinstance(extra, dict) and extra.get("required")
            if required:
                # 一定要填的（PAT、gateway、模型）：打開、放佔位值
                lines.append(f"{names[0]}={shown}")
            elif val == "" or example is not None or "已淘汰" in (info.description or ""):
                # 未設=停用的選配、範例值不是真預設、已淘汰：留成註解，使用者自己打開
                lines.append(f"# {names[0]}={shown}")
            else:
                lines.append(f"{names[0]}={shown}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def show_rows(s: Settings | None = None) -> list[tuple[str, str, str, str]]:
    """(group, ENV, 生效值(遮罩), 來源) —— 給 --show 與 doctor。"""
    s = s or settings()
    rows = []
    for gname, _gcls, fname, info in iter_fields():
        names = env_names(info)
        src = next((n for n in names if os.getenv(n, "") != ""), None)
        rows.append((gname, names[0], _mask(getattr(getattr(s, gname), fname)),
                     f"env:{src}" if src else "default"))
    return rows


def warnings_for(s: Settings | None = None) -> list[str]:
    """設定層就看得出來的問題（不打網路；那是 doctor 的事）。"""
    s = s or settings()
    out: list[str] = []
    if os.getenv("REPO_ROOT", ""):
        out.append("REPO_ROOT 已淘汰，只有 ② 還認；改用 REPOS_ROOT。")
    if os.getenv("PORT", "") and not any(os.getenv(n, "") for n in
                                         ("ADO_TOOLS_PORT", "READER_TOOLS_PORT",
                                          "RESULT_TOOLS_PORT")):
        out.append("偵測到舊式 PORT：單一 env 檔請改用 ADO_TOOLS_PORT / "
                   "READER_TOOLS_PORT / RESULT_TOOLS_PORT，否則三個服務會搶同一個 port。")
    if s.orchestrator.auto_post and s.tools.dry_run:
        out.append("AUTO_POST=true 但 DRY_RUN=true：review 會「假發」——③ 只回預覽。"
                   "（這是出廠安全側，確認要真發再翻 DRY_RUN）")
    if s.knowledge.root is not None and not s.knowledge.root.is_dir():
        out.append(f"KNOWLEDGE_ROOT 指向不存在的目錄：{s.knowledge.root}（K 段視同停用）")
    if s.rules.root is not None and not s.rules.root.is_dir():
        out.append(f"RULES_ROOT 指向不存在的目錄：{s.rules.root}（規則視同停用）")
    if s.repos.repos_root is not None and not s.repos.repos_root.is_dir():
        out.append(f"REPOS_ROOT 指向不存在的目錄：{s.repos.repos_root}（沒有 base 上下文）")
    if s.graph.tools_url and not s.graph.tool_blast:
        out.append("GRAPH_TOOLS_URL 有設但 GRAPH_TOOL_BLAST 沒設：graph 不會啟用。")
    if s.knowledge.redistill and s.knowledge.root and not s.orchestrator.api_key.get_secret_value():
        out.append("KNOWLEDGE_REDISTILL=true 但 ORCH_API_KEY 未設：/knowledge/redistill 無認證。")
    return out


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m shared.settings")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true", help="目前生效值與來源（secrets 遮罩）")
    g.add_argument("--example", action="store_true", help="由 schema 產生 suite.env.example")
    g.add_argument("--check", action="store_true", help="只驗證；壞值回傳碼 1")
    a = ap.parse_args(argv)

    if a.example:
        sys.stdout.write(example_text())
        return 0
    try:
        s = Settings()
    except ValidationError as e:
        print("設定值有誤：")
        for err in e.errors():
            print(f"  {'.'.join(map(str, err['loc']))}: {err['msg']}（收到 {err.get('input')!r}）")
        return 1
    if a.check:
        print("設定 OK")
    else:
        w = max(len(r[1]) for r in show_rows(s))
        cur = None
        for grp, name, val, src in show_rows(s):
            if grp != cur:
                print(f"\n[{grp}]")
                cur = grp
            print(f"  {name:<{w}}  {val:<40}  {src}")
    warns = warnings_for(s)
    if warns:
        print("\n注意：")
        for ln in warns:
            print(f"  - {ln}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
