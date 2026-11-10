import random

from brain import Task, config, privacy, run
from brain.privacy import Mask

from test_brain import Fake, fakes, people  # noqa: F401  (fixture)

EVERYONE = [["420yolocoon", "Jake", "jakey"], ["Lenk", "lenk_", "Lance Talban"]]


def test_hides_every_name_a_person_goes_by_as_one_stand_in():
    m = Mask(EVERYONE, random.Random(1))
    out = m.hide("jake: yo @420yolocoon and Jakey, lance talban said LENK_ is afk")
    for real in ("jake", "420yolocoon", "lance", "talban", "lenk"):
        assert real not in out.lower()
    jake, lenk = m.stand_in["jake"], m.stand_in["lenk"]
    assert out == f"{jake}: yo @{jake} and {jake}, {lenk} said {lenk} is afk"
    assert m.reveal(f"{jake} roasted {lenk}") == "420yolocoon roasted Lenk"


def test_whole_words_only():
    m = Mask([["Al"]], random.Random(1))
    assert m.hide("also Alright, Al!") == f"also Alright, {m.stand_in['al']}!"


def test_stand_ins_differ_between_masks():
    seen = {Mask(EVERYONE).stand_in["jake"] for _ in range(20)}
    assert len(seen) > 1


def test_stand_ins_never_collide_with_a_real_name():
    m = Mask([["Marcus"], ["Theo"]] + [[n] for n in privacy.POOL[2:]], random.Random(3))
    assert {m.stand_in["marcus"].lower(), m.stand_in["theo"].lower()}.isdisjoint({"marcus", "theo"})


def test_contact_details_become_placeholders_and_come_back():
    m = Mask(EVERYONE)
    text = ("mail jake@gmail.com or call +1 (555) 123-4567, my id 555736699364179980, "
            "ip 192.168.1.20, https://instagram.com/jakey?x=1 and discord.gg/abc123. score was 10-12 on 2026-11-08")
    out = m.hide(text)
    for secret in ("jake@gmail.com", "555) 123-4567", "555736699364179980", "192.168.1.20", "instagram", "abc123"):
        assert secret not in out
    assert "[email-1]" in out and "[phone-1]" in out and "[id-1]" in out and "[ip-1]" in out and "[link-1]" in out
    assert "10-12" in out and "2026-11-08" in out  # not phone numbers
    back = m.reveal(out)
    assert "jake@gmail.com" in back and "555736699364179980" in back and "https://instagram.com/jakey?x=1" in back


def test_discord_tags_are_hidden():
    m = Mask(EVERYONE)
    out = m.hide("add me A7916817#1531 or jake.v2#0420, room #1 at 10#")
    assert "A7916817#1531" not in out and "jake.v2#0420" not in out and "[tag-1]" in out and "[tag-2]" in out
    assert "room #1" in out  # not a tag
    assert "A7916817#1531" in m.reveal(out)


def test_reveal_value_walks_json():
    m = Mask(EVERYONE)
    j = m.stand_in["jake"]
    assert m.reveal_value({"notes": [f"{j} likes Elden Ring"], "ok": True}) == {"notes": ["420yolocoon likes Elden Ring"],
                                                                              "ok": True}


def test_cloud_backend_only_sees_masked_text(fakes, monkeypatch):
    people(monkeypatch, EVERYONE)
    monkeypatch.setenv("BRAIN_ROLE_BRIEF", "codex")
    seen = []

    def answer(task):
        seen.append(task)
        stand_in = next(w for w in task.user.split() if w.rstrip(":") in privacy.POOL).rstrip(":")
        return f'{{"summary": "{stand_in} is AFK"}}'

    fakes["codex"] = Fake("codex", [answer])
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    got = run(Task("brief", "Sum up what Jake did.", "Jake: brb, text me 555-123-4567 89", schema=schema))
    assert got == {"summary": "420yolocoon is AFK"}  # real name back
    sent = seen[0].system + seen[0].user
    assert "Jake" not in sent and "555-123-4567" not in sent


def test_local_backend_gets_the_real_text(fakes, monkeypatch):
    monkeypatch.setenv("BRAIN_ROLE_BRIEF", "local")
    fakes["local"] = Fake("local", [lambda t: t.user])
    assert run(Task("brief", "s", "Lenk: hi")) == "Lenk: hi"


def test_no_name_list_means_no_cloud(fakes, monkeypatch):
    people(monkeypatch, [])
    monkeypatch.setenv("BRAIN_ROLE_JUDGE", "codex,qwen")
    fakes["codex"] = Fake("codex", ["cloud"])
    fakes["qwen"] = Fake("qwen", ["local"])
    assert run(Task("judge", "s", "Jake: hi")) == "local"
    assert not fakes["codex"].calls
    # own people, or nothing private, can go out
    assert run(Task("judge", "s", "Jake: hi", people=[["Jake"]])) == "cloud"
    assert run(Task("judge", "s", "what's 2+2", private=False)) == "cloud"


def test_needs_gpu_swap_sees_the_cloud_is_closed(fakes, monkeypatch):
    from brain import needs_gpu_swap
    people(monkeypatch, [])
    monkeypatch.setenv("BRAIN_ROLE_JUDGE", "codex,qwen")
    fakes["codex"] = Fake("codex", ["x"])
    fakes["qwen"] = Fake("qwen", ["x"], gpu=True)
    assert needs_gpu_swap("judge")


def test_a_failing_source_is_skipped(monkeypatch):
    def broken():
        raise ConnectionError("NAS down")
    monkeypatch.setattr(privacy, "_sources", [broken, lambda: [["Lenk"]]])
    monkeypatch.setattr(privacy, "_cache", (0.0, []))
    assert privacy.people() == [["Lenk"]]
    assert config.is_cloud("codex")
