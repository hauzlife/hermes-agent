# Grok Bot ephemeral agents (1 agent per Hermes job)

On the grokbot path, each kanban/oneshot job mints its own Temporal agent,
sends via `SendGrokBotUserMessage` to **that** `agentId`, then deletes it in a
`finally` path. Artur's main agent `67f75405-…` is never the target for these jobs.

## How to enable (do **not** flip global fleet unless asked)

Global `model.provider` stays as-is. Opt in per run:

1. **Kanban worker already on grokbot**  
   Workers export `HERMES_KANBAN_TASK` → ephemeral is automatic when
   `api_mode`/`provider` is grokbot.

2. **One-shot / ad-hoc**  
   ```bash
   GROKBOT_EPHEMERAL_AGENT=1 hermes chat --provider grokbot -q "…" --oneshot
   # or any worker that calls agent.grokbot.client.infer
   ```

3. **Pin a long-lived agent (disables ephemeral)**  
   ```bash
   GROKBOT_AGENT_ID=<uuid>   # skips create/delete
   ```

4. **Force off**  
   ```bash
   GROKBOT_EPHEMERAL_AGENT=0
   ```

Auth: existing `~/.grokbot/session.json` (no browser). `machineId` from session
(`6806ab60-b2ae-401a-8b62-d6b0b5728c49`).

## API

- `CreateGrokBotTemporalAgent` → `SendGrokBotUserMessage` → `DeleteGrokBotAgent`
- Helpers: `create_agent`, `delete_agent`, `want_ephemeral`, `infer(..., ephemeral=True)`
