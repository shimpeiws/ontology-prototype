# Real-data applicability check

The loop has only run on synthetic fixtures. This page explains how to check that it applies to a real operational sample, and it records the results. The run happens locally on de-identified data. Only this procedure and non-sensitive findings are committed (#58; follow-up from [v0.1.md](v0.1.md#release-gate)).

The check verifies applicability: whether real exports map, ingest, project, and evaluate, and which diagnostics and gaps they produce. It does not claim a business improvement.

## Status

| Date | Sample | Result |
| --- | --- | --- |
| 2026-10-07 | none available | No suitable de-identified sample exists yet. The procedure and the `scripts/deidentify.py` tool are ready. This does not block v0.3. |

## Rules

- Work in a directory **outside the repository**. `scripts/deidentify.py` refuses to write its output or key file anywhere inside a git work tree unless git reports the path as ignored.
- Never commit raw data, de-identified data, keys, `.ontology/` stores, traces, or tool output from a real run. Commit only what the findings template below asks for.
- Record diagnostic **codes, severities, field names, and counts**. Do not record diagnostic `message` text, because some messages quote values from the source.
- Do not record business numbers (latencies, medians, rates) unless the data owner approves. Record the verdict class (`success`, `not_met`, `unverified`) and the guardrail state instead.
- Delete the key, the de-identified files, and the run workspace when the check is done.

## Procedure

Run from the repository root, with the packages installed as described in the [README](../README.md).

### 1. Prepare a private work directory and key

```bash
WORK=~/ontology-realdata          # any path outside the repository
mkdir -p "$WORK" && chmod 700 "$WORK"
python scripts/deidentify.py --generate-key "$WORK/key.hex"
```

The key file is created with owner-only permissions, and the tool never overwrites an existing key. Use **one key for every file in the sample**. The same raw value then gets the same pseudonym in every column and file, so references such as `assignee` → `Person.id` still resolve.

### 2. Classify every column

List each source file's columns and classify every column with exactly one action. The tool refuses to run while any column is unclassified.

| Action | Use for | Effect |
| --- | --- | --- |
| `--pseudonymize` | ids, references, names, emails: anything identifying that the mapping must still join on | Keyed HMAC pseudonym. Surrounding whitespace, blanks, and JSON number ids keep their shape, so ingestion diagnostics are unchanged. |
| `--shift` | dates and datetimes | Every value moves by the same secret whole-day offset, derived from the key, so intervals are preserved. Time of day, UTC offset, and naive-ness stay as they were. A malformed value becomes `invalid-timestamp`. |
| `--keep` | non-identifying categories: statuses, booleans, enum codes | Copied verbatim. **Your judgement**: never keep free text, amounts that identify a customer, or anything you are unsure about. |
| `--drop` | everything the mapping does not need | Removed. |

Nested JSON values in any column are replaced by an empty object or list. Extra CSV cells beyond the header become blank cells. Both keep the shape ingestion reports (`INGEST.NON_SCALAR`, `INGEST.INVALID_ROW`) without the content. A string with an unpaired surrogate becomes a content-free placeholder that still triggers `INGEST.ENCODING_ERROR`.

The tool refuses, without writing any output, in two cases. The first is a CSV that ingestion's strict parser would reject as a whole, such as one with an unterminated quote: record that as an `INGEST.INVALID_SOURCE` finding. The second is a valid date that the offset would push out of the representable range: pass a different `--shift-days`.

### 3. De-identify

```bash
python scripts/deidentify.py --key "$WORK/key.hex" --format csv \
  --pseudonymize id,assignee --shift completed_at,invoiced_at \
  --keep status --drop customer_name,notes \
  /path/to/export/jobs.csv "$WORK/jobs.csv"
```

`--format` is the workspace's `mapping.source.format`, not the file extension. The tool prints only the column names, the action applied to each, and how many values each column processed. It never prints values, the key, or the date offset. The output is created atomically and never overwrites an existing file. Repeat this step for each file with the same key.

Inspect the de-identified files yourself before going further.

### 4. Build a run workspace

```bash
cp -R examples/job-invoicing "$WORK/ws"   # or the workspace for the workflow under test
rm -rf "$WORK/ws/.ontology" "$WORK/ws/data"
# Edit $WORK/ws/mapping.yaml so source fields match the real column names.
ontology check "$WORK/ws"
```

Every edit you need here is a finding (a mapping gap). Note each one in step 6.

### 5. Run the loop's deterministic stages

```bash
ontology ingest "$WORK/ws" "$WORK/jobs.csv" --json > "$WORK/ingest.json"
python - "$WORK/ingest.json" <<'PY'
import collections, json, sys
r = json.load(open(sys.argv[1]))
count = lambda v: len(v) if isinstance(v, list) else v
print("accepted:", count(r["accepted"]), "rejected:", count(r["rejected"]))
codes = collections.Counter((d["severity"], d["code"], d["path"].split(".", 1)[-1] if d["path"] else "-")
                            for d in r["diagnostics"])
for (sev, code, field), n in sorted(codes.items()):
    print(f"{sev:7} {code:28} {field:20} {n}")
PY
ontology project "$WORK/ws" --as-of <ISO-8601 with offset>
ontology evaluate "$WORK/ws" --boundary <ISO-8601> --as-of <ISO-8601>
```

The summary prints only counts, codes, and field names, which is the form findings use.

`--as-of` and `--boundary` decide the cohorts, and therefore the verdict. Pick them by a named rule, applied to the de-identified data. Then repeated checks are comparable without recording any date. The original calendar times no longer line up, and the tool never prints the offset.

| Rule | `--as-of` | `--boundary` |
| --- | --- | --- |
| `change-date` (preferred) | 00:00 UTC on the day after the latest de-identified start or end event | the de-identified date of the business change being evaluated. Shift the change date with the same key: put it in a one-column file and run `--shift` on that column. |
| `median-start` (no known change) | as above | 00:00 UTC on the median de-identified start-event date, so the two cohorts are roughly equal halves |

Record the rule name in the findings. Do not record the resulting timestamps. The diagnosis, approval, and write-back stages are out of scope here: they never touch the source and add nothing to applicability.

### 6. Record findings and clean up

Add a row to the Status table and a section under Findings, using the template below. Then delete `$WORK` entirely, including the key.

Mapping gaps and missing core capabilities also go to the gap log that #54 introduces (`docs/generality-gaps.md`). Until that log exists, list them here.

## Findings template

```markdown
### <YYYY-MM-DD> — <workflow>, <source system kind, e.g. "job export from a field-service SaaS">

- Sample: <formats>, <record count bucket: <100 / 100–1k / 1k–10k / >10k>, <columns classified: N pseudonymize / N shift / N keep / N drop>
- Mapping gaps: <what the four definitions could not express, by concept, not by value>
- Ingestion: <accepted/rejected counts; diagnostic code × field × count table>
- Data quality: <patterns such as "naive datetimes in completed_at", "ids with trailing spaces">
- Projection/evaluation: <ran / failed with code>; window rule <change-date | median-start>; verdict class <success | not_met | unverified>; guardrail <passed | failed | unavailable>
- Follow-ups: <issue links>
```

## Findings

None yet. See Status.

## Limits of the de-identification

- Pseudonyms are linkable within a key. That is required for joins, and it is why the key stays local and is deleted afterwards.
- A whole-day shift keeps intervals and the time of day, but it moves calendar context: weekdays, holidays, month ends. Do not draw conclusions that depend on specific calendar dates.
- `--keep` is copied verbatim. The tool cannot tell whether a kept column is safe.
- This is pseudonymization for a local check, not a formal anonymity guarantee such as k-anonymity or differential privacy.
