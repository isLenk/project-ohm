"""Turn what the model writes into one clean reply, and its "@Name" mentions into real ones.

The model sometimes writes a whole fake transcript ("Ohm: yo\\nLenk: you there\\nOhm: ...") instead of one
reply. Saving that teaches it to echo people on later turns, so the reply is cut where the next speaker's
line starts. Messages reach it as "Name: text" lines, the same labels it uses when it does this.

Mentions are "@DisplayName" plain text in the training data and the history. In text chat they become
<@id> so they actually ping; in voice the "@" is dropped so the TTS doesn't read it out.
No discord import here, so all of this can be tested on its own.
"""
import re

_USER_LABEL = re.compile(r"([^:\n]{1,32}): ")  # "Lenk: yo" -> "Lenk" (Discord names are at most 32 characters)


def speaker_labels(history, names=()) -> list[str]:
    """Everyone talking: the label on each user message in the history plus `names`, without repeats."""
    labels = []
    for m in history or []:
        if m.get("role") == "user":
            match = _USER_LABEL.match(m.get("content") or "")
            if match:
                labels.append(match[1].strip())
    seen, out = set(), []
    for label in labels + [n for n in names if n]:
        if label and label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out


def stop_sequences(labels) -> list[str]:
    """Stop the model where it starts writing someone's next line ("\\nLenk:", "\\nlenk:")."""
    out = []
    for label in labels:
        for s in (f"\n{label}:", f"\n{label.lower()}:"):
            if s not in out:
                out.append(s)
    return out


def strip_leading_label(text: str, names) -> str:
    """Drop the "Ohm:" the reply starts with, or the label of someone else in the chat ("Lenk: ..."): the model
    copies the "Name: text" form the messages reach it in. Only the first one: a later label line is a
    transcript (cut_transcript). names: one name or several."""
    names = sorted({n for n in ([names] if isinstance(names, str) else names) if n}, key=len, reverse=True)
    if not names:
        return text
    # ? Only spaces after the colon: eating a newline would hide the next line's label from cut_transcript
    pattern = r"^\s*(?:" + "|".join(re.escape(n) for n in names) + r")[ \t]*:[ \t]*"
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)


def cut_transcript(text: str, labels) -> str:
    """Everything before the first later line that starts with a speaker label."""
    labels = sorted({l for l in labels if l}, key=len, reverse=True)
    if not labels:
        return text.strip()
    pattern = r"\n[ \t]*(?:" + "|".join(re.escape(l) for l in labels) + r")[ \t]*:"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return (text[:match.start()] if match else text).strip()


def clean_reply(text: str, name: str, labels) -> str:
    """One reply: no leading "Ohm:" or "Lenk:", and nothing from where the model starts writing other people's lines."""
    return cut_transcript(strip_leading_label(text, [name, *labels]), [*labels, name])


# ── Mentions ────────────────────────────────────────────────────────────────

def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def readable_mentions(content: str, users: dict, roles: dict | None = None, channels: dict | None = None) -> str:
    """'<@123> gg' -> '@Lenk gg', like the training data. Ids that aren't in the dicts are left alone."""
    def repl(m):
        kind, i = m[1], int(m[2])
        table, sign = {"#": (channels, "#"), "@&": (roles, "@")}.get(kind, (users, "@"))
        name = (table or {}).get(i)
        return f"{sign}{name}" if name else m[0]
    return re.sub(r"<(@[!&]?|#)(\d+)>", repl, content)


def member_names(member) -> list[str]:
    """What people call someone: server display name, global name, username."""
    names = []
    for attr in ("display_name", "global_name", "name"):
        n = getattr(member, attr, None)
        if n and n.lower() not in (x.lower() for x in names):
            names.append(n)
    return names


def _mention_at(text: str, i: int, candidates):
    """(member, end) if text[i] is "@" followed by one of the candidates' names (longest first), else None."""
    for name, member in candidates:
        end = i + 1 + len(name)
        if text[i + 1:end].lower() == name.lower() and (end == len(text) or not _word(text[end])):
            return member, end
    return None


def _candidates(members):
    return sorted(((n, m) for m in members for n in member_names(m)), key=lambda c: len(c[0]), reverse=True)


def outgoing_mentions(text: str, members, reply_to=None) -> tuple[str, str]:
    """(for Discord, for the history) versions of a reply.

    A leading "@Name" of the person being replied to is dropped (the reply already pings them).
    Other "@Name"s that match a member become <@id> for Discord and "@DisplayName" for the history;
    an "@Name" that matches nobody just loses its "@" (it wouldn't ping, and "@everyone" shouldn't).
    """
    if reply_to is not None:
        own = _candidates([reply_to])
        rest = text.lstrip()
        while rest.startswith("@") and (hit := _mention_at(rest, 0, own)):
            rest = rest[hit[1]:].lstrip(" \t,:;")
        text = rest if rest != text.lstrip() else text

    everyone = _candidates(members)
    to_discord, to_history, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == "@" and (i == 0 or not _word(text[i - 1])):  # not the "@" in an email address
            hit = _mention_at(text, i, everyone)
            if hit:
                member, i = hit
                to_discord.append(f"<@{member.id}>")
                to_history.append("@" + member_names(member)[0])
                continue
            if i + 1 < len(text) and _word(text[i + 1]):
                i += 1
                continue
        to_discord.append(ch)
        to_history.append(ch)
        i += 1
    return "".join(to_discord).strip(), "".join(to_history).strip()


class AtFilter:
    """Drops the "@" in front of names on a stream for the TTS ("@lenk" would be read "at lenk").
    Only the character before an "@" decides, so an "@" arriving in a chunk of its own is fine."""

    def __init__(self):
        self.prev = ""

    def feed(self, text: str) -> str:
        out = []
        for ch in text:
            if not (ch == "@" and not _word(self.prev)):
                out.append(ch)
            self.prev = ch
        return "".join(out)


def drop_at_mentions(text: str) -> str:
    return AtFilter().feed(text)
