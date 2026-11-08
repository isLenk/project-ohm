"""Finding the people a message is about by any name they go by. Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
from controller.memory import find_people, format_people, person_names

LENK = {"user_id": "1", "name": "Lenk", "display_name": "Lenk", "preferred_name": None,
        "username": "lenk_07", "aliases": ["Kyle", "Kyle Ramos"]}
MAY = {"user_id": "2", "name": "May", "display_name": "May", "preferred_name": None, "username": "mayday", "aliases": []}
AL = {"user_id": "3", "name": "Al", "display_name": "Al", "preferred_name": None, "username": None, "aliases": []}
UNNAMED = {"user_id": "4", "name": "4", "display_name": None, "preferred_name": None, "username": None, "aliases": []}
USERS = [LENK, MAY, AL, UNNAMED]


def ids(text, users=USERS, mentioned=None):
    return [p["user_id"] for p in find_people(text, users, mentioned)]


def test_every_name_finds_the_same_person():
    for text in ["what does lenk play", "is Kyle coming", "ask lenk_07", "KYLE RAMOS said so"]:
        assert ids(text) == ["1"], text


def test_names_match_whole_words_only():
    assert ids("lenkster is here") == []
    assert ids("skyler is here") == []
    assert ids("lenk's cat") == ["1"]
    assert ids("@Lenk you up") == ["1"]


def test_people_come_in_the_order_they_are_named():
    assert ids("did may beat kyle") == ["2", "1"]


def test_short_discord_names_and_bare_ids_are_not_matched():
    assert ids("al is here") == []
    assert ids("4 people came") == []
    # ...but a short name someone typed in as an alias is
    assert ids("ty is here", [{"user_id": "5", "name": "Tyler", "aliases": ["Ty"]}]) == ["5"]


def test_mentions_come_first_even_when_not_in_the_list_yet():
    assert ids("you and kyle", mentioned={"9": "Newbie"}) == ["9", "1"]
    assert find_people("hey", [], {"9": "Newbie"}) == [{"user_id": "9", "name": "Newbie", "also": []}]


def test_names_lead_with_what_he_calls_them_without_repeats():
    assert person_names(LENK) == ["Lenk", "lenk_07", "Kyle", "Kyle Ramos"]
    assert person_names({"user_id": "6", "name": "Kyle", "preferred_name": "Kyle", "display_name": "lenk",
                         "aliases": ["kyle"]}) == ["Kyle", "lenk"]


def test_prompt_block_names_everyone_and_says_when_nothing_is_known():
    block = format_people([
        {"name": "Lenk", "also": ["Kyle"], "notes": ["Kyle plays Hell Let Loose."]},
        {"name": "May", "also": [], "notes": []},
    ])
    assert block == ("Lenk (also called Kyle):\n- Kyle plays Hell Let Loose.\n\n"
                     "May:\n- Nothing remembered about them yet.")


def test_how_he_gets_on_with_someone_is_one_line():
    block = format_people([
        {"name": "Lenk", "also": [], "notes": ["Lenk plays Hell Let Loose."], "affinity": 0.6,
         "relationship_notes": "calls him grandpa,\n still owes him 5 bucks"},
        {"name": "May", "also": [], "notes": [], "affinity": -0.5, "relationship_notes": None},
        {"name": "Sam", "also": [], "notes": [], "affinity": 0.1, "relationship_notes": ""},
        {"name": "Jo", "also": [], "notes": [], "affinity": 0, "relationship_notes": "beat him at chess once"},
    ])
    assert block == ("Lenk:\n- Lenk plays Hell Let Loose.\n"
                     "- You get on well with them. Running jokes and grudges: calls him grandpa, still owes him 5 bucks\n\n"
                     "May:\n- You're not their biggest fan.\n\n"
                     "Sam:\n- Nothing remembered about them yet.\n\n"
                     "Jo:\n- Running jokes and grudges: beat him at chess once")


def test_relationships_fail_soft_and_are_cached(monkeypatch):
    from controller.memory import MemoryClient
    client = MemoryClient(url="http://nas.invalid")
    calls = []

    def request(method, path, **kwargs):
        calls.append(path)
        return None if len(calls) == 1 else [{"user_id": 1, "name": "Lenk", "affinity": 0.7, "notes": "x"}]

    monkeypatch.setattr(client, "_request", request)
    assert client.relationships("ohm") == {}
    assert client.relationships("ohm") == {}  # a failed fetch waits a full TTL before trying again
    assert calls == ["/relationships"]
    client._relationships.clear()
    assert client.relationships("ohm") == {"1": {"affinity": 0.7, "notes": "x"}}
