import time

from fastapi.testclient import TestClient

from curator_service.app import main
from curator_service.app.project import Project, state
from curator_service.tests.seed import seed


def make(tmp_path) -> str:
    p = Project.create(tmp_path / "api.ohm", "API")
    seed(p)
    p.save()
    p.close()
    return str(tmp_path / "api.ohm")


def test_full_flow(tmp_path):
    path = make(tmp_path)
    c = TestClient(main.app)
    assert c.get("/health").json()["ok"]
    assert c.post("/project/open", json={"path": path}).status_code == 200

    feed = c.get("/feed").json()
    blocks = [i for i in feed["items"] if i["type"] == "block"]
    assert blocks and feed["authors"]

    # drag a line to split it, then undo through the returned inverse
    r = c.put("/links/message/1007", json={"parent_id": "1005"}).json()
    assert r["dirty"] and r["units"][0]["messages"][1]["link"]["parent_id"] == "1005"
    inv = r["inverse"]
    assert c.request(inv["method"], inv["path"], json=inv["body"]).status_code == 200

    bad = c.put("/links/message/1001", json={"parent_id": "1004"})
    assert bad.status_code == 422 and bad.json()["error"] == "parent_not_earlier"

    convs = c.get("/conversations").json()["conversations"]
    first = min(convs, key=lambda x: x["first_ts"])
    assert c.post(f"/conversations/{first['id']}/approve", json={}).status_code == 409
    assert c.post(f"/conversations/{first['id']}/approve", json={"confirm": True}).json()["approved"]
    assert [x["id"] for x in c.get("/conversations?filter=approved").json()["conversations"]] == [first["id"]]

    s = c.get("/search", params={"q": "@Kaptain baro"}).json()
    assert len(s["messages"]) == 2 and s["people"]

    people = c.get("/people").json()["people"]
    kaptain = next(x for x in people if x["name"] == "KaptainDoosh")
    m = c.post(f"/people/{kaptain['author_id']}/memories", json={"text": "Plays Barotrauma with the group."}).json()
    assert m["status"] == "kept"
    assert c.patch(f"/memories/{m['id']}", json={"status": "forgotten"}).json()["status"] == "forgotten"
    assert c.post("/memories/extract", json={}).status_code == 503  # no llama-server in tests
    assert c.post("/links/guess", json={}).json()["error"] == "llm_down"

    prev = c.get("/export/preview").json()
    assert prev["examples"] > 0 and len(prev["sample"][0]) == 3
    out = c.post("/export/jsonl", json={"path": str(tmp_path / "out.jsonl")}).json()
    assert (tmp_path / "out.jsonl").read_text().count("\n") == out["examples"]

    assert c.post("/project/save").json()["dirty"] is False
    assert c.get("/project").json()["dirty"] is False


def test_models_and_simulated_training(tmp_path):
    path = make(tmp_path)
    c = TestClient(main.app)
    c.post("/project/open", json={"path": path})
    gguf = tmp_path / "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf"
    gguf.write_bytes(b"GGUF" + b"\0" * 1024)
    models = c.post("/models/base", json={"gguf_path": str(gguf)}).json()
    base = next(m for m in models["base_models"] if m["gguf_path"] == str(gguf.resolve()))
    assert base["quant"] == "Q5_K_M" and base["hf_path"] == "unsloth/gemma-4-E4B-it" and base["different_base"]
    c.put("/models/active", json={"base_model_id": base["id"]})

    conv = min(c.get("/conversations").json()["conversations"], key=lambda x: x["first_ts"])
    c.post(f"/conversations/{conv['id']}/approve", json={"confirm": True})

    pre = c.get("/train/preflight").json()
    assert pre["trainable"] and pre["export"]["examples"] > 0

    run = c.post("/train", json={}).json()
    assert run["status"] == "running"
    assert c.post("/train", json={}).json()["error"] == "run_active"
    deadline = time.time() + 60
    while time.time() < deadline:
        r = c.get(f"/train/runs/{run['id']}").json()
        if r["status"] != "running":
            break
        time.sleep(0.5)
    assert r["status"] == "succeeded", r
    assert r["losses"] and r["progress"]["overall"] > 0.8
    models = c.get("/models").json()
    assert models["active"]["adapter_id"] == r["adapter_id"]
    assert any(a["id"] == r["adapter_id"] for a in models["adapters"])

    body = c.get(f"/train/runs/{run['id']}/events").text
    assert "event: done" in body and "event: end" in body
    state.project.close()
    state.project = None
