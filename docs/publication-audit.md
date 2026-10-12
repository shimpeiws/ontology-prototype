# Publication audit

The v0.1 release gate ([v0.1.md](v0.1.md#release-gate)) requires an OSS license
and an inspection of committed content for private data before the repository
is published. This page records the procedure and the result of that
inspection. It does not publish anything: changing repository visibility
remains a separate maintainer action, and no workflow publishes automatically.

- Date: 2026-10-12 (re-run ahead of the visibility change; first run 2026-10-07)
- Issue: #98 (re-run), #52 (first run)
- License: MIT ([LICENSE](../LICENSE)), declared in every package's metadata
- Scope: every tracked file at the audited commit, and every line ever added
  in the history reachable from `origin/main`

## Procedure

Run these from the repository root. Each command prints only findings; empty
output means nothing matched.

```bash
# 1. Tracked files: credential-shaped tokens, home-directory paths, emails.
git grep -n -I -E 'sk-or-[A-Za-z0-9-]+|sk-ant-[A-Za-z0-9-]+|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|/Users/[A-Za-z]|/home/[a-z]+/|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' \
  -- . ':!apps/web/package-lock.json' ':!graft' \
  | grep -v -E 'noreply@|example\.(com|org)|@types/|@react-router|@vitejs|@testing-library'

# 2. History: credential-shaped tokens and home paths in any added line.
git log --all -p --no-color origin/main | grep -E '^\+' \
  | grep -oE 'sk-or-v1-[A-Za-z0-9]{8,}|sk-ant-[A-Za-z0-9-]{8,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|/Users/[A-Za-z]+' \
  | sort | uniq -c

# 3. Runtime artifacts ever committed (canonical store, receipts, run dirs, env files).
git log --all --name-only --format= origin/main \
  | grep -E '(^|/)\.(ontology|verification|env)(/|$)|\.(db|sqlite3?)$|reminders\.csv$' | sort -u

# 4. Data files to read by hand.
git ls-files | grep -E '\.(csv|json|jsonl)$' | grep -v -E 'package(-lock)?\.json|tsconfig|graft/'
```

## Result

| Check | Finding | Decision |
| --- | --- | --- |
| 1. Tracked files | `apps/cli/tests/test_diagnose_cli.py`: `sk-or-test-secret-abc123`; this document's own pattern literals self-match | Accepted. The fake key exists to prove secrets never reach output; the doc hits are the scan's own regexes. |
| 2. History | none | — |
| 3. Runtime artifacts | none | `.ontology/` and `.verification/` stay git-ignored. |
| 4. Data files | Job snapshots under `examples/job-invoicing/data/` (now including `invoices-{baseline,followup}.csv`), ticket snapshots under `examples/ticket-sla/data/`, `eval_fixtures/`; diagnosis comparison reports under `docs/` | Accepted. All synthetic: ids like `101`/`B01`/`T-C01`, assignees `taro`/`hanako`/`akira`/`yuki`, and one deliberate prompt-injection string in `jobs_untrusted.csv`. |
| New content since the first run | `scripts/deidentify.py` and its tests, `docs/v0.2`–`v0.5`, ADR 0003/0004, `real-data-validation.md`, ticket-sla docs, `AGENTS.md`, per-package `LICENSE` copies, `.github/workflows/web.yml` | Accepted. No personal paths, email addresses, or organization names; the workflows use no secrets and no external services. |
| Prose review (first run) | `docs/diagnosis-comparison-2026-10-06.md` named the operator's local env file | Removed in #52. It held no secret; earlier history keeps the phrase, which is acceptable. |

Commit metadata contains the maintainer's name and email address
(`Shimpei Takamatsu` / `shin` `<shimpeiws@gmail.com>`, plus `GitHub
<noreply@github.com>` on merges), which a public repository exposes.
Rewriting history to change that is a maintainer decision outside this audit.

## Re-running

Re-run the procedure before the visibility change if more commits land after
the audited date, and before committing any real-data findings (see the
de-identified data follow-up in [v0.1.md](v0.1.md#release-gate)).
