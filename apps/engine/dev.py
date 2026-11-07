"""
Dev runner for the Discord bot. Run `python dev.py` instead of `python main.py`.

Loads the STT models once, then restarts only the bot whenever a .py file it uses
changes. The bot's code is re-imported from scratch, but Parakeet stays in memory.
"""
import asyncio
import os
import sys
import traceback

import discord
from dotenv import load_dotenv
from watchfiles import awatch, PythonFilter

ROOT = os.path.dirname(os.path.abspath(__file__))
WATCH_PATHS = ["main.py", "pyhandler.py", "modules", "controller", "utils", "config"]

# Modules re-imported on every reload
RELOAD_PREFIXES = ("main", "pyhandler", "modules", "controller", "utils", "config")
# Loaded once and kept, changes here need a full restart
KEEP = {"modules", "modules.stt", "modules.stt.parakeet"}
KEEP_PATHS = [os.path.normcase(os.path.join(ROOT, "modules", "stt", "parakeet.py"))]

load_dotenv()


def purge_modules():
    """Drop the bot's modules from the import cache so the next import reads them fresh"""
    for name in list(sys.modules):
        if name in KEEP:
            continue
        if any(name == p or name.startswith(p + ".") for p in RELOAD_PREFIXES):
            del sys.modules[name]


async def start_bot(token):
    """Import the bot fresh and start it. Returns (client, task) or (None, None) on failure"""
    purge_modules()
    try:
        import main as app
        from modules.discord.DiscordClient import DiscordClient
        client = DiscordClient.initialize(app.build_model())
    except Exception:
        traceback.print_exc()
        print("[dev] Bot failed to load, fix the error and save to retry")
        return None, None

    task = asyncio.create_task(client.start(token), name="discord-bot")

    def on_done(t):
        if not t.cancelled() and t.exception():
            print("[dev] Bot crashed, save a file to restart")
            traceback.print_exception(t.exception())
    task.add_done_callback(on_done)
    return client, task


async def stop_bot(client, task, keep_tasks):
    if client is not None:
        try:
            # ? close() also disconnects voice, which cleans up the STT sink
            await asyncio.wait_for(client.close(), timeout=10)
        except Exception as e:
            print(f"[dev] close() failed: {e!r}")

    # ? Cancel anything the bot left running (voice workers etc.) so it doesn't leak into the next one
    leftover = [t for t in asyncio.all_tasks() if t not in keep_tasks]
    for t in leftover:
        t.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


async def run():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN is not set")

    discord.utils.setup_logging()

    print("[dev] Loading STT models (once)...")
    from modules.stt.parakeet import get_models
    await asyncio.to_thread(get_models)

    keep_tasks = {asyncio.current_task()}
    watcher = awatch(*[os.path.join(ROOT, p) for p in WATCH_PATHS], watch_filter=PythonFilter())

    while True:
        client, task = await start_bot(token)

        changes = await anext(watcher)
        changed = sorted({os.path.relpath(path, ROOT) for _, path in changes})
        print(f"\n[dev] Changed: {', '.join(changed)} - reloading bot...")
        if any(os.path.normcase(path) in KEEP_PATHS for _, path in changes):
            print("[dev] Note: modules/stt/parakeet.py isn't hot reloaded, restart dev.py to apply it")

        await stop_bot(client, task, keep_tasks)


if __name__ == "__main__":
    os.chdir(ROOT)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
