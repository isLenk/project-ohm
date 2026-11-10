"""/web: ddgs search, then a short sourced answer from the snippets (`web` role)"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from ddgs import DDGS

import brain
from brain import Task

os.environ.setdefault("BRAIN_ROLE_WEB", "codex,openrouter")

# ? Engines often come back empty, so race them. Only these work from the NAS
BACKENDS = ("yahoo", "google", "bing")
RESULTS = 6
WAIT = 6             # seconds per round
ROUNDS = 2           # retries often succeed
SECONDS = 120        # model timeout
MAX_MESSAGE = 1900   # Discord max is 2000

SYSTEM = """You answer a question from web search results. Use only what the results say.

Answer in at most four short sentences, or a few short bullet points when it's a list. Put the result's number
in square brackets after what you took from it, like [2]. If the results don't answer the question, say so in
one sentence and say what they do cover. Plain text, no headings. Today is {today}."""


def search(query: str) -> list:
    """Top results as [{title, body, href}], or []"""
    for _ in range(ROUNDS):
        results = ask_engines(query)
        if results:
            return results
    print(f"[web] no results for {query!r}")
    return []


def ask_engines(query: str) -> list:
    """Race all engines; first non-empty result within WAIT"""
    pool = ThreadPoolExecutor(max_workers=len(BACKENDS))
    asked = {pool.submit(lambda b=b: DDGS(timeout=WAIT).text(query, max_results=RESULTS, backend=b)): b
             for b in BACKENDS}
    try:
        for done in as_completed(asked, timeout=WAIT):
            try:
                results = done.result()
            except Exception:
                continue  # empty or timed out
            if results:
                print(f"[web] {query!r} answered by {asked[done]}")
                return [{"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                        for r in results]
    except TimeoutError:
        pass
    finally:
        pool.shutdown(wait=False)
    return []


def answer(query: str, results: list):
    """Short answer citing [n], or None if no backend answered"""
    numbered = "\n\n".join(f"[{n}] {r['title']}\n{r['body']}" for n, r in enumerate(results, 1))
    return brain.run(Task("web", SYSTEM.format(today=date.today().isoformat()),
                          f"Question: {query}\n\nSearch results:\n\n{numbered}",
                          max_tokens=500, reasoning="low", timeout=SECONDS))


def run(query: str) -> str:
    """Discord message: question, answer, sources"""
    results = search(query)
    if not results:
        return f"**{query}**\nThe search came back empty. Try again, or word it differently."
    said = answer(query, results)
    # Cited sources only, else the first three
    used = [n for n in range(1, len(results) + 1) if f"[{n}]" in (said or "")] or [1, 2, 3]
    # ? <> suppresses link previews
    links = "\n".join(f"{n}. [{r['title'][:80] or r['href']}](<{r['href']}>)"
                      for n, r in enumerate(results, 1) if n in used)
    if said is None:
        return f"**{query}**\nI couldn't write an answer right now, but this is what came up:\n{links}"[:MAX_MESSAGE]
    head = f"**{query}**\n{said.strip()}\n\n"
    while len(head + links) > MAX_MESSAGE and "\n" in links:
        links = links.rsplit("\n", 1)[0]
    return (head + links)[:MAX_MESSAGE]
