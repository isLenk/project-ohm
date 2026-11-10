"""utils/trace.py"""
import pytest

import utils.latency as latency
import utils.trace as trace


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    posted = []
    monkeypatch.setattr(trace, "_post", lambda url, json, timeout: posted.append((url, json)))
    monkeypatch.setattr(trace, "COALESCE_S", 0.01)
    trace.reset()
    yield posted
    trace.reset()


def test_a_voice_turn_goes_through_its_stages():
    trace.begin("voice", {"Lance": "what's up"}, "ohm", note="1 speaker")
    trace.stage("memory", "running", "searching memories")
    trace.stage("memory", "done", "2 hits · 40 ms", log="likes ramen")
    trace.stage("search", "skipped", "no query")
    trace.end("done")
    t = trace.current()
    assert t["kind"] == "voice" and t["status"] == "done" and t["speakers"] == ["Lance"]
    assert t["said"] == "Lance: what's up"
    assert t["stages"]["discord"]["status"] == "done"
    assert t["stages"]["stt"]["logs"][0]["text"] == "Lance: what's up"
    assert t["stages"]["queue"]["note"] == "1 speaker"
    assert t["stages"]["memory"] == {
        **t["stages"]["memory"], "status": "done", "note": "2 hits · 40 ms",
    }
    assert t["stages"]["memory"]["logs"][0]["text"] == "likes ramen"
    assert t["stages"]["memory"]["ended_at"] >= t["stages"]["memory"]["started_at"]
    assert t["stages"]["search"]["status"] == "skipped"


def test_hearing_opens_a_listening_turn_that_begin_takes_over():
    first = trace.hearing("Lance", "ohm")
    trace.hearing("Kai", "ohm")
    t = trace.current()
    assert t["status"] == "hearing" and t["speakers"] == ["Lance", "Kai"]
    assert t["stages"]["stt"]["status"] == "running"
    assert trace.begin("voice", {"Lance": "hey", "Kai": "yo"}, "ohm") == first  # the same turn, now replying
    t = trace.current()
    assert t["status"] == "running" and t["stages"]["stt"]["status"] == "done"


def test_a_cut_in_while_replying_lands_on_that_turn_not_a_new_one():
    tid = trace.begin("voice", {"Lance": "tell me a story"}, "ohm")
    trace.stage("playback", "running", "playing")
    assert trace.hearing("Kai", "ohm") == tid
    assert trace.current()["stages"]["playback"]["logs"][-1]["text"] == "Kai is cutting in"


def test_ending_a_turn_only_counts_once_and_settles_what_was_running():
    trace.begin("voice", {"Lance": "hi"}, "ohm")
    trace.stage("llm", "running")
    trace.stage("tts", "running")
    trace.end("cut", "talked over")
    trace.end("done")
    t = trace.current()
    assert t["status"] == "cut" and t["end_note"] == "talked over"
    assert t["stages"]["llm"]["status"] == "skipped" and t["stages"]["tts"]["status"] == "skipped"
    # Late updates still land by id
    trace.stage("playback", "done", "played", turn=t["id"])
    assert trace.current()["stages"]["playback"]["status"] == "done"


def test_stages_can_be_addressed_by_turn_id_after_the_next_one_begins():
    old = trace.begin("voice", {"Lance": "one"}, "ohm")
    trace.end("done")
    new = trace.begin("voice", {"Lance": "two"}, "ohm")
    trace.stage("commit", "done", "saved", turn=old)
    assert trace.current_id() == new
    assert "commit" not in trace.current()["stages"]
    assert trace._turns[old]["stages"]["commit"]["status"] == "done"


def test_latency_marks_go_into_the_turn_and_the_right_stage_log():
    trace.begin("voice", {"Lance": "hi"}, "ohm")
    latency.start(0.0)
    latency._t0 = 0.0
    import time
    latency._t0 = time.perf_counter() - 0.5
    latency.mark("pre_checks")
    t = trace.current()
    assert 400 <= t["marks"]["pre_checks"] <= 2000
    assert t["stages"]["checks"]["logs"][0]["text"].startswith("pre_checks +")


def test_changes_are_posted_as_one_turn_coalesced(fresh):
    trace.begin("voice", {"Lance": "hi"}, "ohm")
    for i in range(20):
        trace.reply(f"word {i}")
    trace.end("done")
    assert trace.flush(2.0)
    urls = {url for url, _ in fresh}
    assert urls == {f"{trace.CONTROLLER_URL}/api/turns"}
    assert 1 <= len(fresh) < 20
    assert fresh[-1][1]["status"] == "done" and fresh[-1][1]["reply"] == "word 19"


def test_the_app_being_closed_never_reaches_the_engine(monkeypatch):
    def down(url, json, timeout):
        raise ConnectionError("refused")
    monkeypatch.setattr(trace, "_post", down)
    trace.begin("voice", {"Lance": "hi"}, "ohm")
    assert trace.flush(2.0)
    assert trace._down_until > 0
    trace.stage("memory", "done")
    assert trace.current()["stages"]["memory"]["status"] == "done"


def test_nothing_happens_without_a_turn():
    trace.stage("memory", "done")
    trace.log("memory", "x")
    trace.mark("pre_checks", 10)
    trace.end("done")
    assert trace.current() is None
