# Agent tools never receive a user identity from the model

The MCP servers are separate processes the LLM drives; whatever arguments the model emits are untrusted. A tool therefore never accepts a `user_id` (or anything that selects a tenant) as an argument. The user is fixed by the server that spawns the MCP subprocess (environment variable / connection-time context) and enforced again by Postgres RLS. This closes the confused-deputy path where a prompt-injected model asks a tool for another user's data. Extends `backend/ai/AGENTS.md` rule 13 (egress) to ingress/tenancy.
