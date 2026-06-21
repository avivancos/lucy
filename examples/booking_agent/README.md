# Booking agent (reference example)

A minimal booking agent built **only on public Lucy APIs** - the open,
sanitized counterpart to product verticals (which live in their own private
repos, e.g. Pili). It shows the supported shape of a booking flow without any
vertical/product code:

- a voice turn on the local simulators (`VoiceAgent` + `user_audio`/`synthesize`),
- an MCP-driven `crm.upsert_lead` and `calendar.hold_slot` via `McpClient`,
  enforcing the tool allowlist.

Run it offline, no keys:

```bash
python examples/booking_agent/booking_agent.py
```
