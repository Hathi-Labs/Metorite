# Inherited history

Metorite began as a full-history mirror of [`FracktalWorks/CommandCenter`](https://github.com/FracktalWorks/CommandCenter).
Commits, branches and tags carried over natively. Everything GitHub keeps outside
git — pull requests and issues — is archived here as data.

| | |
|---|---|
| [Pull requests](pull-requests/index.md) | 449 archived, 437 merged |
| [Issues](issues.md) | 2 archived |
| [Raw API payloads](raw/) | verbatim JSON, for tooling |

## Fetching the archived PR commits

```bash
git fetch origin 'refs/archive/pr/*:refs/archive/pr/*'
git show refs/archive/pr/433          # head commit of original PR #433
git diff main...refs/archive/pr/433   # its diff
```

These refs are read-only history. Do not build on them.
