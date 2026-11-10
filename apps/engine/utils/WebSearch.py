"""Web search for Ohm. Free search through the `ddgs` package (no API key).
Asks every backend at once and takes the first with results; a repeat query within REUSE_S shares one search.
"""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import requests
from ddgs import DDGS

# ? DuckDuckGo, Brave, Mojeek and Startpage never answer from this machine
BACKENDS = ("yahoo", "yandex", "bing", "google")
MAX_RESULTS = 3
TIMEOUT_S = 1.5     # reply without results after this
ENGINE_TIMEOUT = 4  # per engine; stragglers finish in the background
REUSE_S = 120       # how long a query's answer is reused
CONTROLLER_URL = "http://localhost:3001"  # the controller app (pyhandler.interface_api); optional

_recent: dict[str, tuple[float, Future]] = {}  # query -> (started, answer)
_lock = threading.Lock()
# ? Room for stragglers so the next search doesn't queue behind them
_engines = ThreadPoolExecutor(max_workers=4 * len(BACKENDS), thread_name_prefix="web-search")


def search(query: str) -> tuple[list[dict], str | None]:
    """The top results as [{title, body, href}], and an error message if the search failed."""
    key = _key(query)
    now = time.monotonic()
    with _lock:
        at, future = _recent.get(key, (0.0, None))
        mine = future is None or now - at > REUSE_S
        if mine:
            future = Future()
            _recent[key] = (now, future)
            for k in [k for k, (t, _) in _recent.items() if now - t > REUSE_S]:
                del _recent[k]
    if not mine:
        print(f"[web_search] {query} (same as a moment ago, reusing it)")
        return future.result()
    print(f"[web_search] {query}")
    try:
        answer = _race(query)
    except Exception as e:  # ? always set the Future, others may be waiting
        answer = ([], str(e) or type(e).__name__)
    if answer[1]:
        print(f"[web_search] failed: {answer[1]}")
    future.set_result(answer)
    return answer


def _key(query: str) -> str:
    return " ".join(query.lower().split())


def answered(query: str) -> bool:
    """search(query) has a fresh answer already"""
    with _lock:
        at, future = _recent.get(_key(query), (0.0, None))
    return future is not None and future.done() and time.monotonic() - at <= REUSE_S


def _engine(backend: str, query: str) -> list[dict]:
    return DDGS(timeout=ENGINE_TIMEOUT).text(query, max_results=MAX_RESULTS, backend=backend)


def _race(query: str) -> tuple[list[dict], str | None]:
    """First engine with results wins, nothing after TIMEOUT_S"""
    started = time.perf_counter()
    asked = {_engines.submit(_engine, b, query): b for b in BACKENDS}
    error = "No results found."
    try:
        for done in as_completed(asked, timeout=TIMEOUT_S):
            try:
                results = done.result()
            except Exception as e:
                error = str(e) or type(e).__name__
                continue
            if results:
                print(f"[web_search] answered by {asked[done]} in {(time.perf_counter() - started) * 1000:.0f} ms")
                return [{"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                        for r in results], None
    except TimeoutError:
        return [], f"No answer within {TIMEOUT_S:g}s."
    return [], error


def as_text(results: list[dict]) -> str | None:
    """Results the way the model gets them. No URLs: in voice they'd get read out loud."""
    return "\n\n".join(f"{r['title']}\n{r['body']}" for r in results) or None


def report(character: str, said: str, query: str, results: list[dict], error: str | None, ms: int):
    """Tell the controller about a search so it shows on the dashboard. In the background, and it's fine
    if the controller isn't running."""
    body = {"character": character, "said": said, "query": query, "results": results, "error": error, "ms": ms}

    def send():
        try:
            requests.post(f"{CONTROLLER_URL}/api/searches", json=body, timeout=3)
        except requests.RequestException:
            pass

    threading.Thread(target=send, daemon=True, name="search-report").start()
