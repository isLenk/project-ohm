import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/tts

from spoken import spoken  # noqa: E402


def test_slang_is_said_as_words():
    assert spoken("ig we could") == "I guess we could"
    assert spoken("idk tbh, u down rn?") == "I don't know to be honest, you down right now?"
    assert spoken("NGL that was mid") == "not gonna lie that was mid"
    assert spoken("mb, brb") == "my bad, be right back"


def test_laughs_go_and_a_laugh_alone_is_haha():
    assert spoken("lmao") == "haha"
    assert spoken("LMAOOOO 💀💀") == "haha"
    assert spoken("bro lmao you're cooked") == "bro you're cooked"
    assert spoken("that's crazy lol.") == "that's crazy."
    assert spoken("lmfaoooo what") == "what"


def test_emoji_and_emotes_are_not_read():
    assert spoken("nice 🔥🔥 <:pepe:123456789012345678>") == "nice"
    assert spoken("ok :) see you") == "ok see you"


def test_ordinary_words_are_left_alone():
    text = "burn the big lollipop, you know? it's a lot of work"
    assert spoken(text) == text
    assert spoken("") == "" and spoken("   ") == ""
