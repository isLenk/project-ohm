import pytest

from curator_service.app import export, names
from curator_service.app.project import state

EVERYONE = [["420yolocoon", "Jake"], ["Lenk", "Lance"]]


def row(conv, speaker_text):
    return {"id": f"conv:{conv}:0:555736699364179980:0", "conversation_id": conv, "speaker": "420yolocoon",
            "speaker_id": "555736699364179980", "source_message_ids": ["964375797844566047"],
            "messages": [{"role": "system", "content": "You are Ohm."},
                         {"role": "user", "content": speaker_text},
                         {"role": "assistant", "content": "lol Lance is cooked, Ohm out"}]}


def test_for_cloud_keeps_only_masked_messages():
    rows = [row(9, "Jake: yo @Lenk, my number is 555 123 4567"), row(9, "Lenk: Jake?"), row(12, "Jake: back")]
    out = export.for_cloud(rows, EVERYONE)
    assert [set(r) for r in out] == [{"conversation_id", "messages"}] * 3
    assert [r["conversation_id"] for r in out] == [1, 1, 2]
    text = repr(out)
    for secret in ("Jake", "Lenk", "Lance", "420yolocoon", "555736699364179980", "964375797844566047", "555 123 4567"):
        assert secret not in text
    assert "You are Ohm." in text and "Ohm out" in text  # Ohm keeps his name


def test_for_cloud_stand_ins_hold_within_a_conversation_only():
    def jake(r):
        return r["messages"][1]["content"].split(":")[0]
    pairs = [export.for_cloud([row(1, "Jake: a"), row(1, "Jake: b"), row(2, "Jake: c")], EVERYONE) for _ in range(10)]
    assert all(jake(a) == jake(b) for a, b, _ in pairs)  # same person, same conversation: same stand-in
    assert any(jake(a) != jake(c) for a, _, c in pairs)  # a new conversation draws again


def test_for_cloud_refuses_without_a_name_list():
    with pytest.raises(RuntimeError, match="can't be masked"):
        export.for_cloud([row(1, "Jake: hi")], [])


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
