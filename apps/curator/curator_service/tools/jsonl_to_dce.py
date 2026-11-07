"""Write a messages.jsonl dump out as DiscordChatExporter JSON files, one per channel.

You don't need this to import: the Curator's Import button reads messages.jsonl directly (app/formats.py).
Use it to inspect the conversion or to hand the files to another tool.

    python -m curator_service.tools.jsonl_to_dce <messages.jsonl> [--exports <dir or .json> ...] [--out <dir>]

Channels that already have a real export are skipped; real exports keep fields the JSONL lost.
Author objects (avatars, nicknames, roles) come from those exports, then from speakers.json next to the JSONL.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from curator_service.app import formats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--exports", type=Path, nargs="*", help="DiscordChatExporter files or folders (default: the JSONL's folder)")
    ap.add_argument("--out", type=Path, help="where to write (default: <jsonl folder>/import)")
    args = ap.parse_args(argv)

    files = [f for p in (args.exports or [args.jsonl.parent]) for f in (sorted(p.glob("*.json")) if p.is_dir() else [p])]
    docs, speakers = [], {}
    for f in files:
        kind = formats.kind_of(f)
        if kind == "dce":
            docs.append(json.loads(f.read_text(encoding="utf-8")))
        elif kind == "speakers":
            speakers = formats.load_speakers(f)
    idx = formats.index_dce(docs)
    del docs
    authors = {**{i: formats.author_from_speaker(i, s) for i, s in speakers.items()}, **idx["authors"]}
    print(f"read {len(files)} file(s): {len(idx['channels'])} exported channel(s), {len(authors)} author(s)")

    with args.jsonl.open(encoding="utf-8") as lines:
        out = formats.jsonl_to_dce(lines, authors, idx["guilds"], idx["thread_parent"].get, skip_channels=idx["channels"])
    out_dir = args.out or args.jsonl.parent / "import"
    out_dir.mkdir(parents=True, exist_ok=True)
    for doc in out:
        g, c = doc["guild"], doc["channel"]
        name = " - ".join(p for p in (g["name"], c["category"], c["name"]) if p) + f" [{c['id']}].json"
        path = out_dir / re.sub(r'[<>:"/\\|?*]', "_", name)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path.name}: {doc['messageCount']} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
