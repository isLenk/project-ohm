FROM python:3.12-slim

# Codex CLI (brain backend) needs Node
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @openai/codex@0.163.0 \
    && npm cache clean --force

# Mirror the repo; brain finds .env three folders up
WORKDIR /srv/ohm/apps/lite
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/ohm/apps/brain \
    CODEX_HOME=/data/codex \
    CURATOR_DATA_DIR=/data \
    LITE_DATA=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=brain brain /srv/ohm/apps/brain/brain
COPY *.py ./

CMD ["python", "bot.py"]
