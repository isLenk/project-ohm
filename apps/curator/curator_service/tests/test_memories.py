import re

import requests

from curator_service.app import llm, memories


def _said(prompt: str) -> list:
    """[everything before, what the person said]. The header names the date, so split on it."""
    return re.split(r" said on [^\n]*:\n", prompt, maxsplit=1)


class FakeJob:
    cancelled = False

    def progress(self, *a, **k):
        pass


def test_extract_with_fake_llm(project, monkeypatch):
    answers = {
        "I’m down to baro": {"note": "KaptainDoosh plays Barotrauma with the group.", "category": "fact", "short_lived": False},
        "baro with me and arnav": {"note": "KaptainDoosh plays with someone called arnav.", "category": "relationship",
                                   "short_lived": False, "mentions_person": "arnav"},
        "Mb I was out": {"note": "KaptainDoosh was out all day.", "category": "fact", "short_lived": True},
    }

    prompts = []

    def fake(system, user, max_tokens=300):
        prompts.append(user)
        said = _said(user)[1]  # match only their own messages, not the context before them
        for needle, answer in answers.items():
            if needle in said:
                return answer
        return {"note": None}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    result = memories.extract(project, FakeJob())
    assert result["created"] == 2  # the 3-line block yields one note; the mass ping another
    assert all("Context, earlier in the chat:" in u for u in prompts)

    with project.lock:
        mems = memories.listing(project.conn, "100", "new")
    texts = {m["text"]: m for m in mems}
    assert "KaptainDoosh plays with someone called arnav." in texts
    arnav = texts["KaptainDoosh plays with someone called arnav."]
    assert arnav["category"] == "relationship" and arnav["mentioned_name"] == "arnav" and arnav["sources"]

    # running again scans nothing new
    assert memories.extract(project, FakeJob())["blocks"] == 0

    with project.tx() as conn:
        kept = memories.keep_all(conn, "100")
    assert kept >= 1
    with project.lock:
        assert all(not m["short_lived"] for m in memories.listing(project.conn, "100", "kept"))


def test_sync_needs_memory_service(project):
    import pytest

    from curator_service.app.errors import ApiError

    with pytest.raises(ApiError) as e:
        with project.tx() as conn:
            memories.sync(conn, "ohm")
    assert e.value.code == "memory_service_off"


def test_sync_sends_the_export_avatar(project, monkeypatch):
    """The NAS gets each person's Discord avatar with their name, but not a local file from a media export."""
    sent = []

    class Res:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": len(sent)}

    class Session:
        def put(self, url, json, timeout):
            sent.append((url, json))
            return Res()

        def post(self, url, json, timeout):
            return Res()

    monkeypatch.setattr(memories, "MEMORY_API_URL", "http://nas")
    monkeypatch.setattr(memories, "_session", Session)
    with project.tx() as conn:
        authors = [r["id"] for r in conn.execute("SELECT id FROM authors ORDER BY id LIMIT 2")]
        web, local = authors
        conn.execute("UPDATE authors SET avatar_url = 'https://cdn.discordapp.com/avatars/1/a.png' WHERE id = ?", (web,))
        conn.execute("UPDATE authors SET avatar_url = 'media/avatars/b.png' WHERE id = ?", (local,))
        for a in authors:
            conn.execute("INSERT INTO memories (author_id, category, text, status, created_at, updated_at) "
                         "VALUES (?, 'fact', 'Plays Barotrauma.', 'kept', 0, 0)", (a,))
        assert memories.sync(conn, "ohm")["synced"] == 2
    users = {url.rsplit("/", 1)[1]: body for url, body in sent}
    assert users[web]["avatar_url"] == "https://cdn.discordapp.com/avatars/1/a.png"
    assert "avatar_url" not in users[local]


def test_sync_stops_and_says_so_when_the_key_is_rejected(project, monkeypatch):
    calls = []

    class Session:
        def put(self, url, json, timeout):
            calls.append(url)
            res = requests.Response()
            res.status_code = 401
            res.url = url
            return res

    monkeypatch.setattr(memories, "MEMORY_API_URL", "http://nas")
    monkeypatch.setattr(memories, "_session", Session)
    with project.tx() as conn:
        for a in [r["id"] for r in conn.execute("SELECT id FROM authors ORDER BY id LIMIT 2")]:
            conn.execute("INSERT INTO memories (author_id, category, text, status, created_at, updated_at) "
                         "VALUES (?, 'fact', 'Plays Barotrauma.', 'kept', 0, 0)", (a,))
        r = memories.sync(conn, "ohm")
    assert len(calls) == 1  # every other row would fail the same way
    assert r["synced"] == 0 and r["failed"] == 1 and r["remaining"] == 2
    assert "API key" in r["error"]


def test_extract_leaves_failed_blocks_unscanned(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)

    def broken(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(llm, "chat_json", broken)
    r = memories.extract(project, FakeJob())
    assert r["failed"] > 0 and r["created"] == 0
    scanned = project.scalar("SELECT COUNT(*) FROM memory_scans")
    assert scanned == r["blocks"] - r["failed"]  # only the too-short blocks


def test_vague_notes_are_dropped(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"note": "KaptainDoosh is down to participate.", "category": "fact"})
    assert memories.extract(project, FakeJob())["created"] == 0


def test_extract_reads_newest_blocks_first(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    seen = []
    monkeypatch.setattr(llm, "chat_json", lambda s, u, **k: seen.append(_said(u)[1]) or {"note": None})
    memories.extract(project, FakeJob())

    blocks = project.all(
        "SELECT b.id FROM blocks b JOIN authors a ON a.id = b.author_id WHERE a.is_bot = 0 ORDER BY b.first_ts DESC, b.id DESC"
    )
    texts = []
    for b in blocks:
        msgs = project.all("SELECT content_text FROM messages WHERE block_id = ? AND excluded = 0 ORDER BY ts", (b["id"],))
        text = "\n".join(m["content_text"] for m in msgs)
        if len(text.split()) >= memories.MIN_WORDS:
            texts.append(text)
    assert seen == texts


def test_notes_about_someone_else_are_dropped(project, monkeypatch):
    # unanticipatedinflation asks "we running barotrauma this night? @KaptainDoosh"; a note about
    # KaptainDoosh must not be filed under them
    systems = {}

    def fake(system, user, **k):
        said = _said(user)[1]
        systems[said] = system
        if "barotrauma this night" in said:
            return {"note": "KaptainDoosh is suggesting they run Barotrauma this weekend.", "category": "fact"}
        return {"note": None}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    assert memories.extract(project, FakeJob())["created"] == 0
    asked = next(s for said, s in systems.items() if "barotrauma this night" in said)
    assert "about unanticipatedinflation" in asked and "KaptainDoosh" not in asked


def test_misfiled_new_notes_are_forgotten_and_read_again(project, monkeypatch):
    block = project.scalar("SELECT block_id FROM messages WHERE id = '1003'")
    with project.tx() as conn:
        wrong = memories.add(conn, "102", "fact", "KaptainDoosh is suggesting they run Barotrauma.", source_ids=("1003",))
        kept = memories.add(conn, "102", "fact", "KaptainDoosh plays Barotrauma.", status="kept", source_ids=("1003",))
        conn.execute("INSERT INTO memory_scans (block_id, scanned_at) VALUES (?, 0)", (block,))

    asked = []
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda s, u, **k: asked.append(u) or {"note": None})
    r = memories.extract(project, FakeJob())

    assert r["dropped"] == 1
    assert any("barotrauma this night" in _said(u)[1] for u in asked)  # read again
    ids = {row["id"] for row in project.all("SELECT id FROM memories")}
    assert wrong not in ids and kept in ids  # the user's reviewed notes are left alone


def _prompts(project, monkeypatch) -> dict:
    """What the model is shown for each block, keyed by what the person said."""
    seen = {}

    def fake(system, user, **k):
        context, said = _said(user)
        seen[said] = context
        return {"note": None}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    memories.extract(project, FakeJob())
    return seen


def test_context_is_the_half_hour_before(project, monkeypatch):
    seen = _prompts(project, monkeypatch)
    # 1003 at 18:58 sees the conversation from 18:38 on, but not 1000 at 18:12 (46 minutes earlier)
    ctx = next(c for said, c in seen.items() if "barotrauma this night" in said)
    assert "depends on other coons" in ctx and "We running it this weekend" not in ctx
    # 1006 at 21:50: nothing in the half hour before, but what it replies to is still shown
    ctx = next(c for said, c in seen.items() if "I was out all day" in said)
    assert "Baro could hit" not in ctx  # 20:31
    assert "(replied to): we running barotrauma this night?" in ctx


def test_context_skips_files_and_bare_pings(project, monkeypatch):
    with project.tx() as conn:
        conn.execute("UPDATE messages SET attachments = ? WHERE id = '1001'",
                     ('[{"type": "attachment", "name": "clip.mp4", "url": null, "size": null}]',))
        conn.execute("UPDATE messages SET content_text = '@KaptainDoosh @ELEVIST' WHERE id = '1002'")
    ctx = next(c for said, c in _prompts(project, monkeypatch).items() if "barotrauma this night" in said)
    assert "depends on other coons" not in ctx and "@KaptainDoosh @ELEVIST" not in ctx


def test_priority_reads_the_wording():
    assert memories.priority("Lenk is studying nursing at college.", False) == "important"
    assert memories.priority("Lenk likes to play pickleball.", False) == "interest"
    assert memories.priority("Lenk thinks the movie was bad.", False) == "opinion"
    assert memories.priority("Lenk is heading out to pick up Travis.", False) == "short_lived"
    assert memories.priority("Lenk has the original radio.", False) == "other"
    assert memories.priority("Lenk has two cats.", True) == "short_lived"  # the model's flag wins


def test_same_memories_are_grouped_and_merged(project, monkeypatch):
    import numpy as np

    from curator_service.app import embeddings

    def vec(*xs):
        v = np.array(xs, dtype="float32")
        return v / np.linalg.norm(v)

    with project.tx() as conn:
        a = memories.add(conn, "100", "fact", "KaptainDoosh is getting the alcohol from Kentucky.", embedding=vec(1, 0, 0))
        b = memories.add(conn, "100", "fact", "KaptainDoosh is getting the alc from Kentucky.", embedding=vec(1, 0.2, 0))
        c = memories.add(conn, "100", "fact", "KaptainDoosh plays Barotrauma.", embedding=vec(0, 1, 0))
        conn.execute("INSERT INTO memory_sources SELECT ?, id FROM messages WHERE author_id = '100' LIMIT 1", (b,))
    with project.lock:
        groups = {m["id"]: m["group"] for m in memories.listing(project.conn, "100", "new")}
    assert groups[a] == groups[b] != groups[c]

    with project.tx() as conn:
        out = memories.merge(conn, groups[a], [a, b])
    assert out["sources"]  # b's message moved over
    with project.lock:
        ids = {m["id"] for m in memories.listing(project.conn, "100", "new")}
    assert ids == {groups[a], c}
    assert embeddings  # imported for the blob helpers add() uses


def test_around_returns_neighbours(project):
    with project.lock:
        mid = project.conn.execute("SELECT id FROM messages ORDER BY ts LIMIT 1 OFFSET 3").fetchone()[0]
        out = memories.around(project.conn, mid, before=2, after=1)
    ids = [m["id"] for m in out["messages"]]
    assert mid in ids and len(ids) <= 4 and out["messages"][0]["author"]["name"]


def test_misreadings_are_dropped(project, monkeypatch):
    # One answer per KaptainDoosh block (the seed has four).
    answers = {
        "Mb I was out": {"meaning": "asks", "said_as": "So if I lived...", "question": True,
                         "note": "KaptainDoosh lives in Milton."},
        "baro with me and arnav": {"said_as": "statement", "note": "KaptainDoosh owes arnav $12.50."},
        "We running it this weekend": {"said_as": "statement", "note": "KaptainDoosh told his mom about the situation."},
        "pulling up now": {"said_as": "statement", "note": "KaptainDoosh is free on Monday.", "short_lived": False},
    }

    def fake(system, user, max_tokens=300):
        said = _said(user)[1]
        return next((a for k, a in answers.items() if k in said), {"note": None})

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    assert memories.extract(project, FakeJob())["created"] == 1
    with project.lock:
        (m,) = [x for x in memories.listing(project.conn, "100", "new") if x["category"] != "style"]
        ts = project.conn.execute("SELECT ts FROM messages WHERE id = '1013'").fetchone()[0]
    # "on Monday" only means something next to the day it was said.
    day = memories._when(ts).rsplit(",", 1)[0]
    assert m["text"] == f"KaptainDoosh is free on Monday (said {day})." and m["short_lived"]


def test_prompt_says_when(project):
    with project.lock:
        b = project.conn.execute(
            "SELECT b.*, a.name, a.display_name FROM blocks b JOIN authors a ON a.id = b.author_id "
            "WHERE b.id = (SELECT block_id FROM messages WHERE id = '1007')").fetchone()
        msgs = project.all("SELECT id, content_text, reply_to_id FROM messages WHERE block_id = ?", (b["id"],))
    assert f"What KaptainDoosh said on {memories._when(b['first_ts'])}:" in memories._prompt(project, b, msgs)
    assert "2026" in memories._when(b["first_ts"])


def test_confidence_reads_wording_and_repetition():
    one = memories.confidence("KaptainDoosh works at a bakery in town.", "important", "fact", said=1, similar=0, unlinked=False)
    assert one >= memories.SURE  # who someone is, said plainly once
    assert memories.confidence("KaptainDoosh plays Barotrauma with the group.", "interest", "fact", 1, 0, False) < memories.SURE
    assert memories.confidence("KaptainDoosh plays Barotrauma with the group.", "interest", "fact", 3, 0, False) >= memories.SURE
    assert memories.confidence("KaptainDoosh might work at a bakery in town.", "important", "fact", 1, 0, False) < memories.SURE
    assert memories.confidence("KaptainDoosh is heading home tonight soon.", "short_lived", "fact", 4, 2, False) < memories.SURE
    # "works" right after the name says who they are; "work" elsewhere doesn't.
    assert memories.confidence("KaptainDoosh noticed the mic doesn't work in the clip.", "important", "fact", 1, 0, False) < memories.SURE
    assert memories.confidence("KaptainDoosh is playing CS with SerfShoop tonight.", "interest", "fact", 2, 1, False) < memories.SURE
    assert memories.confidence("KaptainDoosh usually types in lowercase.", "other", "style", 0, 0, False) < memories.SURE


def test_said_counts_blocks_not_messages(project):
    with project.tx() as conn:
        once = memories.add(conn, "100", "fact", "KaptainDoosh plays Barotrauma with the group.")
        # Three messages, all from the same block: said once.
        conn.execute("INSERT INTO memory_sources SELECT ?, id FROM messages WHERE id IN ('1006', '1007', '1008')", (once,))
        often = memories.add(conn, "100", "fact", "KaptainDoosh plays Killing Floor with the group.")
        # Three separate blocks: said three times.
        conn.execute("INSERT INTO memory_sources SELECT ?, id FROM messages WHERE id IN ('1000', '1007', '1010')", (often,))
    with project.lock:
        by_id = {m["id"]: m for m in memories.listing(project.conn, "100", "new")}
    assert not by_id[once]["sure"] and by_id[often]["sure"]
    assert by_id[often]["confidence"] > by_id[once]["confidence"]

    with project.tx() as conn:
        assert memories.keep_all(conn, "100", sure_only=True) == 1
    with project.lock:
        assert [m["id"] for m in memories.listing(project.conn, "100", "kept")] == [often]
        assert [m["id"] for m in memories.listing(project.conn, "100", "new")] == [once]
