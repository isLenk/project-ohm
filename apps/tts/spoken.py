"""Chat text as it's said out loud: slang as words, laughs and emoji dropped.
Only the audio changes; the written reply keeps its words."""

import re
import unicodedata

# Whole words, any case
WORDS = {
    "ig": "I guess", "idk": "I don't know", "idc": "I don't care", "ik": "I know", "ikr": "I know right",
    "ngl": "not gonna lie", "tbh": "to be honest", "tbf": "to be fair", "imo": "in my opinion",
    "imho": "in my opinion", "afaik": "as far as I know", "istg": "I swear to god", "ong": "on god",
    "fr": "for real", "frfr": "for real for real", "rn": "right now", "nvm": "never mind", "omg": "oh my god",
    "wtf": "what the fuck", "wth": "what the hell", "stfu": "shut the fuck up", "af": "as fuck", "asf": "as fuck",
    "smh": "", "jk": "just kidding", "brb": "be right back", "gtg": "got to go", "g2g": "got to go",
    "omw": "on my way", "lmk": "let me know", "btw": "by the way", "irl": "in real life", "ofc": "of course",
    "wym": "what do you mean", "wdym": "what do you mean", "hbu": "how about you", "wbu": "what about you",
    "wyd": "what you doing", "nm": "not much", "mb": "my bad", "np": "no problem", "ty": "thank you",
    "thx": "thanks", "yw": "you're welcome", "pls": "please", "plz": "please", "ppl": "people", "bc": "because",
    "abt": "about", "rly": "really", "srsly": "seriously", "prob": "probably", "probs": "probably",
    "tho": "though", "tmr": "tomorrow", "tmrw": "tomorrow", "b4": "before", "w/": "with", "w/o": "without",
    "u": "you", "ur": "your", "r": "are", "k": "okay", "kk": "okay", "ye": "yeah", "yea": "yeah",
    "ight": "aight", "gg": "G G", "gn": "good night", "gm": "good morning", "pfp": "profile pic",
}
# lol, lmaooo, xd, kek...
LAUGH = re.compile(r"(?<![\w'])(?:lo+l+|lma+o+|lmfa+o+|lmf?a+o+|rofl|kek|xd+|lul)(?![\w'])", re.IGNORECASE)
EMOTE = re.compile(r"<a?:\w+:\d+>")              # Discord custom emoji
EMOTICON = re.compile(r"(?<!\w)(?:[:;=8][-']?[)(DPpOo/\\|3*]+|<3|\^\^|:\w+:)(?!\w)")
_word = re.compile(r"(?<![\w/'])(" + "|".join(re.escape(k) for k in sorted(WORDS, key=len, reverse=True)) +
                   r")(?![\w/'])", re.IGNORECASE)


def _no_symbols(text: str) -> str:
    """Drop emoji and other pictographs"""
    return "".join(c for c in text if unicodedata.category(c) not in ("So", "Sk", "Cs", "Co")
                   and c not in "‍️︎")


def spoken(text: str) -> str:
    if not text:
        return text
    original = text
    text = EMOTE.sub(" ", text)
    text = _no_symbols(text)
    text = EMOTICON.sub(" ", text)
    laughed = bool(LAUGH.search(text))
    text = LAUGH.sub(" ", text)
    text = _word.sub(lambda m: WORDS[m.group(1).lower()], text)
    text = re.sub(r"[*_~`]+", "", text)                      # markdown
    text = re.sub(r"\s+([,.!?])", r"\1", text)               # "you down ?" -> "you down?"
    text = re.sub(r"^[\s,.!?]+|(?<=[,.!?])[,.!?]+", "", text)  # leftover punctuation
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    if not re.search(r"\w", text):
        return "haha" if laughed or original.strip() else ""
    return text
