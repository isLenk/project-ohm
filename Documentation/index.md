# Ohm Documentation

Ohm is a virtual assistant that combines speech-to-text, text-to-speech and generative AI into human-like chatbots that hang out in Discord.

## Start here

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Setting Ohm up on a PC and starting him |
| [Architecture](architecture.md) | The services, and what travels between them |
| [How a reply is made](turn.md) | One voice turn, from hearing someone to answering |

## The parts

| Section | What it covers |
|---|---|
| [Interface](Interface/README.md) | The Electron app that runs Ohm |
| [Curator](Curator/README.md) | Turning exported Discord chat into LoRA training data, memories and reflection |
| [Brain](apps/brain/README.md) | Routing background LLM jobs to cloud or local models |
| [Memory service](apps/memory/README.md) | The Postgres + pgvector service on the NAS, and its API |
| [Ohm Lite](apps/lite/README.md) | The slash-command bot that stays up when the PC is off |

## Project

| Page | What it covers |
|---|---|
| [Dev Notes](DevNotes.md) | Troubles and tech debt met during development |
| [Writing these docs](writing-docs.md) | Previewing the site, adding pages and drawing diagrams |
