# Publication audit

The v0.1 release gate ([v0.1.md](v0.1.md#release-gate)) requires an OSS license
and an inspection of committed content for private data before the repository
is published. This page records the procedure and the result of that
inspection. It does not publish anything: changing repository visibility
remains a separate maintainer action, and no workflow publishes automatically.

- Date: 2026-10-07
- Issue: #52
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
| 1. Tracked files | `apps/cli/tests/test_diagnose_cli.py`: `sk-or-test-secret-abc123` | Accepted. A fake key the test uses to prove secrets never reach output. |
| 2. History | none | — |
| 3. Runtime artifacts | none | `.ontology/` and `.verification/` stay git-ignored. |
| 4. Data files | Job snapshots under `examples/job-invoicing/data/` and `eval_fixtures/data/`; diagnosis comparison reports and eval output fixtures under `docs/` and `eval_fixtures/outputs/` | Accepted. All synthetic: ids like `101`/`B01`/`E01`, assignees `taro`/`hanako`, and one deliberate prompt-injection string in `jobs_untrusted.csv`. The comparison report's `workspace_dir` is repository-relative. |
| Prose review | `docs/diagnosis-comparison-2026-10-06.md` named the operator's local env file | Removed in #52. It held no secret; earlier history keeps the phrase, which is acceptable. |

Commit metadata contains the maintainer's name and email address, which a
public repository exposes. Rewriting history to change that is a maintainer
decision outside this audit.

## Re-running

Re-run the procedure before the visibility change if more commits land after
the audited date, and before committing any real-data findings (see the
de-identified data follow-up in [v0.1.md](v0.1.md#release-gate)).
