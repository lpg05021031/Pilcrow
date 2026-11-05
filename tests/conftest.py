"""測試共用：設定快取與環境變數綁死。

shared.settings.settings() 是惰性單例；測試用 monkeypatch.setenv 改環境變數後
必須讓它重讀。這裡把 MonkeyPatch.setenv / delenv 綁上 reset()，既有 60+ 條測試
不用改一行；每條測試開頭與結尾也各清一次，測試之間不會互相汙染。
"""
import pytest

import shared.settings as S


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch, tmp_path):
    S.reset()
    # review 歷史（SQLite）與 config.toml 永遠寫在這條測試自己的 tmp 裡，不碰真路徑
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "cfg"))
    orig_set, orig_del = pytest.MonkeyPatch.setenv, pytest.MonkeyPatch.delenv

    def setenv(self, *a, **k):
        orig_set(self, *a, **k)
        S.reset()

    def delenv(self, *a, **k):
        orig_del(self, *a, **k)
        S.reset()

    monkeypatch.setattr(pytest.MonkeyPatch, "setenv", setenv)
    monkeypatch.setattr(pytest.MonkeyPatch, "delenv", delenv)
    yield
    S.reset()
