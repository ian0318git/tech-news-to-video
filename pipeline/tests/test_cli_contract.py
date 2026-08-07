"""CLI 契約測試: typed wrappers 的解析與形狀鎖定。

用真實 CLI 回應的 envelope 形狀作為 fixture,mock 掉 subprocess。
任何「CLI 改了形狀」都會讓這裡的測試紅 — 這就是契約。
"""

import _cli
import pytest

# ---------- fixtures: 真實 CLI 回應形狀 ----------

NOTEBOOK_LIST = {
    "notebooks": [
        {
            "index": 1,
            "id": "8fb9586d-877f-4caa-8a16-caeed12f24ef",
            "title": "Test NB",
            "is_owner": True,
        }
    ]
}

NOTEBOOK_CREATE = {
    "notebook": {"id": "3777c627-39a7-483c-97ce-dfd2a36126c9", "title": "New NB"},
    "active_notebook_id": "3777c627-39a7-483c-97ce-dfd2a36126c9",
}

SOURCE_LIST = {
    "notebook_id": "3777c627-39a7-483c-97ce-dfd2a36126c9",
    "notebook_title": "Test",
    "sources": [
        {
            "index": 1,
            "id": "2e99e814-2dde-4cea-be38-4ffd3ede454c",
            "title": "The Linux Kernel Archives",
            "type": "web_page",
            "url": "https://www.kernel.org/",
            "status": "ready",
            "status_id": 2,
        }
    ],
    "count": 1,
}

SOURCE_ADD = {
    "source": {
        "id": "2e99e814-2dde-4cea-be38-4ffd3ede454c",
        "title": "The Linux Kernel Archives",
        "type": "web_page",
        "url": "https://www.kernel.org/",
    }
}

AUTH_CHECK = {
    "status": "ok",
    "account": {"email": "test@gmail.com", "authuser": 0},
    "checks": {"storage_exists": True},
}

GENERATE_DONE = '{"task_id": "ae5a27a9-6946-4d36-a0a6-bd42d64d2c6e", "status": "completed", "url": "https://x"}\n'


@pytest.fixture
def mock_run_cli(monkeypatch):
    """回傳可控的 run_cli mock: 用 .out 屬性設定回傳值。"""
    calls = []

    class Fake:
        out = "{}"

        def __call__(self, args, logger, timeout=None):
            calls.append(args)
            return self.out

    fake = Fake()
    monkeypatch.setattr(_cli, "run_cli", fake)
    return fake, calls


# ---------- notebook 契約 ----------


def test_notebook_list_contract(mock_run_cli):
    fake, calls = mock_run_cli
    fake.out = __import__("json").dumps(NOTEBOOK_LIST)
    items = _cli.notebook_list(__import__("logging").getLogger("t"))
    assert calls[0][:2] == ["list", "--json"]
    assert items[0]["id"] == "8fb9586d-877f-4caa-8a16-caeed12f24ef"


def test_notebook_list_bad_shape_fails(mock_run_cli):
    fake, _ = mock_run_cli
    fake.out = '{"unexpected": true}'
    with pytest.raises(SystemExit):
        _cli.notebook_list(__import__("logging").getLogger("t"))


def test_notebook_create_contract(mock_run_cli):
    fake, calls = mock_run_cli
    fake.out = __import__("json").dumps(NOTEBOOK_CREATE)
    nb_id = _cli.notebook_create("New NB", __import__("logging").getLogger("t"))
    assert calls[0][:3] == ["create", "New NB", "--use"]
    assert nb_id == "3777c627-39a7-483c-97ce-dfd2a36126c9"


# ---------- source 契約 ----------


def test_source_list_contract(mock_run_cli):
    fake, calls = mock_run_cli
    fake.out = __import__("json").dumps(SOURCE_LIST)
    items = _cli.source_list(__import__("logging").getLogger("t"))
    assert calls[0][:3] == ["source", "list", "--json"]
    assert items[0]["status"] == "ready"
    assert items[0]["url"] == "https://www.kernel.org/"


def test_source_add_contract(mock_run_cli):
    fake, calls = mock_run_cli
    fake.out = __import__("json").dumps(SOURCE_ADD)
    src = _cli.source_add(
        "https://www.kernel.org/", __import__("logging").getLogger("t")
    )
    assert calls[0][:3] == ["source", "add", "https://www.kernel.org/"]
    assert src["id"] == "2e99e814-2dde-4cea-be38-4ffd3ede454c"


# ---------- auth 契約 ----------


def test_auth_check_contract(mock_run_cli):
    fake, calls = mock_run_cli
    fake.out = __import__("json").dumps(AUTH_CHECK)
    result = _cli.auth_check(__import__("logging").getLogger("t"))
    assert calls[0] == ["auth", "check", "--test", "--json"]
    assert result["status"] == "ok"


def test_auth_check_missing_status_fails(mock_run_cli):
    fake, _ = mock_run_cli
    fake.out = '{"error": true}'
    with pytest.raises(SystemExit):
        _cli.auth_check(__import__("logging").getLogger("t"))


# ---------- generate 契約 ----------


def test_generate_video_contract(monkeypatch):
    logger = __import__("logging").getLogger("t")
    calls = []

    def fake_run_live(args, lg, timeout=None):
        calls.append((args, timeout))
        return GENERATE_DONE

    monkeypatch.setattr(_cli, "run_live", fake_run_live)
    result = _cli.generate_video("desc", "explainer", logger, timeout=1800)
    assert calls[0][1] == 1920  # timeout + 120 餘裕
    assert result["status"] == "completed"
    assert result["task_id"] == "ae5a27a9-6946-4d36-a0a6-bd42d64d2c6e"


def test_generate_video_no_json_returns_completed(monkeypatch):
    monkeypatch.setattr(_cli, "run_live", lambda *a, **k: "no json here\n")
    result = _cli.generate_video("d", "short", __import__("logging").getLogger("t"))
    assert result == {"status": "completed"}
