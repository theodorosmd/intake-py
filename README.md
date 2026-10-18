# intake-py: AI phone intake agent for a personal-injury law firm

An English-speaking phone agent that answers a law firm's intake line, collects the caller's case details,
saves the case file and books a consultation. It runs on a real US phone number and was built as a
reference deployment for a fictional New York personal-injury firm. It has been tested with real test
calls and a 50-scenario replay suite, not with live clients.

## What it does on a call

1. Answers the call (Telnyx Call Control) and announces that the caller is talking to an AI.
2. Streams the caller's audio to speech-to-text (Deepgram nova-3 through Telnyx) and detects the end of each sentence.
3. Sends the conversation to an LLM through a LiteLLM gateway (`gpt-4.1-mini`, with a fallback provider), streamed sentence by sentence.
4. Speaks each sentence as soon as it is ready (streaming TTS, `gpt-4o-mini-tts`), and stops talking when the caller interrupts.
5. Calls two tools on the gateway: `save_intake` (case file) and `book_consultation` (weekday slots at 10 AM, 2 PM, 4 PM New York time; booking again moves the slot).

```
caller ─ phone ─ Telnyx ─ media WebSocket ─┬─ STT (Deepgram via Telnyx)
                                          ├─ conversation.py  turns, interruptions, echo filter, latency log
                                          ├─ agent_vocal.py   LLM via LiteLLM, tools, guardrails, TTS
                                          └─ main.py          /intake, /consultations, /health, Telnyx webhooks
```

## Guardrails

| Risk | Guardrail | Where |
|---|---|---|
| Announcing a booking that was not made | A sentence announcing a booking is held back unless a tool call backs it in the same turn; the model is asked to call the tool | `phrases_llm` in `agent_vocal.py` |
| Wrong dates ("45 days ago") | The model passes `accident_days_ago`; code computes the date | `normaliser_arguments` |
| Medical emergency in progress | Tell the caller to call 911 before any intake question | instructions in `consignes()` |
| Legal or medical advice, promises | Forbidden in the instructions, checked by the replay suite | `consignes()`, `evals/` |
| Misheard names and numbers | Full name and number read back before saving; unclear parts spelled | `consignes()` |
| Lost leads | Case file saved as soon as the five mandatory fields are known, even without a consultation | `consignes()` |
| Personal data in logs | Names, phone numbers, spelled names and e-mails masked in every log line | `confidentialite.py` |

## Measured results

| Measure | Result | How |
|---|---|---|
| Replay suite, last full run | 48/50 scenarios | `python -m evals.rejeu` (Oct 18, 2026) |
| Replay suite, scenarios fixed after that run | 18/18 (6 scenarios × 3 runs) | `--seulement … --repetitions 3` |
| Time to first sound (median) | 3.6 s → 2.64 s → 2.54 s | Per-turn log on real test calls: before streaming, after streaming and model change, latest 14-turn call |
| Time to first sound on the cluster | median 3.0 s on a first call, 2.2 s total median on a later one | Same per-turn log, k3s in Helsinki (the Telnyx edge and the model are far from it) |
| Model time to first token | 1.19 s (`gpt-4.1-mini`) vs 1.93 s (previous model) | `litellm/mesure_ttft.py` in the gateway repo, 8 runs each |

The appendix of the deployment playbook lists every defect found and how it was fixed.

## Run it

Requirements: Python 3.14, a Telnyx account with a number, an OpenAI key, and the LiteLLM gateway
(`~/dev/pi-intake`, Docker) if `LLM_API_BASE` is set.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m fastapi dev main.py          # gateway on :8000
cloudflared tunnel --url http://localhost:8000   # public URL for Telnyx
```

| Variable | Purpose |
|---|---|
| `TELNYX_API_KEY` | Call Control and speech-to-text |
| `PUBLIC_HOST` | Public host of the tunnel, without `https://` |
| `LLM_MODEL` | Model name, e.g. `openai/llm-agent` (a name known to the gateway) |
| `LLM_API_BASE`, `LLM_API_KEY` | LiteLLM gateway URL and virtual key; without them the model is called directly |
| `OPENAI_API_KEY` | Direct calls and TTS |
| `TTS_MODEL`, `TTS_VOICE` | Optional, default `openai/gpt-4o-mini-tts`, `alloy` |
| `GATEWAY_URL` | Where tools are sent, default `http://127.0.0.1:8000` |
| `LOGS_EN_CLAIR=1` | Disable log masking for local debugging only |
| `TELNYX_PUBLIC_KEY` | Telnyx public key used to verify webhook signatures |
| `MCP_TOKEN` | Bearer token for the MCP endpoint; without it `/mcp` answers 503 |
| `DATABASE_URL` | Postgres for cases, bookings and call summaries; without it they are kept in memory (tests, replay) |

## Deployment (Kubernetes)

Runs on a single-node k3s cluster (Hetzner, Helsinki) behind Traefik, with a fixed domain and a
Let's Encrypt certificate issued by cert-manager. The manifests and the Helm chart live in the `pi-intake` repo.

```
Telnyx ─ https://intake.theappstudio.co/telnyx/voice ─ Traefik ─ intake-gateway (this repo)
                                                                   ├─ litellm ─ litellm-db (Postgres)
                                                                   └─ summary-worker
```

- **Images:** built for `linux/amd64` by CI, pushed to a private GHCR registry, tagged with the commit (`sha-xxxxxxx`), never `latest`.
- **Secrets:** created from local `.env` files by a script; values are never in git or in logs. The LiteLLM virtual key is generated inside the cluster.
- **Resources and probes:** memory requests and limits set from `kubectl top` measurements (the OOMKilled behaviour was reproduced on purpose); startup, readiness and liveness probes on every pod.
- **Data:** cases, bookings and call summaries live in their own `intake` database and user, inside the cluster's Postgres instance (a Job creates them, the gateway waits for it with an init container). The booking slot is the primary key, so Postgres, not the code, prevents double bookings. A CronJob runs `pg_dump` every night and keeps 7 days on a cluster volume.
- **Rollout:** the gateway runs one replica with a `Recreate` strategy, because the state of a call in progress still lives in memory; `helm rollback` restores the previous revision.
- **Exposure:** the ingress publishes only `/telnyx` and `/mcp`; a cloud firewall allows 22, 80 and 443; the Kubernetes API is only reachable through an SSH tunnel.
- **CI (`.github/workflows/ci.yml`):** on every push, `ruff` and the unit tests, then the image build and push.
- **CD (`deploy.yml` in `pi-intake`):** a manual workflow takes an image tag and runs `helm upgrade`. GitHub holds a dedicated SSH key that can only open a tunnel to the API, and a service account limited to the `intake` namespace.

## Firm knowledge (RAG)

Callers ask about the firm itself: what it costs, whether someone speaks Spanish, what to bring.
The agent answers those questions from the firm's own information, not from the model's memory.

- **Source:** Markdown files in `connaissances/` (fictional demo content), one chunk per `##` section.
  They state facts about the firm and never give legal advice: legal questions have their own
  section, which sends the caller to the attorney.
- **Index:** at startup the gateway embeds the sections with `text-embedding-3-small` and stores them
  in Postgres with pgvector (`knowledge_chunks`, HNSW index, cosine distance). Indexing is incremental:
  a hash per section, so only new or edited sections are embedded again, and removed ones are deleted.
- **Retrieval:** the agent calls the `search_firm_info` tool; the gateway embeds the question and returns
  the closest sections with their source and score. Below a similarity threshold (`RAG_SEUIL`, 0.22 by
  default, set from the calibration run: on-topic questions scored 0.26 or more, off-topic ones 0.19 or less) nothing is returned and the agent says it does not have that information.
- **Checks:** 6 replay scenarios (`connaissances-*`) verify that the tool was called, that the expected
  section came back, and that the agent neither invents a fact (free parking, a Brooklyn office) nor
  answers a legal question (filing deadline). `python -m evals.rag_calibrage` shows the real scores of
  on-topic and off-topic questions to set the threshold.

## MCP server for the firm

The gateway also serves an MCP endpoint at `https://intake.theappstudio.co/mcp`, so the firm can
work with the intake desk from Claude, Cursor or any MCP client: "which urgent cases came in today?",
"book John Miller on Thursday afternoon".

| Tool | Does |
|---|---|
| `list_intake_cases` | Cases saved by the phone agent, most recent first, with an `urgent_only` filter |
| `get_intake_case` | One case by id |
| `list_call_summaries` | Post-call summaries from the summary worker |
| `list_consultations` | Booked consultations, in order |
| `find_next_slot` | First free slot on or after a date (read-only) |
| `search_firm_knowledge` | The same search as the phone agent, with sources |
| `book_consultation` | Books it; the same phone number is moved rather than double-booked |

It reads and writes the same Postgres data as the phone agent (`mcp_outils.py`, official Python SDK,
stateless Streamable HTTP with JSON responses). Every request needs `Authorization: Bearer <MCP_TOKEN>`;
without a configured token the endpoint stays closed.

```bash
claude mcp add --transport http pi-intake https://intake.theappstudio.co/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"
```

## Tests and replay

```bash
python -m pytest -q          # unit tests, no network (data kept in memory)
DATABASE_URL=postgresql://... TEST_DATABASE_URL=postgresql://... python -m pytest -q   # same suite on Postgres, as CI does
python -m ruff check .
python -m evals.rejeu        # 50 scenarios, about 20 minutes, needs the LLM gateway
python -m evals.rejeu --seulement reservation-changement --repetitions 3
```

The replay suite (`evals/`) plays each scenario in text: an LLM plays the caller from the scenario sheet,
the real agent answers with its real tools (on an in-memory copy of the gateway), then deterministic
checks verify the case file, the final slot and every booking announcement, and an LLM judge flags
advice or promises with an exact quote that is checked against the transcript. Set `JUGE_MODEL` to run
the judge on a stronger model. Reports with full transcripts go to `evals/rapports/`.

## Known gaps

- One node, one gateway replica: no high availability, and no autoscaling until call state is stored outside the process. The node is shared with another workload and its memory is tight.
- Hosted in Helsinki: fine for tests, but a US deployment should run close to Telnyx and the model for latency.
- MCP access uses one shared token, not per-user OAuth.
- Embeddings go straight to OpenAI (like text-to-speech), not through the LiteLLM proxy, so they are not
  counted in the proxy's budgets. Telnyx also offers embeddings and similarity search over its storage
  buckets; pgvector was chosen to keep retrieval inside the cluster, next to the data.
- Backups stay on the same server as the database: they protect against mistakes, not against losing the server. No retention or deletion policy for callers' personal data yet.
- Telnyx webhook signatures (Ed25519) are verified on `/telnyx/voice` and `/telnyx/webhook`; the media WebSocket `/telnyx/media` is not signed (a secret token in the stream URL would close that).
- At the end of a call the transcript goes to the summary worker, but the result is only written to the log: it is not yet stored or sent to a CRM.
- Audio recordings are written unmasked inside the pod and disappear on restart; retention and masking are not implemented.
- The LiteLLM image is not pinned by digest yet.
- No transfer to a human yet in this pipeline (the vendor-hosted assistant version had one).
- Existing clients, out-of-scope matters and cancellations get an honest answer but no hand-off: decisions for the firm.
- Code comments and log messages are in French.
