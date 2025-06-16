# DEVNOTES
Contained in this file is all of the troubles and start of tech debt that I've come across during development. Events prior to June 15 2025 have been loosely documented as that day was when this file was created.

### June 15 2025
- Due to time constraints, I've fixed character in `/api/:character/memories` to be Ohm. In other words, all characters share the same set of memories.
- For two reasons, namely time and efficiency, memory retrieval/generation is done in two parts. The python end (which I'd already implemented LLM handling in) will first send a memory retrieval request, then 
- Due to semantic search 