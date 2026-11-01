# Neurotree plugin for Claude Code

`/neurotree:scan <question>`: Claude asks Neurotree (the attention tree of this Mac) which projects, files and
claude-mem memories hold the knowledge for the question, reads only those, and cites them.
`/neurotree:scan` with no question: what you worked on in the last 24 hours, 7 and 30 days.

Install once:

```bash
claude plugin marketplace add ~/Desktop/Neurotree/plugin
claude plugin install neurotree@neurotree
```

It runs `bin/neurotree`, which calls the Neurotree checkout (`NEUROTREE_REPO`, default `~/Desktop/Neurotree`) and
its `.venv`. Local only: no network, nothing written except a reindex when the index is over a day old.
No MCP server, so nothing runs between scans. Remove with `claude plugin uninstall neurotree`.
