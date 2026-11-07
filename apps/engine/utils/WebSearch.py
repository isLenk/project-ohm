"""Web search for Ohm. Free search through the `ddgs` package (no API key)."""
from ddgs import DDGS

# ? DuckDuckGo/Brave block this machine; Bing then Google are the ones that answer
BACKENDS = "bing,google"
MAX_RESULTS = 3


def web_search(query: str) -> str | None:
    """Search the web and return the top results as plain text, or None if nothing came back."""
    print(f"[web_search] {query}")
    try:
        results = DDGS().text(query, max_results=MAX_RESULTS, backend=BACKENDS)
    except Exception as e:
        print(f"[web_search] failed: {e}")
        return None
    # No URLs: in voice they'd get read out loud
    return "\n\n".join(f"{r['title']}\n{r['body']}" for r in results) or None
