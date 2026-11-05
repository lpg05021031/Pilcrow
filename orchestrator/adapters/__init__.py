"""adapters —— 對外部系統的薄封裝，全部是行程內的普通函式。

S1 起 ①（ado_pr_tools）與 ③（review_result_tools）不再是獨立行程：唯一的呼叫者
就是這個 orchestrator，而且在同一台機器，MCP 那層只是多三個 port 與三份 env。
這裡的函式是同步的（httpx.Client），async 呼叫端經 clients.py 用 asyncio.to_thread 包。
"""
