---
hide:
  - toc
---

# Architecture

Ohm is a handful of small services. The live reply runs entirely on the PC; anything worth keeping goes to the memory service on the NAS; slower background thinking can go to a cloud model.

<div class="ohm-diagram">
<svg viewBox="0 0 1110 750" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="How the parts of Ohm fit together">
<defs>
<marker id="od-a-live" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="od-m-live"/></marker>
<marker id="od-a-mem" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="od-m-mem"/></marker>
<marker id="od-a-bg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="od-m-bg"/></marker>
<marker id="od-a-ctl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="od-m-ctl"/></marker>
</defs>
<!-- Interface -->
<rect class="od-box" x="30" y="20" width="1040" height="58" rx="8"/>
<text class="od-t" x="48" y="44">Ohm Interface</text>
<text class="od-s" x="48" y="63">Electron app · apps/mainframe · :3001 · starts and stops the Engine and Curator · edits characters, memories and settings</text>
<!-- Discord -->
<rect class="od-box" x="30" y="130" width="160" height="280" rx="8"/>
<text class="od-t" x="46" y="154">Discord</text>
<rect class="od-inner" x="46" y="160" width="128" height="40" rx="6"/>
<text class="od-s" x="58" y="185">Text channel</text>
<rect class="od-inner" x="46" y="290" width="128" height="40" rx="6"/>
<text class="od-s" x="58" y="315">Voice call</text>
<text class="od-l" x="46" y="392">people talking to Ohm</text>
<!-- Engine -->
<rect class="od-box" x="250" y="120" width="400" height="300" rx="8"/>
<text class="od-t" x="268" y="144">Engine</text>
<text class="od-s" x="326" y="144">Python · apps/engine</text>
<rect class="od-inner" x="268" y="158" width="364" height="104" rx="6"/>
<text class="od-t" x="282" y="180">Turn · prompt builder</text>
<text class="od-s" x="282" y="199">history · related memories · notes on people named</text>
<text class="od-s" x="282" y="216">chat brief · mood line · glossary</text>
<text class="od-s" x="282" y="233">web search or screenshot + OCR when asked</text>
<text class="od-s" x="282" y="250">stops at every speaker label</text>
<rect class="od-inner" x="268" y="282" width="150" height="52" rx="6"/>
<text class="od-t" x="282" y="303">STT</text>
<text class="od-s" x="282" y="321">Silero VAD + Parakeet</text>
<rect class="od-inner" x="268" y="352" width="172" height="52" rx="6"/>
<text class="od-t" x="282" y="373">clean_reply</text>
<text class="od-s" x="282" y="391">cut labels, strip tags</text>
<rect class="od-inner" x="450" y="352" width="182" height="52" rx="6"/>
<text class="od-t" x="464" y="373">voice tag filter</text>
<text class="od-s" x="464" y="391">streams, keeps [laugh]</text>
<!-- llama-server -->
<rect class="od-box" x="700" y="236" width="170" height="70" rx="8"/>
<text class="od-t" x="714" y="258">llama-server</text>
<text class="od-s" x="714" y="276">Gemma 4 E4B + LoRA</text>
<text class="od-s" x="714" y="293">vision · :5000 · local</text>
<!-- TTS -->
<rect class="od-box" x="700" y="352" width="170" height="52" rx="8"/>
<text class="od-t" x="714" y="373">TTS API</text>
<text class="od-s" x="714" y="391">Chatterbox · ws :8000</text>
<!-- Memory service -->
<rect class="od-box" x="910" y="120" width="160" height="300" rx="8"/>
<text class="od-t" x="924" y="144">Memory service</text>
<text class="od-s" x="924" y="162">on the NAS · :8100</text>
<text class="od-s" x="924" y="178">FastAPI + pgvector</text>
<rect class="od-inner" x="924" y="192" width="132" height="26" rx="5"/><text class="od-l" x="934" y="209">characters</text>
<rect class="od-inner" x="924" y="224" width="132" height="26" rx="5"/><text class="od-l" x="934" y="241">people + notes</text>
<rect class="od-inner" x="924" y="256" width="132" height="26" rx="5"/><text class="od-l" x="934" y="273">memories</text>
<rect class="od-inner" x="924" y="288" width="132" height="26" rx="5"/><text class="od-l" x="934" y="305">traits + relationships</text>
<rect class="od-inner" x="924" y="320" width="132" height="26" rx="5"/><text class="od-l" x="934" y="337">reflection queue</text>
<text class="od-l" x="924" y="370">queues a job after</text>
<text class="od-l" x="924" y="385">40+ new lines and</text>
<text class="od-l" x="924" y="400">30 quiet minutes</text>
<!-- Cloud brains -->
<rect class="od-box" x="30" y="500" width="160" height="100" rx="8"/>
<text class="od-t" x="46" y="524">Cloud brains</text>
<text class="od-s" x="46" y="544">Codex CLI (default)</text>
<text class="od-s" x="46" y="561">Claude CLI · DeepSeek</text>
<text class="od-s" x="46" y="578">OpenRouter</text>
<!-- Brain -->
<rect class="od-box" x="250" y="500" width="220" height="74" rx="8"/>
<text class="od-t" x="268" y="524">Brain</text>
<text class="od-s" x="314" y="524">apps/brain</text>
<text class="od-s" x="268" y="544">routes LLM jobs by role,</text>
<text class="od-s" x="268" y="561">falls back down the list</text>
<!-- Curator -->
<rect class="od-box" x="520" y="590" width="350" height="100" rx="8"/>
<text class="od-t" x="536" y="613">Curator</text>
<text class="od-s" x="596" y="613">FastAPI · apps/curator · :8200</text>
<rect class="od-inner" x="536" y="626" width="155" height="50" rx="6"/>
<text class="od-s" x="548" y="646">reflection worker</text>
<text class="od-l" x="548" y="663">polls the NAS</text>
<rect class="od-inner" x="701" y="626" width="155" height="50" rx="6"/>
<text class="od-s" x="713" y="646">imports · linking</text>
<text class="od-l" x="713" y="663">LoRA training</text>
<!-- Live reply -->
<path class="od-live" d="M190 180 H268" marker-end="url(#od-a-live)"/>
<text class="od-l" x="204" y="173">text in</text>
<path class="od-live" d="M190 310 H268" marker-end="url(#od-a-live)"/>
<text class="od-l" x="228" y="303">audio</text>
<path class="od-live" d="M338 282 V262" marker-end="url(#od-a-live)"/>
<path class="od-live" d="M632 250 H700" marker-start="url(#od-a-live)" marker-end="url(#od-a-live)"/>
<text class="od-l" x="656" y="228">prompt / tokens</text>
<path class="od-live" d="M430 262 V352" marker-end="url(#od-a-live)"/>
<text class="od-l" x="437" y="312">text</text>
<path class="od-live" d="M541 262 V352" marker-end="url(#od-a-live)"/>
<text class="od-l" x="548" y="312">voice</text>
<path class="od-live" d="M268 378 H222 V200 H190" marker-end="url(#od-a-live)"/>
<text class="od-l" x="228" y="216">reply</text>
<path class="od-live" d="M632 378 H700" marker-end="url(#od-a-live)"/>
<path class="od-live" d="M785 404 V446 H110 V410" marker-end="url(#od-a-live)"/>
<text class="od-l" x="384" y="440">audio back through DiscordVoice · barge-in pauses it</text>
<!-- Memory -->
<path class="od-mem" d="M650 150 H910" marker-start="url(#od-a-mem)" marker-end="url(#od-a-mem)"/>
<text class="od-l" x="664" y="142">memories · people · character · messages</text>
<path class="od-mem" d="M990 78 V120" marker-start="url(#od-a-mem)" marker-end="url(#od-a-mem)"/>
<text class="od-l" x="998" y="104">CRUD</text>
<!-- Background thinking -->
<path class="od-bg" d="M340 420 V500" marker-end="url(#od-a-bg)"/>
<text class="od-l" x="348" y="482">brief every 12 lines or 4 min</text>
<path class="od-bg" d="M250 537 H190" marker-end="url(#od-a-bg)"/>
<path class="od-bg" d="M470 520 H672 V290 H700" marker-end="url(#od-a-bg)"/>
<text class="od-l" x="500" y="512">local fallback</text>
<path class="od-bg" d="M520 640 H360 V574" marker-end="url(#od-a-bg)"/>
<text class="od-l" x="372" y="632">reflect · judge · curate</text>
<path class="od-bg" d="M870 640 H990 V420" marker-start="url(#od-a-bg)" marker-end="url(#od-a-bg)"/>
<text class="od-l" x="880" y="659">claim job · store result</text>
<!-- Control -->
<path class="od-ctl" d="M450 78 V120" marker-end="url(#od-a-ctl)"/>
<text class="od-l" x="458" y="104">start / stop</text>
<path class="od-ctl" d="M1070 49 H1090 V672 H870" marker-end="url(#od-a-ctl)"/>
<!-- Legend -->
<line class="od-live" x1="46" y1="726" x2="86" y2="726"/><text class="od-l" x="94" y="730">live reply, stays on the PC</text>
<line class="od-mem" x1="290" y1="726" x2="330" y2="726"/><text class="od-l" x="338" y="730">memory, to and from the NAS</text>
<line class="od-bg" x1="545" y1="726" x2="585" y2="726"/><text class="od-l" x="593" y="730">background thinking, may use a cloud brain</text>
<line class="od-ctl" x1="870" y1="726" x2="910" y2="726"/><text class="od-l" x="918" y="730">started by the interface</text>
</svg>
</div>

For what happens inside the Engine during one reply, see [How a reply is made](turn.md).

## The parts

| Part | Where | What it does |
|---|---|---|
| **Ohm Interface** | `apps/mainframe`, Electron + React | The app you use: Dashboard, Characters, Curator, Modules, Memories, Filters, Settings. Starts and stops the Engine and the Curator. See [Interface](Interface/README.md) |
| **Engine** | `apps/engine`, Python | The assistant itself: the Discord bot, speech-to-text, the prompt builder and the modules (mood, vision, memory, TTS client) |
| **llama-server** | [llama.cpp](https://github.com/ggml-org/llama.cpp), port 5000 | Runs Gemma 4 E4B with the character's LoRA. Every live reply comes from here |
| **TTS API** | `apps/tts`, port 8000 | Chatterbox Turbo through RealtimeTTS. Takes text over a websocket and streams audio back |
| **Memory service** | `apps/memory`, on the NAS, port 8100 | Postgres + pgvector behind a FastAPI app. Holds characters, people, memories, traits, relationships, the message log and the reflection queue. See [Memory service](apps/memory/README.md) |
| **Brain** | `apps/brain`, a Python package | Sends one-shot LLM jobs to whichever backend their role is set to, and falls back down the list. See [Brain](apps/brain/README.md) |
| **Curator** | `apps/curator`, port 8200 | Turns Discord exports into LoRA training data, and runs the reflection worker. See [Curator](Curator/README.md) |
| **Ohm Lite** | `apps/lite`, on the NAS | The slash-command part of Ohm that stays up when the PC is off. Not in the diagram. See [Ohm Lite](apps/lite/README.md) |

## Four kinds of traffic

**Live reply (blue).** Text or voice comes in from Discord, the Engine builds a prompt, llama-server streams tokens back, and the reply goes out as a message or as speech through the TTS API. Nothing on this path leaves the PC, and nothing on it waits for a cloud model.

**Memory (green).** The Engine reads memories, notes on people and the character card from the NAS before it replies, and logs every line afterwards. The Interface edits the same data.

**Background thinking (amber).** Jobs that can take a few seconds go through the Brain: the running brief of a conversation (every 12 user lines, or 4 minutes after the first one), and the Curator's reflection, judging and curation. Each role has an ordered list of backends, so a job set to Codex still gets an answer from the local model when Codex is unavailable.

**Control (red).** The Interface starts and stops the Engine and the Curator. `npm run dev:all` starts llama-server and the TTS API alongside it.

## What leaves the PC

Live replies, voice audio and screenshots never do. Chat text only leaves for the roles pointed at a cloud backend in Settings › Integrations. `BRAIN_LOCAL_ONLY=1` keeps every role on the local models.
