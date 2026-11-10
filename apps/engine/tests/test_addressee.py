from controller.addressee import addressee, aside, last_speaker

PEOPLE = {"Zhing": ["Zhingstin Ji", "zhing"], "Lenk": ["lenk", "Lance"], "KaptainDoosh": ["kaptain"]}


def history(*lines):
    return [{"role": "user", "content": "\n".join(lines)}]


def test_you_right_after_someone_else_is_for_them():
    h = history("Zhing: work was so bad today", "my manager made me redo the whole inventory count")
    note = aside("Lenk: did u at least get paid for it", h, "Ohm", PEOPLE)
    assert note.startswith("Lenk is probably talking to Zhing, not you")
    assert "Don't answer for Zhing" in note


def test_a_quick_reaction_in_between_doesnt_steal_the_you():
    h = history("Zhing: work was so bad today", "my manager made me redo the whole inventory count")
    note = aside("KaptainDoosh: thats brutal\nLenk: did u at least get paid for it", h, "Ohm", PEOPLE)
    assert note.startswith("Lenk is probably talking to Zhing")
    h = history("Zhing: this guy just walks up and asks if i wanna join his pyramid scheme")
    assert aside("KaptainDoosh: LMAO\nLenk: what did u say", h, "Ohm", PEOPLE).startswith("Lenk is probably talking to Zhing")


def test_starting_with_a_name_or_an_at_mention_is_for_them():
    assert addressee("zhing what did u say", "Lenk", "Ohm", PEOPLE, None) == ("Zhing", True)
    assert addressee("lmao @kaptain look at this", "Lenk", "Ohm", PEOPLE, None) == ("KaptainDoosh", True)


def test_talking_about_someone_isnt_talking_to_them():
    assert addressee("lenk is cooked", "Zhing", "Ohm", PEOPLE, None) is None


def test_anything_naming_ohm_is_his():
    h = history("Zhing: i got a shift saturday")
    assert aside("Lenk: ohm you doing anything", h, "Ohm", PEOPLE) == ""
    assert addressee("zhing and ohm u guys down", "Lenk", "Ohm", PEOPLE, "Zhing") is None


def test_after_ohm_spoke_you_means_ohm():
    h = history("Lenk: whats up") + [{"role": "assistant", "content": "nm, you?"}]
    assert last_speaker(h, "Ohm") == "Ohm"
    assert aside("Lenk: what are you doing tonight", h, "Ohm", PEOPLE) == ""


def test_lines_in_the_same_turn_count_and_unlabeled_lines_carry_on():
    note = aside("Zhing: nope lmao\nLenk: why do u even work there\nlike fr", [], "Ohm", PEOPLE)
    assert note.startswith("Lenk is probably talking to Zhing")
    assert aside("Lenk: yo whats everyone doing this weekend", [], "Ohm", PEOPLE) == ""


def test_the_engines_model_wrapper_passes_the_chat_to_pre_checks():
    from controller.model import ModelObject
    seen = {}
    wrapper = ModelObject.__new__(ModelObject)
    wrapper.model = type("M", (), {"pre_checks": lambda self, *a, **k: seen.update(args=a, kwargs=k) or {"to": None}})()
    wrapper.pre_checks("did u get paid", "Zhing: work sucked", "Lenk", ["Zhing"])
    assert seen["args"] == ("did u get paid", "Zhing: work sucked", "Lenk", ["Zhing"])
