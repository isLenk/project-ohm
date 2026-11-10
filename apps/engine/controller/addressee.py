"""Who a message is for, so Ohm doesn't answer a question meant for someone else.
Names and @mentions first, then pre_checks' "to", then the last person who said something.
"""

import re

YOU = re.compile(r"(?<![\w'])(?:u|you|ur|your|yours|youre|you're|ya|yall|y'all)(?![\w'])", re.IGNORECASE)
LABEL = re.compile(r"^([^:\n]{1,40}):\s")


def _word(name: str) -> re.Pattern:
    return re.compile(r"(?<![\w])@?" + re.escape(name) + r"(?![\w])", re.IGNORECASE)


def _names_ohm(text: str, bot: str) -> bool:
    return bool(_word(bot).search(text))


def _called(text: str, aliases: list[str]) -> bool:
    """@mentions them, or starts with their name and talks to them ("zhing what did u say", not "lenk is cooked")"""
    for name in aliases:
        if len(name) < 2:
            continue
        start = re.match(r"^\s*@?" + re.escape(name) + r"(?![\w])", text, re.IGNORECASE)
        if start:
            rest = text[start.end():]
            if rest.lstrip().startswith(",") or "?" in rest or YOU.search(rest):
                return True
        if re.search(r"(?<![\w])@" + re.escape(name) + r"(?![\w])", text, re.IGNORECASE):
            return True
    return False


def addressee(text: str, speaker: str, bot: str, people: dict[str, list[str]], previous: str | None):
    """(who, sure) when `text` is for someone other than the bot, else None.
    people: {display name: every name they go by}. previous: who spoke before."""
    if not text or _names_ohm(text, bot):
        return None
    for who, aliases in people.items():
        if who in (speaker, bot) or who.lower() == bot.lower():
            continue
        if _called(text, [who, *aliases]):
            return who, True
    if previous and previous not in (speaker, bot) and YOU.search(text):
        return previous, False
    return None


REACTION_WORDS = 3  # "thats brutal", "LMAO"


def _reaction(text: str) -> bool:
    return "?" not in text and len(re.findall(r"\w+", text)) <= REACTION_WORDS


def _lines(history: list[dict], bot: str):
    """(speaker, text) per line, newest first"""
    for m in reversed(history or []):
        if m["role"] == "assistant":
            yield bot, m.get("content") or ""
            continue
        parsed, speaker = [], ""
        for line in (m.get("content") or "").split("\n"):
            label = LABEL.match(line)
            if label:
                speaker, line = label.group(1).strip(), line[label.end():]
            parsed.append((speaker, line))
        yield from reversed(parsed)


def last_speaker(history: list[dict], bot: str, skip: str | None = None) -> str | None:
    """Who the next "you" is likely for, skipping `skip` and short reactions"""
    reacted = None
    for speaker, text in _lines(history, bot):
        if speaker == bot:
            return reacted or bot
        if not speaker or speaker == skip:
            continue
        if _reaction(text):
            reacted = reacted or speaker  # fallback only
            continue
        return speaker
    return reacted


def split(line: str) -> list[tuple[str, str]]:
    """Turn.line as (speaker, text); unlabeled lines carry on the one above"""
    parsed = []
    for l in (line or "").split("\n"):
        if not l.strip():
            continue
        m = LABEL.match(l)
        parsed.append((m.group(1).strip(), l[m.end():]) if m else (parsed[-1][0] if parsed else "", l))
    return parsed


def aside(line: str, history: list[dict], bot: str, people: dict[str, list[str]], to: str | None = None) -> str:
    """The "not for you" note for this turn (Turn.line), or "". to: pre_checks' answer, if asked."""
    parsed = split(line)
    if not parsed:
        return ""
    speaker = parsed[-1][0]
    # The last speaker's run of lines counts as one
    run = 0
    while run < len(parsed) and parsed[-1 - run][0] == speaker:
        run += 1
    text = " ".join(t for _, t in parsed[len(parsed) - run:])
    # Earlier lines of this turn count as history
    earlier = [{"role": "user", "content": "\n".join(f"{s}: {t}" for s, t in parsed[:len(parsed) - run])}]
    previous = last_speaker(list(history or []) + earlier if parsed[:len(parsed) - run] else history, bot, skip=speaker)
    explicit = _names_ohm(text, bot) or any(_called(text, [w, *a]) for w, a in people.items() if w not in (speaker, bot))
    if to is not None and not explicit:
        # Trust the model, but only a known name counts
        match = next((w for w, a in people.items() if to.strip().lower() in {n.lower() for n in [w, *a]}), None)
        found = (match, False) if match and match not in (speaker, bot) else None
    else:
        found = addressee(text, speaker, bot, people, previous)
    if not found:
        return ""
    who, sure = found
    return (f"{speaker} is {'' if sure else 'probably '}talking to {who}, not you. Don't answer for {who} or as "
            f"if it happened to you; if you say anything, say it as yourself.")
