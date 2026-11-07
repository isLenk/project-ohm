import json
import time

import pytest
from fastapi.testclient import TestClient

from curator_service.app import importer, main
from curator_service.app.errors import ApiError
from curator_service.app.project import Project, state

GUILD = {"id": "g1", "name": "Test server", "iconUrl": None}
GENERAL = {"id": "500", "type": "GuildTextChat", "categoryId": None, "category": "Text", "name": "general", "topic": None}
LENK = {"id": "1", "name": "_lenk", "discriminator": "0000", "nickname": "Lenk", "color": None, "isBot": False,
        "roles": [{"id": "r1", "name": "A5", "color": None, "position": 1}],
        "avatarUrl": "https://cdn.discordapp.com/avatars/1/aaa.png?size=512"}
ZHING = {"id": "2", "name": "au16227", "discriminator": "0000", "nickname": "Zhingstin Ji", "color": None, "isBot": False,
         "roles": [], "avatarUrl": "https://cdn.discordapp.com/avatars/2/bbb.png?size=512"}
BOT = {"id": "9", "name": "Mantaro", "discriminator": "4454", "nickname": "Maiden", "color": None, "isBot": True,
       "roles": [], "avatarUrl": None}


def msg(mid, author, minute, content, mtype="Default", reply=None, mentions=(), attachments=()):
    m = {"id": str(mid), "type": mtype, "timestamp": f"2025-02-15T21:{minute:02d}:00.000+00:00", "timestampEdited": None,
         "callEndedTimestamp": None, "isPinned": False, "content": content, "author": author,
         "attachments": list(attachments), "embeds": [], "stickers": [], "reactions": [], "mentions": list(mentions),
         "inlineEmojis": []}
    if reply:
        m["reference"] = {"type": "Default", "messageId": str(reply), "channelId": "500", "guildId": "g1"}
    return m


def dce(channel, messages):
    return {"guild": GUILD, "channel": channel, "dateRange": {"after": None, "before": None},
            "exportedAt": "2026-11-06T00:00:00Z", "messages": messages, "messageCount": len(messages)}


GENERAL_MSGS = [
    msg(100, LENK, 0, "@Zhingstin Ji you coming <@2>", mentions=[ZHING]),
    msg(101, LENK, 1, "<#500> is dead lol"),                       # same author, joins block
    msg(102, ZHING, 2, "yeah", "Reply", reply=100),                # native reply -> block link
    msg(103, ZHING, 3, "on my way", "Reply", reply=100),           # same target, joins block
    msg(104, ZHING, 4, "wait what", "Reply", reply=101),           # different target -> new block
    msg(105, BOT, 5, "Added to queue", "20"),                       # bot
    msg(106, LENK, 6, "Started a thread.", "ThreadCreated"),       # system; also the thread's id
    msg(107, LENK, 7, ""),                                          # empty
    msg(108, LENK, 8, "", "Reply", reply=100,                      # image only: dropped
        attachments=[{"id": "a", "url": "https://x/y.png", "fileName": "y.png", "fileSizeBytes": 5}]),
    msg(110, ZHING, 9, "lmao", "Reply", reply=108),                # reply to the image -> 100
    msg(109, ZHING, 59, "much later"),                              # 51 min gap -> new conversation
]


def jsonl_line(mid, channel_id, channel_name, author_id, author, display, minute, text, reply=None, flags=()):
    return json.dumps({"id": str(mid), "channel_id": channel_id, "channel_name": channel_name, "guild_id": "g1",
                       "ts": f"2025-02-15T22:{minute:02d}:00.000Z", "author_id": author_id, "author": author,
                       "author_display": display, "text": text, "raw_content": text, "reply_to": reply, "mentions": [],
                       "reactions": {"🔥": 2}, "attachments": [], "edited": False, "flags": list(flags)})


@pytest.fixture
def empty(tmp_path):
    p = Project.create(tmp_path / "imp.ohm", "Import")
    yield p
    p.close()


def write(path, doc):
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_dce_import(empty, tmp_path):
    f = write(tmp_path / "general.json", dce(GENERAL, GENERAL_MSGS))
    s = importer.run(empty, [str(f)])
    assert s["messages"] == 9 and s["new_authors"] == 3 and s["channels"] == 1
    assert s["empty"] == 2  # 107 and 108 have no text, so they aren't kept
    assert s["excluded"] == {"bot": 1, "system": 1}
    assert s["links"]["discord"] == 3  # 102 -> 100, 104 -> 101, 110 -> 100 (past the image it replied to)
    assert s["conversations"] == 2

    rows = {r["id"]: r for r in empty.all("SELECT * FROM messages")}
    assert rows["100"]["content_text"] == "@Zhingstin Ji you coming @Zhingstin Ji"
    assert rows["101"]["content_text"] == "#general is dead lol"
    assert rows["101"]["block_id"] == rows["100"]["block_id"]
    assert rows["103"]["block_id"] == rows["102"]["block_id"]
    assert rows["104"]["block_id"] != rows["102"]["block_id"]
    assert "107" not in rows and "108" not in rows
    assert rows["110"]["reply_to_id"] == "100"

    link = empty.one("SELECT * FROM links WHERE unit_kind = 'block' AND unit_id = ?", (str(rows["102"]["block_id"]),))
    assert link["parent_id"] == "100" and link["origin"] == "discord" and link["status"] == "accepted"

    authors = {a["id"]: a for a in empty.all("SELECT * FROM authors")}
    assert authors["1"]["avatar_url"].startswith("https://cdn.discordapp.com/avatars/1/")
    assert authors["1"]["display_name"] == "Lenk"
    assert len({a["color_index"] for a in authors.values()}) == 3

    with pytest.raises(ApiError) as e:
        importer.run(empty, [str(f)])
    assert e.value.code == "already_imported"


def test_folder_with_jsonl_thread_and_speakers(empty, tmp_path):
    folder = tmp_path / "Ohm Training"
    folder.mkdir()
    write(folder / "general.json", dce(GENERAL, GENERAL_MSGS))
    lines = [
        # duplicates of the real export: skipped
        jsonl_line(100, "500", "general", "1", "_lenk", "Lenk", 0, "dup"),
        jsonl_line(101, "500", "general", "1", "_lenk", "Lenk", 1, "dup"),
        # the thread started by message 106 (a thread's id is its starter message's id)
        jsonl_line(200, "106", "Teusday", "2", "au16227", "Zhingstin Ji", 0, "Board rental $83"),
        jsonl_line(201, "106", "Teusday", "1", "_lenk", "Lenk", 1, "bet", reply=200),
        jsonl_line(202, "106", "Teusday", "3", "inflation", "inflation", 2, "who's driving"),
    ]
    (folder / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write(folder / "speakers.json", {"schema_version": 1, "speakers": {
        "3": {"name": "inflation", "alias": "Arnav", "variants": ["inflation"], "is_bot": False, "message_count": 1}}})
    write(folder / "conversations.json", {"schema_version": 1, "conversations": []})

    s = importer.run(empty, [str(folder)])
    files = {f["file"]: f for f in s["files"]}
    assert files["conversations.json"]["status"] == "skipped"
    assert files["messages.jsonl"]["status"] == "imported"
    assert s["messages"] == 12 and s["duplicates"] == 2 and s["channels"] == 2

    thread = empty.one("SELECT * FROM sources WHERE channel_id = '106'")
    assert thread["channel_name"] == "Teusday" and thread["kind"] == "jsonl"
    authors = {a["id"]: a for a in empty.all("SELECT * FROM authors")}
    assert authors["3"]["display_name"] == "Arnav"           # alias from speakers.json
    assert authors["2"]["avatar_url"]                        # avatar borrowed from the real export
    assert empty.scalar("SELECT COUNT(*) FROM links WHERE origin = 'discord'") == 4  # 102, 104 and 110 in the export; 201 in the thread
    assert empty.scalar("SELECT content_text FROM messages WHERE id = '100'").startswith("@Zhingstin")  # export won


def test_overlapping_export_extends(empty, tmp_path):
    importer.run(empty, [str(write(tmp_path / "a.json", dce(GENERAL, GENERAL_MSGS[:3])))])
    blocks_before = empty.scalar("SELECT COUNT(*) FROM blocks")
    s = importer.run(empty, [str(write(tmp_path / "b.json", dce(GENERAL, GENERAL_MSGS[1:5])))])
    assert s["messages"] == 2 and s["duplicates"] == 2 and s["new_authors"] == 0
    rows = {r["id"]: r for r in empty.all("SELECT * FROM messages")}
    assert rows["103"]["block_id"] == rows["102"]["block_id"]  # joined the block from the first import
    assert empty.scalar("SELECT COUNT(*) FROM blocks") == blocks_before + 1


def test_api_import_job(tmp_path):
    path = tmp_path / "api.ohm"
    Project.create(path, "API").close()
    f = write(tmp_path / "general.json", dce(GENERAL, GENERAL_MSGS))
    c = TestClient(main.app)
    assert c.post("/project/open", json={"path": str(path)}).status_code == 200
    try:
        assert c.post("/imports", json={"paths": [str(tmp_path / "missing.json")]}).status_code == 404
        job = c.post("/imports", json={"paths": [str(f)]}).json()
        for _ in range(100):
            job = c.get(f"/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        assert job["result"]["messages"] == 9
        assert c.get("/imports").json()["sources"][0]["imported_messages"] == 9
        people = c.get("/people").json()
        people = people.get("people", people)
        assert any(p["avatar_url"] for p in people)
    finally:
        c.post("/project/close")
        state.project = None
