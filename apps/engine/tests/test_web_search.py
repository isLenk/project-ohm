"""utils/WebSearch.py"""
import threading
import time

import pytest

from utils import WebSearch

RESULT = [{"title": "T", "body": "B", "href": "H"}]


class SlowDDGS:
    calls = []     # (query, backend)
    delay = {}     # backend -> seconds (default 0.2)
    empty = set()  # backends that find nothing

    def __init__(self, **kw):
        pass

    def text(self, query, backend=None, **kw):
        SlowDDGS.calls.append((query, backend))
        time.sleep(SlowDDGS.delay.get(backend, 0.2))
        if "nothing" in query or backend in SlowDDGS.empty:
            raise RuntimeError("No results found.")
        return [{**RESULT[0], "title": f"T{'' if backend == WebSearch.BACKENDS[0] else ' ' + backend}"}] \
            if SlowDDGS.delay else RESULT


@pytest.fixture
def ddgs(monkeypatch):
    SlowDDGS.calls, SlowDDGS.delay, SlowDDGS.empty = [], {}, set()
    monkeypatch.setattr(WebSearch, "DDGS", SlowDDGS)
    monkeypatch.setattr(WebSearch, "_recent", {})
    return SlowDDGS


def searched(ddgs) -> list[str]:
    return list(dict.fromkeys(q for q, _ in ddgs.calls))


def test_a_restarted_turn_joins_the_search_already_running(ddgs):
    answers = []
    threads = [threading.Thread(target=lambda q: answers.append(WebSearch.search(q)), args=(q,))
               for q in ["Canadian equivalent of the FBI", "canadian  equivalent of the fbi", "Canadian equivalent of the FBI"]]
    for t in threads:
        t.start()
        time.sleep(0.02)  # first one under way
    for t in threads:
        t.join()
    assert searched(ddgs) == ["Canadian equivalent of the FBI"]
    assert answers == [(RESULT, None)] * 3


def test_a_failure_is_shared_too_and_a_new_question_searches_again(ddgs):
    assert WebSearch.search("nothing here") == ([], "No results found.")
    assert WebSearch.search("nothing here") == ([], "No results found.")
    assert WebSearch.search("something else") == (RESULT, None)
    assert searched(ddgs) == ["nothing here", "something else"]


def test_an_old_answer_is_asked_again(ddgs, monkeypatch):
    WebSearch.search("what day is it")
    monkeypatch.setattr(WebSearch, "REUSE_S", 0)
    WebSearch.search("what day is it")
    assert [q for q, b in ddgs.calls if b == WebSearch.BACKENDS[0]] == ["what day is it"] * 2


def test_every_engine_is_asked_and_the_first_with_results_wins(ddgs):
    first, *rest = WebSearch.BACKENDS
    ddgs.empty = {first}                       # fastest finds nothing
    ddgs.delay = {first: 0.01, rest[0]: 0.05, rest[1]: 0.6, rest[2]: 0.6}
    started = time.perf_counter()
    results, error = WebSearch.search("who won last night")
    assert error is None and results[0]["title"] == f"T {rest[0]}"
    assert time.perf_counter() - started < 0.4
    assert sorted(b for _, b in ddgs.calls) == sorted(WebSearch.BACKENDS)


def test_gives_up_after_the_timeout(ddgs, monkeypatch):
    monkeypatch.setattr(WebSearch, "TIMEOUT_S", 0.1)
    ddgs.delay = {b: 0.5 for b in WebSearch.BACKENDS}
    started = time.perf_counter()
    assert WebSearch.search("slow question") == ([], "No answer within 0.1s.")
    assert time.perf_counter() - started < 0.3


def test_answered_once_the_answer_is_in(ddgs, monkeypatch):
    assert not WebSearch.answered("who won last night")
    WebSearch.search("who won last night")
    assert WebSearch.answered("Who won  last night")
    monkeypatch.setattr(WebSearch, "REUSE_S", 0)
    assert not WebSearch.answered("who won last night")  # too old
