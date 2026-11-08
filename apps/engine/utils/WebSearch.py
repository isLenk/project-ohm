"""Web search for Ohm. Free search through the `ddgs` package (no API key)."""
import threading

import requests
from ddgs import DDGS

# ? DuckDuckGo/Brave block this machine; Bing then Google are the ones that answer
BACKENDS = "bing,google"
MAX_RESULTS = 3
CONTROLLER_URL = "http://localhost:3001"  # the controller app (pyhandler.interface_api); optional


def search(query: str) -> tuple[list[dict], str | None]:
    """The top results as [{title, body, href}], and an error message if the search failed."""
    print(f"[web_search] {query}")
    try:
        results = DDGS().text(query, max_results=MAX_RESULTS, backend=BACKENDS)
    except Exception as e:
        print(f"[web_search] failed: {e}")
        return [], str(e) or type(e).__name__
    return [{"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
            for r in results], None


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
