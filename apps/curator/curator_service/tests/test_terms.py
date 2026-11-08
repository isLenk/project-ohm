import re

import pytest

from curator_service.app import llm, memories, terms
from curator_service.app.errors import ApiError
from curator_service.tests.seed import MESSAGES


@pytest.fixture
def vocab(monkeypatch):
    """Every seed word is a normal word except "baro"."""
    words = {w for m in MESSAGES for w in re.findall(r"[a-z]+", m[3].lower())} - {"baro"}
    monkeypatch.setattr(terms, "_vocab", words)
    monkeypatch.setattr(terms, "MIN_USES", 2)
    monkeypatch.setattr(llm, "pieces", lambda ws: None)


def test_scan_finds_unfamiliar_words(project, vocab):
    with project.tx() as conn:
        out = terms.scan(conn)
    assert out["added"] == 1
    with project.lock:
        (baro,) = terms.listing(project.conn)
    assert baro["term"] == "baro" and baro["status"] == "unknown"
    assert baro["uses"] == 3 and baro["people"] == 2
    assert baro["examples"][0]["id"] == "1010"  # newest first


def test_words_the_model_keeps_whole_are_normal(project, vocab, monkeypatch):
    monkeypatch.setattr(llm, "pieces", lambda ws: [[" " + w] for w in ws])
    with project.tx() as conn:
        assert terms.scan(conn)["found"] == 0


def test_define_link_and_rescan(project, vocab):
    with project.tx() as conn:
        terms.scan(conn)
        tid = conn.execute("SELECT id FROM terms WHERE term = 'baro'").fetchone()[0]
        linked = terms.update(conn, tid, relation="same", related_term="Barotrauma")
        assert linked["status"] == "linked" and linked["related_term"] == "barotrauma"
        defined = terms.save(conn, "barotrauma", definition="A co-op submarine game.")
        assert defined["origin"] == "manual" and defined["examples"]  # found through search
        with pytest.raises(ApiError):
            terms.update(conn, tid, related_term="baro")
    with project.lock:
        baro = next(t for t in terms.listing(project.conn) if t["term"] == "baro")
    assert baro["related_definition"] == "A co-op submarine game."

    # A rescan that no longer finds "baro" keeps it, because the user linked it.
    with project.tx() as conn:
        conn.execute("UPDATE terms SET examples = '[]'")
    terms._vocab.add("baro")
    with project.tx() as conn:
        assert terms.scan(conn)["removed"] == 0
        assert conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0] == 2


def test_untouched_terms_go_when_they_stop_qualifying(project, vocab):
    with project.tx() as conn:
        terms.scan(conn)
    terms._vocab.add("baro")
    with project.tx() as conn:
        assert terms.scan(conn)["removed"] == 1


def test_ctrl_click_on_a_known_word_adds_it(project, vocab):
    with project.tx() as conn:
        t = terms.save(conn, "  Pulling Up ", definition="Arriving.")
    assert t["term"] == "pulling up" and t["status"] == "defined" and t["examples"][0]["id"] == "1013"


def test_glossary_goes_into_the_extraction_prompt(project, vocab):
    with project.tx() as conn:
        terms.save(conn, "baro", relation="same", related_term="barotrauma")
        terms.save(conn, "barotrauma", definition="A co-op submarine game.")
        terms.save(conn, "coons", definition="Never shown: ignored.")
        conn.execute("UPDATE terms SET ignored = 1 WHERE term = 'coons'")
    with project.lock:
        b = project.conn.execute(
            "SELECT b.*, a.name, a.display_name FROM blocks b JOIN authors a ON a.id = b.author_id "
            "WHERE b.id = (SELECT block_id FROM messages WHERE id = '1007')"
        ).fetchone()
        msgs = project.all("SELECT id, content_text, reply_to_id FROM messages WHERE block_id = ?", (b["id"],))
    prompt = memories._prompt(project, b, msgs)
    assert prompt.startswith("Words this group uses:")
    assert '"baro" means the same as "barotrauma": A co-op submarine game.' in prompt
    assert "coons" not in prompt.split("Context")[0]


def test_migration_adds_terms(tmp_path):
    from curator_service.app import schema
    from curator_service.app.project import Project

    p = Project.create(tmp_path / "old.ohm", "Old")
    with p.lock:
        p.conn.execute("DROP TABLE terms")
        p.conn.execute("PRAGMA user_version = 5")
        p.conn.executescript(schema.PROJECT_MIGRATIONS[4])
        assert p.conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0] == 0
    p.close()


def test_other_projects_terms_reach_the_prompt(project, vocab):
    with project.tx() as conn:
        terms.save(conn, "baro", definition="Barotrauma, from this project.")
    shared = [
        {"term": "baro", "definition": "Something else, from another project.", "relation": None, "related_term": None},
        {"term": "coons", "definition": "What the group calls each other.", "relation": None, "related_term": None},
    ]
    with project.lock:
        lines = terms.glossary_for(project.conn, "depends on other coons, baro tonight", extra=shared)
    assert '"baro": Barotrauma, from this project.' in lines  # this project's own definition wins
    assert '"coons": What the group calls each other.' in lines
    assert len(lines) == 2


def test_share_sends_defined_terms(project, vocab, monkeypatch):
    sent = {}

    class Ok:
        def raise_for_status(self):
            pass

    def put(url, json, headers, timeout):
        sent["url"], sent["terms"] = url, json["terms"]
        return Ok()

    monkeypatch.setattr(terms, "MEMORY_API_URL", "http://nas:8100")
    monkeypatch.setattr(terms.requests, "put", put)
    with project.tx() as conn:
        terms.save(conn, "baro", relation="same", related_term="barotrauma")
        terms.save(conn, "zhing", definition="")  # nothing said about it: not sent
    out = terms.share(project)
    assert out["ok"] and out["terms"] == 1
    assert sent["url"] == f"http://nas:8100/glossary/{terms.source_id(project)}"
    assert sent["terms"] == [{"term": "baro", "definition": None, "relation": "same", "related_term": "barotrauma"}]
    assert terms.shared_status(project)["ok"]


def test_share_without_memory_service(project):
    assert terms.share(project)["ok"] is False
    assert terms.from_other_projects(project) == []


class FakeJob:
    cancelled = False

    def progress(self, *a, **k):
        pass


def test_suggest_then_approve(project, vocab, monkeypatch):
    answers = {
        "baro": {"meaning": "the game Barotrauma", "kind": "short", "definition": None, "same_as": "Barotrauma"},
        "zhing": {"meaning": "great", "kind": "slang", "definition": "Great, excellent.", "same_as": None},
        "coons": {"meaning": "the friends in the chat", "kind": "normal", "definition": None, "same_as": None},
        "ttc": {"meaning": "?", "kind": "unsure", "definition": None, "same_as": None},
    }
    asked = []

    def chat_json(system, prompt, max_tokens):
        word = prompt.split('Word: "')[1].split('"')[0]
        asked.append(word)
        return answers[word]

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", chat_json)
    with project.tx() as conn:
        for w in answers:
            terms.save(conn, w)  # unknown: no definition yet
        terms.save(conn, "mb", definition="My bad.")  # already defined: not asked
    out = terms.suggest(project, FakeJob())
    assert sorted(asked) == sorted(answers) and out["suggested"] == 4 and out["kinds"]["unsure"] == 1

    with project.lock:
        by = {t["term"]: t for t in terms.listing(project.conn)}
    assert by["baro"]["suggestion"] == {"kind": "same", "related_term": "barotrauma", "meaning": "the game Barotrauma"}
    assert by["zhing"]["status"] == "unknown"  # nothing changes until it's approved
    assert by["ttc"]["suggestion"] == {"kind": "unsure"}

    asked.clear()
    terms.suggest(project, FakeJob())
    assert asked == []  # every unknown term has an answer now

    with project.tx() as conn:
        assert terms.accept(conn, by["zhing"]["id"])["definition"] == "Great, excellent."
        with pytest.raises(ApiError):
            terms.accept(conn, by["ttc"]["id"])  # unsure: nothing to approve
        assert terms.accept_all(conn) == 2  # baro and coons; ttc is left alone
    with project.lock:
        by = {t["term"]: t for t in terms.listing(project.conn)}
    assert by["baro"]["status"] == "linked" and by["baro"]["related_term"] == "barotrauma"
    assert by["coons"]["status"] == "ignored"
    assert by["ttc"]["status"] == "unknown"


def test_dismissed_suggestions_are_not_asked_again(project, vocab, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"kind": "slang", "definition": "Wrong guess."})
    with project.tx() as conn:
        tid = terms.save(conn, "zhing")["id"]
    terms.suggest(project, FakeJob())
    with project.tx() as conn:
        assert terms.dismiss(conn, tid)["suggestion"]["kind"] == "unsure"
        assert terms.accept_all(conn) == 0
    assert terms.suggest(project, FakeJob())["terms"] == 0
    assert terms.suggest(project, FakeJob(), ids=[tid])["suggested"] == 1  # unless asked about it directly


def test_reading_suggestions():
    read = terms._read_suggestion
    assert read("x", None) == {"kind": "unsure"}
    assert read("alc", {"kind": "short", "same_as": "Alcohol"})["related_term"] == "alcohol"
    assert read("alc", {"kind": "typo", "same_as": "alc"}) == {"kind": "unsure"}  # linked to itself
    assert read("hpa", {"kind": "thing", "definition": "null"}) == {"kind": "unsure"}
    long = read("x", {"kind": "slang", "definition": "word " * 60})
    assert len(long["definition"]) <= terms.MAX_DEFINITION + 1 and long["definition"].endswith("…")


def test_migration_adds_suggestions(tmp_path):
    from curator_service.app.project import Project

    p = Project.create(tmp_path / "six.ohm", "Six")
    with p.lock:
        p.conn.execute("ALTER TABLE terms DROP COLUMN suggestion")
        p.conn.execute("PRAGMA user_version = 6")
    p.save()
    p.close()
    p = Project.open(tmp_path / "six.ohm")
    with p.lock:
        assert "suggestion" in {r[1] for r in p.conn.execute("PRAGMA table_info(terms)")}
    p.close()
