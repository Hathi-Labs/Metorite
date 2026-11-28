# Spike notes

## Why
We want agents that **write code** and run it in a sandbox.

## Findings
- Docker sandbox: warm exec ~0.12 s
- Hyperlight: Python-only CodeAct

| Option | Shell | pip |
|---|---|---|
| Docker | yes | allow-list |
| Hyperlight | no | no |
