from curator_service.app import export, names
from curator_service.app.project import state


def test_project_people_are_the_human_authors(tmp_path, monkeypatch):
    class P:
        def all(self, sql, params=()):
            assert "is_bot = 0" in sql
            return [{"name": "420yolocoon", "display_name": "Jake"}]
    monkeypatch.setattr(state, "project", P())
    assert names.project_people() == [["Jake", "420yolocoon"]]
    monkeypatch.setattr(state, "project", None)
    assert names.project_people() == []


def test_repeated_replies_are_capped(project, monkeypatch):
    from curator_service.app import conversations
    with project.tx() as conn:
        conversations.approve_many(conn, [r[0] for r in conn.execute("SELECT id FROM conversations")])
    before = export.build(project)
    turns = sum(e["assistant_turns"] for e in before["examples"])
    assert turns > 2 and before["dropped"]["repeated"] == 0
    monkeypatch.setattr(export, "said_as", lambda text: "bruh")  # as if every line were the same reply
    monkeypatch.setattr(export, "REPEAT_CAP", 1)
    after = export.build(project)
    assert after["dropped"]["repeated"] > 0
    assert sum(e["assistant_turns"] for e in after["examples"]) < turns
