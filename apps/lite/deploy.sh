#!/usr/bin/env bash
# Copy lite + brain to the NAS and rebuild. Keeps the NAS's .env and data/
set -euo pipefail

DEST=${LITE_DEST:-/volume2/docker/ohm_lite}
cd "$(dirname "$0")/.."   # apps/

# A syntax error would crash-loop the bot
python -c "import ast, glob, sys; [ast.parse(open(f, encoding=\"utf-8\").read(), f) for f in glob.glob(\"lite/*.py\")]" \
    || { echo "Not deployed: fix the syntax error above."; exit 1; }

# ? tar over ssh; scp fails on UGOS
tar -c --exclude=__pycache__ --exclude=data --exclude=.env lite brain/brain \
    | ssh nas "mkdir -p $DEST && tar -x -C $DEST"
ssh nas "cd $DEST/lite && docker compose up -d --build && docker compose logs --tail 20 lite"
