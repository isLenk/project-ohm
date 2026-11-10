"""Live module toggling (controller/modules.py, modules/vision/watch.py)"""
import asyncio
import json
import os
import time

from controller import modules
from modules.vision import watch


def settings(path, enabled="", disabled=""):
    path.write_text(json.dumps({"OHM_ENABLED_MODULES": enabled, "OHM_DISABLED_MODULES": disabled}))
    os.utime(path, (time.time() + 1, time.time() + 1))  # new mtime within the same second


def test_vision_follows_the_settings_file_without_a_restart(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setenv("OHM_SETTINGS_FILE", str(f))
    monkeypatch.setattr(modules, "RECHECK_S", 0)
    monkeypatch.setattr(modules, "_live", {"checked": 0.0, "mtime": None, "lists": None})
    monkeypatch.setattr(modules, "ENABLED", set())
    monkeypatch.setattr(modules, "DISABLED", {"stt"})
    settings(f, enabled="vision")
    assert modules.enabled("vision")
    settings(f, enabled="")
    assert not modules.enabled("vision")
    # non-live modules keep the startup lists
    settings(f, enabled="", disabled="")
    assert not modules.enabled("stt")


def test_no_settings_file_means_the_startup_lists(monkeypatch):
    monkeypatch.delenv("OHM_SETTINGS_FILE", raising=False)
    monkeypatch.setattr(modules, "ENABLED", {"vision"})
    assert modules.enabled("vision")


def test_watcher_waits_while_off_and_looks_once_on(monkeypatch):
    state = {"on": False, "grabs": 0, "ticks": 0}

    def on():
        state["ticks"] += 1
        if state["ticks"] == 3:
            state["on"] = True
        return state["on"]

    def grab():
        state["grabs"] += 1
        return object()

    class VC:
        def is_connected(self):
            return True

    voice = type("Voice", (), {"vc": VC()})()
    monkeypatch.setattr(watch, "WATCH_EVERY_S", 0)
    monkeypatch.setattr(watch.screen, "on", on)
    monkeypatch.setattr(watch, "grab_thumbnail", grab)
    monkeypatch.setattr(watch, "difference", lambda a, b: 0)  # never speaks

    async def run():
        task = asyncio.create_task(watch.watch_worker(voice))
        while state["ticks"] < 6:
            await asyncio.sleep(0)
        task.cancel()

    asyncio.run(run())
    assert state["grabs"] >= 2
