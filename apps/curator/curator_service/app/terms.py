"""General terminology: words this chat uses that a model wouldn't know, and what they mean.

See Documentation/Curator/terminology.md.
"""

import hashlib
import json
import re
import threading
from collections import Counter, defaultdict
from typing import Optional

import requests

from . import llm
from .config import MEMORY_API_KEY, MEMORY_API_URL
from .errors import ApiError, not_found
from .project import now_ms

MIN_USES = 4     # a word has to come up this often,
MIN_PEOPLE = 2   # from this many people, to be asked about; one-offs are mostly typos nobody needs explained
EXAMPLES = 3

# Internet shorthand any chat model already knows. Not worth asking about.
KNOWN_SLANG = set("""
lol lmao lmfao rofl wtf wth omg omfg idk idc idgaf btw tbh ngl nvm smh imo imho irl afk brb gg ggs ez wp
fr frfr ong istg lmk ty thx tysm np yw pls plz ppl prob probs cuz cos bc tho thru rn tmrw tmr tmrw
bruh bro bros dude yall ya yea yeah yep yup nah nope ok okay kk k aight ight ayy ayo yo sup wassup gonna
wanna gotta gimme lemme dunno kinda sorta tryna finna ain't aint y'all hmm hm uh um uhh umm huh meh
haha hahaha hehe lmaoo xd xD tf wdym wym hbu wbu ikr ily jk gl hf glhf bbl bbs gtg g2g ttyl
dm dms pm vc vcs fps rpg mmo pvp pve dlc npc op nerf buff noob noobs pog poggers cringe based sus
goated mid lowkey highkey deadass bet cap salty toxic meta irl ig tbf smth sth bday asap fyi etc
""".split())

# Contractions people type without the apostrophe. The vocabulary splits "don't" into "don" + "t" anyway.
NO_APOSTROPHE = set("""
dont cant wont isnt arent wasnt werent didnt doesnt hasnt havent hadnt couldnt wouldnt shouldnt mustnt
thats whats hes shes theres heres wheres whos hows its im ive id ill youre youve youd youll theyre
theyve theyd theyll were weve wed lets
""".split())

URL = re.compile(r"https?://\S+|<a?:\w+:\d+>|<[@#]!?&?\d+>|@\S+|:\w+:")
WORD = re.compile(r"[a-z][a-z']*[a-z]|[a-z]")
REPEATS = re.compile(r"(.)\1{2,}")

_vocab: Optional[set] = None


def vocabulary() -> set:
    """Whole words the embedding model's tokenizer knows (about 22k): common English, plenty of names."""
    global _vocab
    if _vocab is None:
        try:
            from transformers import AutoTokenizer

            from .embeddings import MODEL_NAME

            try:
                tok = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
            except OSError:
                tok = AutoTokenizer.from_pretrained(MODEL_NAME)
        except Exception as e:  # not installed, or no model and no internet
            raise ApiError(503, "no_vocabulary",
                           f"Finding unfamiliar words needs the embedding model's vocabulary ({e}).") from e
        _vocab = {w for w in tok.vocab if w.isalpha()}
    return _vocab


def _norm(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip().lower())


def _known(w: str, vocab: set, names: set) -> bool:
    if w in vocab or w in KNOWN_SLANG or w in NO_APOSTROPHE or w in names:
        return True
    if "'" in w:
        return all(not p or p in vocab for p in w.split("'"))
    if len(set(w)) == 1:  # "aaaa", "hhh"
        return True
    return w.endswith("s") and (w[:-1] in vocab or w[:-1] in KNOWN_SLANG)


def _key(w: str, vocab: set, names: set) -> Optional[str]:
    """The form a word is filed under, or None if it's a normal word. "lmaooo" is "lmao"; "ceebs" is "ceeb"
    once "ceeb" turns up on its own too (that's resolved after counting)."""
    w = w.strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) < 2:
        return None
    if REPEATS.search(w):
        two, one = REPEATS.sub(r"\1\1", w), REPEATS.sub(r"\1", w)
        if _known(two, vocab, names) or _known(one, vocab, names):
            return None
        w = two
    return None if _known(w, vocab, names) else w


def detect(conn) -> dict[str, dict]:
    """Every unfamiliar word used at least MIN_USES times by MIN_PEOPLE people, newest examples first."""
    vocab = vocabulary()
    names: set = set()
    for n, d in conn.execute("SELECT name, display_name FROM authors"):
        for x in (n, d):
            if x:
                names.update(re.findall(r"[a-z]+", x.lower()))
    uses: Counter = Counter()
    people: dict[str, set] = defaultdict(set)
    examples: dict[str, list] = defaultdict(list)
    rows = conn.execute("SELECT id, author_id, content_text FROM messages WHERE excluded = 0 ORDER BY ts DESC")
    for mid, author, text in rows:
        seen = set()
        for w in WORD.findall(URL.sub(" ", text.lower())):
            k = _key(w, vocab, names)
            if not k:
                continue
            uses[k] += 1
            people[k].add(author)
            if k not in seen and len(examples[k]) < EXAMPLES:
                examples[k].append(mid)
            seen.add(k)
    # A plural goes under its singular when both are used.
    for k in [k for k in uses if k.endswith("s") and k[:-1] in uses]:
        base = k[:-1]
        uses[base] += uses.pop(k)
        people[base] |= people.pop(k)
        examples[base] = (examples[base] + examples.pop(k))[:EXAMPLES]
    found = {k: {"uses": n, "people": len(people[k]), "examples": examples[k]}
             for k, n in uses.items() if n >= MIN_USES and len(people[k]) >= MIN_PEOPLE}
    knows = model_knows(list(found))
    return {k: v for k, v in found.items() if k not in knows}


SUFFIXES = {"s", "es", "ed", "d", "ing", "er", "ers", "ly", "'s"}


def model_knows(words: list[str]) -> set:
    """Words Ohm's own model has as one token ("refund", "sushi"), or one token plus an ending ("refunds").
    Slang, names and typos get split up ("ce|eb|s", "air|soft"). The embedding vocabulary is small, so this
    takes out most of the ordinary words it misses. Empty when llama-server isn't running."""
    known = set()
    splits = llm.pieces(words)
    if splits is None:
        return known
    for w, ps in zip(words, splits):
        if len(ps) == 1 or (len(ps) == 2 and ps[1] in SUFFIXES and len(ps[0].strip()) >= 3):
            known.add(w)
    return known


def scan(conn) -> dict:
    """Refresh detected terms. Terms the user touched (defined, linked, marked normal, or added) stay;
    untouched ones that no longer qualify go."""
    found = detect(conn)
    t = now_ms()
    existing = {r["term"]: r for r in conn.execute("SELECT * FROM terms")}
    added = 0
    for term, d in found.items():
        if term in existing:
            conn.execute("UPDATE terms SET uses = ?, people = ?, examples = ? WHERE id = ?",
                         (d["uses"], d["people"], json.dumps(d["examples"]), existing[term]["id"]))
        else:
            conn.execute(
                "INSERT INTO terms (term, origin, uses, people, examples, created_at, updated_at) VALUES (?, 'detected', ?, ?, ?, ?, ?)",
                (term, d["uses"], d["people"], json.dumps(d["examples"]), t, t),
            )
            added += 1
    stale = [r["id"] for term, r in existing.items() if term not in found and r["origin"] == "detected" and not _touched(r)]
    conn.executemany("DELETE FROM terms WHERE id = ?", [(i,) for i in stale])
    return {"found": len(found), "added": added, "removed": len(stale)}


def _touched(r) -> bool:
    return bool(r["definition"] or r["related_term"] or r["ignored"])


def status(r) -> str:
    if r["ignored"]:
        return "ignored"
    if r["related_term"]:
        return "linked"
    if r["definition"]:
        return "defined"
    return "unknown"


def _examples_for(conn, term: str, limit: int = EXAMPLES) -> list[str]:
    """Newest messages that use a term the user added by hand (the scan only counts unfamiliar words)."""
    phrase = '"' + term.replace('"', '""') + '"'
    try:
        rows = conn.execute(
            "SELECT m.id FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH ? AND m.excluded = 0 ORDER BY m.ts DESC LIMIT ?", (phrase, limit)
        ).fetchall()
    except Exception:
        return []
    return [r[0] for r in rows]


def _out(conn, r, by_term: dict) -> dict:
    ids = json.loads(r["examples"] or "[]")
    msgs = []
    if ids:
        found = {m["id"]: m for m in conn.execute(
            "SELECT m.id, m.ts, m.content_text, COALESCE(a.display_name, a.name) AS who FROM messages m "
            f"JOIN authors a ON a.id = m.author_id WHERE m.id IN ({','.join('?' * len(ids))})", ids
        )}
        msgs = [dict(found[i]) for i in ids if i in found]
    related = by_term.get(r["related_term"]) if r["related_term"] else None
    return {
        "id": r["id"], "term": r["term"], "definition": r["definition"], "relation": r["relation"],
        "related_term": r["related_term"], "related_definition": related["definition"] if related else None,
        "ignored": bool(r["ignored"]), "origin": r["origin"], "uses": r["uses"], "people": r["people"],
        "status": status(r), "examples": msgs,
        # The model's guess, waiting for approval: {kind: definition|typo|same|normal|unsure, ...}.
        "suggestion": json.loads(r["suggestion"]) if r["suggestion"] else None,
    }


def listing(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM terms ORDER BY uses DESC, term").fetchall()
    by_term = {r["term"]: r for r in rows}
    return [_out(conn, r, by_term) for r in rows]


def get(conn, term_id: int):
    r = conn.execute("SELECT * FROM terms WHERE id = ?", (term_id,)).fetchone()
    if not r:
        raise not_found("Term")
    return r


def one(conn, term_id: int) -> dict:
    r = get(conn, term_id)
    by_term = {r["term"]: r}
    if r["related_term"]:
        rel = conn.execute("SELECT * FROM terms WHERE term = ?", (r["related_term"],)).fetchone()
        if rel:
            by_term[rel["term"]] = rel
    return _out(conn, r, by_term)


def _check(definition, relation, related_term, term: str):
    if relation is not None and relation not in ("typo", "same", ""):
        raise ApiError(422, "bad_relation", "A related word is a typo of it or means the same.")
    if related_term and _norm(related_term) == term:
        raise ApiError(422, "self", "A word can't be related to itself.")
    if relation and not related_term:
        raise ApiError(422, "no_related", "Say which word it's related to.")


def save(conn, term: str, definition: Optional[str] = None, relation: Optional[str] = None,
         related_term: Optional[str] = None) -> dict:
    """Add a term, or update it if it's already there (what Ctrl+click on a word does)."""
    key = _norm(term)
    if not key:
        raise ApiError(422, "empty", "Pick a word first.")
    if len(key) > 80:
        raise ApiError(422, "too_long", "Terms are a word or a short phrase.")
    _check(definition, relation, related_term, key)
    r = conn.execute("SELECT * FROM terms WHERE term = ?", (key,)).fetchone()
    if r is None:
        t = now_ms()
        cur = conn.execute(
            "INSERT INTO terms (term, origin, uses, people, examples, created_at, updated_at) VALUES (?, 'manual', 0, 0, ?, ?, ?)",
            (key, json.dumps(_examples_for(conn, key)), t, t),
        )
        tid = cur.lastrowid
    else:
        tid = r["id"]
    return update(conn, tid, definition=definition, relation=relation, related_term=related_term,
                  ignored=False if (definition or related_term) else None)


def update(conn, term_id: int, definition: Optional[str] = None, relation: Optional[str] = None,
           related_term: Optional[str] = None, ignored: Optional[bool] = None) -> dict:
    """None leaves a field alone; "" clears it."""
    r = get(conn, term_id)
    _check(definition, relation, related_term, r["term"])
    sets, params = ["updated_at = ?"], [now_ms()]
    if definition is not None:
        sets.append("definition = ?")
        params.append(definition.strip() or None)
    if related_term is not None:
        rel = _norm(related_term) or None
        sets += ["related_term = ?", "relation = ?"]
        params += [rel, (relation or "same") if rel else None]
    elif relation is not None and r["related_term"]:
        sets.append("relation = ?")
        params.append(relation or "same")
    if ignored is not None:
        sets.append("ignored = ?")
        params.append(int(ignored))
    conn.execute(f"UPDATE terms SET {', '.join(sets)} WHERE id = ?", [*params, term_id])
    return one(conn, term_id)


def delete(conn, term_id: int) -> None:
    get(conn, term_id)
    conn.execute("DELETE FROM terms WHERE id = ?", (term_id,))


def counts(conn) -> dict:
    out = {"unknown": 0, "defined": 0, "linked": 0, "ignored": 0}
    for r in conn.execute("SELECT definition, related_term, ignored FROM terms"):
        out[status(r)] += 1
    return out


# ── for prompts ─────────────────────────────────────────────────────────────
def defined(conn) -> list[dict]:
    """Terms with a definition or a link: the ones worth telling a model about."""
    return [dict(r) for r in conn.execute(
        "SELECT term, definition, relation, related_term FROM terms WHERE ignored = 0 "
        "AND (definition IS NOT NULL OR related_term IS NOT NULL) ORDER BY term"
    )]


def glossary_for(conn, text: str, limit: int = 12, extra: list[dict] = ()) -> list[str]:
    """Lines explaining the defined or linked terms that appear in `text`, for the extraction prompt.
    `extra` is terms from other projects (shared through memory_service); this project's own win."""
    by_term = {r["term"]: r for r in extra}
    by_term.update({r["term"]: r for r in defined(conn)})
    rows = list(by_term.values())
    if not rows:
        return []
    low = text.lower()
    words = set(WORD.findall(low))
    defs = {r["term"]: r["definition"] for r in rows if r["definition"]}
    lines = []
    for r in rows:
        t = r["term"]
        if not ((" " in t and t in low) or t in words or (t + "s") in words):
            continue
        if r["related_term"]:
            how = "is a typo of" if r["relation"] == "typo" else "means the same as"
            meaning = defs.get(r["related_term"])
            lines.append(f'"{t}" {how} "{r["related_term"]}"' + (f": {meaning}" if meaning else ""))
        else:
            lines.append(f'"{t}": {r["definition"]}')
        if len(lines) >= limit:
            break
    return lines


# ── suggested definitions ───────────────────────────────────────────────────
# The local model guesses what each unknown term means from the messages that use it. A guess is stored
# in `suggestion` and does nothing until the user approves it.
SUGGEST_EXAMPLES = 6
SUGGEST_CHARS = 220
SUGGEST_TOKENS = 160
SUGGEST_KNOWN = 20  # already-explained words given as background
MAX_DEFINITION = 140

SUGGEST_SYSTEM = (
    "You explain words a group of friends uses in their group chat. You get one word and messages where "
    "they used it. Work out what it means in those messages.\n"
    "Reply with JSON only, in this shape:\n"
    '{"meaning": "what it means in these messages, in a few words", "kind": "slang", '
    '"definition": "One short sentence.", "same_as": null}\n'
    "Things people play, buy, ride, drive or go to are games, gear, transit or places. Don't read an "
    "abbreviation as an ordinary word that happens to share its letters; if the messages point to a game, "
    "place or company, it's that.\n"
    "kind is one of:\n"
    "- \"normal\": an ordinary word, brand, store, game, film or place that any adult already knows, spelled "
    "right (paintball, walmart, minecraft). Nothing to explain.\n"
    "- \"typo\": a misspelling of an ordinary word. same_as is that word, spelled right.\n"
    "- \"short\": a short form or other spelling of one ordinary word (alc: alcohol, ofc: of course). same_as "
    "is the full word or phrase.\n"
    "- \"slang\": slang, an in-joke, or a word the group made up. definition says what it means.\n"
    "- \"name\": a person's name or nickname. definition says who they seem to be, from the messages.\n"
    "- \"thing\": an abbreviation, local place, product, gear or game a stranger wouldn't know (ow: Overwatch, "
    "a team shooter game). "
    "definition says what it is, with the full name if it's an abbreviation.\n"
    "- \"unsure\": the messages don't make it clear. Prefer this to guessing.\n"
    "definition: under 15 words, plain, not starting with the word itself. null for normal, typo and short.\n"
    "Slurs and crude words still get a plain, accurate definition."
)


def _examples_text(conn, r) -> list[str]:
    ids = json.loads(r["examples"] or "[]")
    for i in _examples_for(conn, r["term"], SUGGEST_EXAMPLES):
        if i not in ids:
            ids.append(i)
    ids = ids[:SUGGEST_EXAMPLES]
    if not ids:
        return []
    found = {m["id"]: m for m in conn.execute(
        "SELECT m.id, m.content_text, COALESCE(a.display_name, a.name) AS who FROM messages m "
        f"JOIN authors a ON a.id = m.author_id WHERE m.id IN ({','.join('?' * len(ids))})", ids
    )}
    return [f"{found[i]['who']}: {found[i]['content_text'][:SUGGEST_CHARS]}" for i in ids if i in found]


def _suggest_prompt(conn, r) -> str:
    lines = _examples_text(conn, r)
    prompt = f'Word: "{r["term"]}"\n\nMessages that use it:\n' + ("\n".join(lines) or "(none)")
    # Words already explained say who the group is ("scarbs": Scarborough, Ontario), so the ones that come
    # up here first, then the rest.
    here = glossary_for(conn, "\n".join(lines), limit=SUGGEST_KNOWN)
    rest = [g for g in glossary_for(conn, " ".join(d["term"] for d in defined(conn)), limit=SUGGEST_KNOWN) if g not in here]
    known = [g for g in here + rest if not g.startswith(f'"{r["term"]}"')][:SUGGEST_KNOWN]
    if known:
        prompt = "Words the group already explained:\n" + "\n".join(known) + "\n\n" + prompt
    return prompt


def _read_suggestion(term: str, out) -> dict:
    """The model's answer as a stored suggestion: definition, typo/same with related_term, normal, or unsure."""
    if not isinstance(out, dict):
        return {"kind": "unsure"}
    s = _read_kind(term, out)
    meaning = str(out.get("meaning") or "").strip()[:MAX_DEFINITION]
    if meaning and s["kind"] != "unsure":
        s["meaning"] = meaning  # the model's reading, shown next to the suggestion so it's quick to judge
    return s


def _read_kind(term: str, out: dict) -> dict:
    kind = str(out.get("kind") or "").strip().lower()
    definition = str(out.get("definition") or "").strip().strip('"').strip()
    same_as = _norm(str(out.get("same_as") or "")).strip('"')
    if definition.lower() in ("null", "none", "n/a"):
        definition = ""
    if len(definition) > MAX_DEFINITION:
        definition = definition[:MAX_DEFINITION].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    if kind == "normal":
        return {"kind": "normal"}
    if kind in ("typo", "short") and same_as and same_as != term and same_as != "null":
        return {"kind": "typo" if kind == "typo" else "same", "related_term": same_as}
    if kind in ("slang", "name", "thing", "typo", "short") and definition:
        return {"kind": "definition", "definition": definition, "label": kind if kind in ("slang", "name", "thing") else None}
    return {"kind": "unsure"}


def suggest(project, job, ids: Optional[list[int]] = None) -> dict:
    """Ask the local model about every unknown term that has no suggestion yet (or just `ids`), most used first."""
    if not llm.up():
        raise ApiError(503, "llm_down", "llama-server isn't running on :5000, so definitions can't be suggested.")
    sql = ("SELECT id FROM terms WHERE ignored = 0 AND definition IS NULL AND related_term IS NULL")
    params: list = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params = list(ids)
    else:
        sql += " AND suggestion IS NULL"
    todo = [r["id"] for r in project.all(sql + " ORDER BY uses DESC, term", params)]
    counts: Counter = Counter()
    failed = 0
    stopped = None
    for i, tid in enumerate(todo):
        if job.cancelled:
            break
        job.progress(i, len(todo), "suggesting definitions")
        with project.lock:
            r = project.conn.execute("SELECT * FROM terms WHERE id = ?", (tid,)).fetchone()
            if r is None or r["definition"] or r["related_term"] or r["ignored"]:
                continue  # dealt with while the job ran
            prompt = _suggest_prompt(project.conn, r)
        try:
            out = llm.chat_json(SUGGEST_SYSTEM, prompt, max_tokens=SUGGEST_TOKENS)
        except Exception:  # requests errors; the term stays without a suggestion so the next run retries it
            if not llm.up():
                stopped = "llama-server stopped answering."
                break
            failed += 1
            continue
        s = _read_suggestion(r["term"], out)
        counts[s["kind"]] += 1
        with project.tx() as conn:
            conn.execute("UPDATE terms SET suggestion = ? WHERE id = ? AND definition IS NULL AND related_term IS NULL",
                         (json.dumps(s), tid))
    job.progress(len(todo), len(todo), "done")
    return {"terms": len(todo), "suggested": sum(counts.values()), "kinds": dict(counts), "failed": failed, "stopped": stopped}


def accept(conn, term_id: int) -> dict:
    """Apply a term's suggestion as if the user had typed it."""
    r = get(conn, term_id)
    s = json.loads(r["suggestion"]) if r["suggestion"] else None
    if not s or s.get("kind") == "unsure":
        raise ApiError(422, "no_suggestion", "There's no suggestion to approve for this term.")
    if s["kind"] == "normal":
        return update(conn, term_id, ignored=True)
    if s["kind"] in ("typo", "same"):
        return update(conn, term_id, definition="", relation=s["kind"], related_term=s["related_term"], ignored=False)
    return update(conn, term_id, definition=s["definition"], related_term="", ignored=False)


def accept_all(conn) -> int:
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM terms WHERE suggestion IS NOT NULL AND json_extract(suggestion, '$.kind') != 'unsure' "
        "AND ignored = 0 AND definition IS NULL AND related_term IS NULL"
    )]
    for i in ids:
        accept(conn, i)
    return len(ids)


def dismiss(conn, term_id: int) -> dict:
    """Throw a suggestion away. The term stays unknown, and isn't asked about again until asked."""
    get(conn, term_id)
    conn.execute("UPDATE terms SET suggestion = ? WHERE id = ?", (json.dumps({"kind": "unsure", "dismissed": True}), term_id))
    return one(conn, term_id)


# ── shared with the bot ─────────────────────────────────────────────────────
# Defined and linked terms go to memory_service's glossary, where the bot reads them for every reply and
# other projects read them while extracting. Each project replaces its own set, so removed terms go too.
_share_lock = threading.Lock()
_shared: dict[str, dict] = {}  # source -> how the last push went


def source_id(project) -> str:
    """Stays the same through renames and Save As, so the project doesn't leave old copies behind."""
    created = project.settings().get("created_at")
    if created:
        return f"curator-{created}"
    return "curator-" + hashlib.sha1(str(project.path).encode()).hexdigest()[:12]


def _headers() -> dict:
    return {"X-API-Key": MEMORY_API_KEY}


def _failed(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (404, 405):
        return "memory_service is too old to take terms; rebuild it on the NAS (docker compose up -d --build api)."
    if isinstance(e, requests.ConnectionError):
        return "The memory service isn't reachable."
    return str(e)


def share(project) -> dict:
    """Send this project's defined terms to memory_service. Fails soft: the next change tries again."""
    with project.lock:
        source = source_id(project)
    if not MEMORY_API_URL:
        return {"ok": False, "terms": 0, "at": None, "error": "Set MEMORY_API_URL in the root .env to share terms with Ohm."}
    with _share_lock:  # one push at a time, each reading the latest terms, so the last one sent is current
        with project.lock:
            rows = defined(project.conn)
        try:
            r = requests.put(f"{MEMORY_API_URL}/glossary/{source}", json={"terms": rows}, headers=_headers(), timeout=(2, 10))
            r.raise_for_status()
            _shared[source] = {"ok": True, "terms": len(rows), "at": now_ms(), "error": None}
        except requests.RequestException as e:
            print(f"[curator] could not share terms: {e}")
            _shared[source] = {"ok": False, "terms": len(rows), "at": now_ms(), "error": _failed(e)}
        return _shared[source]


def share_soon(project) -> None:
    """share() in the background, so saving a term never waits on the NAS."""
    if not MEMORY_API_URL:
        return

    def work():
        try:
            share(project)
        except Exception as e:  # the project was closed before it ran, say; the next change shares again
            print(f"[curator] could not share terms: {e}")

    threading.Thread(target=work, daemon=True).start()


def shared_status(project) -> Optional[dict]:
    with project.lock:
        return _shared.get(source_id(project))


def from_other_projects(project) -> list[dict]:
    """Terms other Curator projects defined, for the extraction prompt. Empty when the NAS is out of reach."""
    if not MEMORY_API_URL:
        return []
    with project.lock:
        mine = source_id(project)
    try:
        r = requests.get(f"{MEMORY_API_URL}/glossary", headers=_headers(), timeout=(2, 10))
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[curator] could not read shared terms: {e}")
        return []
    return [{k: t.get(k) for k in ("term", "definition", "relation", "related_term")}
            for t in r.json() if t.get("source") != mine]
