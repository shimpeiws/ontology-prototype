"""Read-only operational inspection: trace, evaluations, proposals.

Issue #61: the operational surface exposes the CLI's read views over the
shared ``.ontology/`` store. Payloads match the CLI ``--json`` output for
the same queries on the job-invoicing walkthrough state; reads never
create ``.ontology/`` or ``canon.db``; core diagnostics pass through
unchanged while transport failures stay ``{error}``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import apisupport
import pytest
from fastapi.testclient import TestClient

from ontology_api import ApiConfig, create_app
from ontology_cli.main import EXIT_OK, main as cli_main

DEMO_DATA = apisupport.REPO_ROOT / "examples" / "job-invoicing" / "data"


def make_client(*roots: Path) -> TestClient:
    return TestClient(create_app(ApiConfig(workspace_roots=tuple(roots))))


def cli(capsys, *args: str) -> str:
    code = cli_main(list(args))
    out = capsys.readouterr().out
    assert code == EXIT_OK, f"ontology {' '.join(args)} exited {code}: {out}"
    return out


def cli_json(capsys, *args: str) -> dict:
    return json.loads(cli(capsys, *args))


def cli_payload(capsys, *args: str) -> dict:
    """Parse the CLI's --json output regardless of exit code — domain
    failures print a payload and exit non-zero."""
    cli_main(list(args))
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def walked(tmp_path, capsys, roots, workspace) -> SimpleNamespace:
    """The job-invoicing walkthrough state, driven through the CLI:
    ingest -> project -> diagnose (stub) -> approve -> execute ->
    re-ingest -> evaluate."""
    for name in ("jobs-baseline", "jobs-current"):
        cli(
            capsys, "ingest", str(workspace),
            str(DEMO_DATA / f"{name}.csv"), "--source", "jobs",
        )
    project = cli_json(
        capsys, "project", str(workspace),
        "--as-of", "2026-10-21T00:00:00Z", "--json",
    )
    diagnosis = cli_json(
        capsys,
        "diagnose", str(workspace),
        "--as-of", "2026-10-21T00:00:00Z",
        "--provider", "stub", "--json",
    )
    pid = cli_json(capsys, "proposals", str(workspace), "--json")[
        "proposals"
    ][0]["proposal_id"]
    cli(
        capsys, "decide", str(workspace), pid,
        "--approve", "--reviewer", "fde-sato",
    )
    cli(capsys, "execute", str(workspace), pid)
    cli(
        capsys, "ingest", str(workspace),
        str(DEMO_DATA / "jobs-followup.csv"), "--source", "jobs",
    )
    for name in ("invoices-baseline", "invoices-followup"):
        cli(
            capsys, "ingest", str(workspace),
            str(DEMO_DATA / f"{name}.csv"), "--source", "invoices",
        )
    evaluation = cli_json(
        capsys,
        "evaluate", str(workspace),
        "--boundary", "2026-10-15T00:00:00Z",
        "--as-of", "2026-10-26T00:00:00Z", "--json",
    )
    return SimpleNamespace(
        workspace=workspace,
        proposal_id=pid,
        projection_run_id=project["run_id"],
        diagnosis_run_id=diagnosis["run_id"],
        evaluation_run_id=evaluation["run_id"],
    )


@pytest.fixture
def proposed(tmp_path, capsys, roots, workspace) -> SimpleNamespace:
    """One persisted, undecided proposal: the walkthrough up to the
    stubbed diagnosis run, before any decision exists."""
    for name in ("jobs-baseline", "jobs-current"):
        cli(
            capsys, "ingest", str(workspace),
            str(DEMO_DATA / f"{name}.csv"), "--source", "jobs",
        )
    cli(capsys, "project", str(workspace), "--as-of", "2026-10-21T00:00:00Z")
    cli(
        capsys, "diagnose", str(workspace),
        "--as-of", "2026-10-21T00:00:00Z", "--provider", "stub",
    )
    pid = cli_json(capsys, "proposals", str(workspace), "--json")[
        "proposals"
    ][0]["proposal_id"]
    return SimpleNamespace(workspace=workspace, proposal_id=pid)


def _canonical_dump(db: Path) -> str:
    """Committed content of canon.db — read through the WAL so a
    committed-but-uncheckpointed write is still visible."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def fingerprint(root: Path) -> dict[str, object]:
    """Runtime content for mutation checks.

    Under WAL (ADR 0003) the ``canon.db-shm``/``canon.db-wal`` sidecars
    are engine bookkeeping — rebuilt on open, and a read-only close can
    leave them behind, but they carry no durable state — so they carry
    no fingerprint weight, and canon.db compares by committed content
    rather than bytes (a checkpoint moves bytes without changing
    content).
    """
    entries: dict[str, object] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if p.name.startswith("canon.db-"):
            continue
        entries[rel] = (
            _canonical_dump(p) if p.name == "canon.db" else p.read_bytes()
        )
    return entries


class TestContractSurface:
    def test_contract_lists_operational_operations(self, roots) -> None:
        operations = make_client(roots).get("/api/v1/contract").json()[
            "operations"
        ]
        assert operations["trace_overview"] == (
            "POST /api/v1/workspaces/trace"
        )
        assert operations["trace_subject"] == (
            "POST /api/v1/workspaces/trace/subject"
        )
        assert operations["trace_run"] == (
            "POST /api/v1/workspaces/trace/run"
        )
        assert operations["evaluation_runs"] == (
            "POST /api/v1/workspaces/evaluations"
        )
        assert operations["evaluation_run"] == (
            "POST /api/v1/workspaces/evaluations/run"
        )
        assert operations["proposals"] == (
            "POST /api/v1/workspaces/proposals"
        )
        assert operations["proposal"] == (
            "POST /api/v1/workspaces/proposals/show"
        )


def post(client: TestClient, route: str, **body) -> dict:
    response = client.post(f"/api/v1/workspaces/{route}", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["api_version"] == "v1"
    return payload


class TestCliParity:
    """Acceptance: for the walkthrough state, each API payload equals the
    CLI --json output for the same query, plus ``api_version``."""

    def test_trace_overview(self, walked, roots, capsys) -> None:
        expected = cli_json(capsys, "trace", str(walked.workspace), "--json")
        body = post(
            make_client(roots), "trace", path=str(walked.workspace)
        )
        assert body == {"api_version": "v1", **expected}
        assert body["ok"] is True
        assert body["proposals"]["by_status"] == {"approved": 1}
        assert body["links"]["unresolved"] == 0

    def test_trace_subject(self, walked, roots, capsys) -> None:
        expected = cli_json(
            capsys, "trace", str(walked.workspace),
            "--subject", "Job/C01", "--json",
        )
        body = post(
            make_client(roots),
            "trace/subject",
            path=str(walked.workspace),
            subject="Job/C01",
        )
        assert body == {"api_version": "v1", **expected}
        assert body["trace"]["subject"] == {"entity": "Job", "id": "C01"}
        assert body["trace"]["evaluations"]

    def test_trace_subject_long_id(
        self, workspace, roots, capsys, tmp_path
    ) -> None:
        """Ingestion stores subject ids verbatim — an id longer than the
        former request cap still resolves through the API."""
        long_id = "C" * 2000
        csv = tmp_path / "long-subject.csv"
        csv.write_text(
            "id,status,assignee,completed_at,invoiced_at\n"
            f"{long_id},completed,taro,2026-10-01,2026-10-09\n"
        )
        cli(
            capsys, "ingest", str(workspace), str(csv),
            "--source", "jobs",
        )
        subject = f"Job/{long_id}"
        expected = cli_json(
            capsys,
            "trace", str(workspace), "--subject", subject, "--json",
        )
        body = post(
            make_client(roots),
            "trace/subject",
            path=str(workspace),
            subject=subject,
        )
        assert body == {"api_version": "v1", **expected}
        assert body["ok"] is True
        assert body["trace"]["subject"]["id"] == long_id

    def test_trace_run(self, walked, roots, capsys) -> None:
        # `trace --run` anchors any recorded artifact id, not only runs.
        for anchor in (
            walked.proposal_id,
            walked.projection_run_id,
            walked.diagnosis_run_id,
            walked.evaluation_run_id,
        ):
            expected = cli_json(
                capsys, "trace", str(walked.workspace),
                "--run", anchor, "--json",
            )
            body = post(
                make_client(roots),
                "trace/run",
                path=str(walked.workspace),
                run_id=anchor,
            )
            assert body == {"api_version": "v1", **expected}, anchor
            assert body["trace"]["id"] == anchor

    def test_proposals(self, walked, roots, capsys) -> None:
        expected = cli_json(capsys, "proposals", str(walked.workspace), "--json")
        body = post(
            make_client(roots), "proposals", path=str(walked.workspace)
        )
        assert body == {"api_version": "v1", **expected}
        proposal = body["proposals"][0]
        assert proposal["proposal_id"] == walked.proposal_id
        assert proposal["status"] == "approved"
        assert proposal["latest_decision"]["reviewer"] == "fde-sato"
        assert [e["status"] for e in proposal["executions"]] == [
            "attempting",
            "succeeded",
        ]

    def test_proposal_detail(self, walked, roots, capsys) -> None:
        expected = cli_json(
            capsys,
            "proposals", str(walked.workspace),
            "--show", walked.proposal_id, "--json",
        )
        body = post(
            make_client(roots),
            "proposals/show",
            path=str(walked.workspace),
            proposal_id=walked.proposal_id,
        )
        assert body == {"api_version": "v1", **expected}
        assert body["proposal"]["decisions"]

    def test_evaluation_run(self, walked, roots, capsys) -> None:
        expected = cli_json(
            capsys,
            "evaluate", str(walked.workspace),
            "--run", walked.evaluation_run_id, "--json",
        )
        body = post(
            make_client(roots),
            "evaluations/run",
            path=str(walked.workspace),
            run_id=walked.evaluation_run_id,
        )
        assert body == {"api_version": "v1", **expected}
        assert body["run"]["report"]["target"]["status"] == "attained"

    def test_evaluation_runs_list(self, walked, roots, capsys) -> None:
        # No CLI list exists; the entries carry the same record shape the
        # `evaluate --run` detail returns.
        detail = post(
            make_client(roots),
            "evaluations/run",
            path=str(walked.workspace),
            run_id=walked.evaluation_run_id,
        )
        body = post(
            make_client(roots), "evaluations", path=str(walked.workspace)
        )
        assert body == {
            "api_version": "v1",
            "workspace": str(walked.workspace),
            "ok": True,
            "evaluation_runs": [detail["run"]],
        }


class TestContractFailures:
    """Missing stores, corrupt journals, and unknown ids are domain
    results — diagnostics payloads, never the ``{error}`` shape."""

    def test_missing_store(self, roots, workspace) -> None:
        client = make_client(roots)
        path = str(workspace)

        overview = post(client, "trace", path=path)
        assert overview["ok"] is True
        assert overview["journal"] == {
            "events": 0,
            "unreadable_lines": 0,
            "failed_events": 0,
        }

        assert post(client, "trace/run", path=path, run_id="dgx-nope")[
            "diagnostics"
        ][0]["code"] == "TRACE.UNKNOWN_ID"

        proposals = post(client, "proposals", path=path)
        assert proposals["ok"] is True
        assert proposals["proposals"] == []

        runs = post(client, "evaluations", path=path)
        assert runs["ok"] is True
        assert runs["evaluation_runs"] == []

        run = post(client, "evaluations/run", path=path, run_id="evl-nope")
        assert run["ok"] is False
        assert run["diagnostics"][0]["code"] == "EVALUATION.UNKNOWN_RUN"

        # No read created the runtime directory or the canonical store.
        assert not (workspace / ".ontology").exists()

    def test_reads_never_create_runtime_state(
        self, roots, workspace
    ) -> None:
        client = make_client(roots)
        path = str(workspace)
        before = fingerprint(workspace)
        for route, body in (
            ("trace", {"path": path}),
            ("trace/subject", {"path": path, "subject": "Job/101"}),
            ("trace/run", {"path": path, "run_id": "dgx-nope"}),
            ("evaluations", {"path": path}),
            ("evaluations/run", {"path": path, "run_id": "evl-nope"}),
            ("proposals", {"path": path}),
            ("proposals/show", {"path": path, "proposal_id": "x-p0"}),
        ):
            post(client, route, **body)
        assert fingerprint(workspace) == before
        assert not (workspace / ".ontology").exists()

    def test_reads_do_not_mutate_runtime(self, walked, roots) -> None:
        runtime = walked.workspace / ".ontology"
        before = fingerprint(runtime)
        client = make_client(roots)
        path = str(walked.workspace)
        for route, body in (
            ("trace", {"path": path}),
            ("trace/subject", {"path": path, "subject": "Job/C01"}),
            ("trace/run", {"path": path, "run_id": walked.proposal_id}),
            ("evaluations", {"path": path}),
            (
                "evaluations/run",
                {"path": path, "run_id": walked.evaluation_run_id},
            ),
            ("proposals", {"path": path}),
            (
                "proposals/show",
                {"path": path, "proposal_id": walked.proposal_id},
            ),
        ):
            post(client, route, **body)
        assert fingerprint(runtime) == before

    def test_corrupt_journal(self, walked, roots, capsys) -> None:
        journal = walked.workspace / ".ontology" / "trace.jsonl"
        with journal.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")

        expected = cli_json(capsys, "trace", str(walked.workspace), "--json")
        body = post(
            make_client(roots), "trace", path=str(walked.workspace)
        )
        assert body == {"api_version": "v1", **expected}
        assert body["ok"] is True
        assert any(
            d["code"] == "TRACE.JOURNAL_CORRUPT"
            for d in body["diagnostics"]
        )

        # The corrupt journal degrades the same way on the run view.
        expected_run = cli_json(
            capsys,
            "trace", str(walked.workspace),
            "--run", walked.proposal_id, "--json",
        )
        run = post(
            make_client(roots),
            "trace/run",
            path=str(walked.workspace),
            run_id=walked.proposal_id,
        )
        assert run == {"api_version": "v1", **expected_run}
        assert any(
            d["code"] == "TRACE.JOURNAL_CORRUPT"
            for d in run["diagnostics"]
        )

    def test_unknown_ids(self, walked, roots, capsys) -> None:
        client = make_client(roots)
        path = str(walked.workspace)

        trace = post(client, "trace/run", path=path, run_id="dgx-nope")
        assert trace["ok"] is False
        assert trace["diagnostics"][0]["code"] == "TRACE.UNKNOWN_ID"
        assert "trace" not in trace  # omitted like the CLI payload

        run = post(
            client, "evaluations/run", path=path, run_id="evl-nope"
        )
        assert run["ok"] is False
        assert run["diagnostics"][0]["code"] == "EVALUATION.UNKNOWN_RUN"

        proposal = post(
            client, "proposals/show", path=path, proposal_id="dgx-x-p9"
        )
        assert proposal["ok"] is False
        assert proposal["diagnostics"][0]["code"] == (
            "DECISION.UNKNOWN_PROPOSAL"
        )

    def test_trace_invalid_subject(self, walked, roots, capsys) -> None:
        client = make_client(roots)
        path = str(walked.workspace)
        for bad in ("C01", "/C01", "Job/", "Job"):
            body = post(
                client, "trace/subject", path=path, subject=bad
            )
            assert body["ok"] is False
            assert body["diagnostics"][0]["code"] == (
                "TRACE.INVALID_SUBJECT"
            )

        # The subject value is echoed into the diagnostic but bounded —
        # an unbounded echo would balloon the payload on bad input.
        body = post(
            client, "trace/subject", path=path, subject="x" * 5000
        )
        assert body["ok"] is False
        assert body["diagnostics"][0]["code"] == "TRACE.INVALID_SUBJECT"
        assert len(body["diagnostics"][0]["message"]) < 500

    def test_corrupt_store(self, walked, roots, capsys) -> None:
        # A damaged canon.db must degrade to the core's store-error
        # diagnostics (200 + ok:false), never a 500.
        db = walked.workspace / ".ontology" / "canon.db"
        db.write_bytes(b"not a sqlite database")

        expected = cli_payload(
            capsys, "trace", str(walked.workspace), "--json"
        )
        body = post(
            make_client(roots), "trace", path=str(walked.workspace)
        )
        assert body == {"api_version": "v1", **expected}
        assert any(
            d["code"] == "TRACE.STORE_ERROR" for d in body["diagnostics"]
        )

        runs = post(
            make_client(roots), "evaluations", path=str(walked.workspace)
        )
        assert runs["ok"] is False
        assert runs["diagnostics"][0]["code"] == "EVALUATION.STORE_ERROR"

        run = post(
            make_client(roots),
            "evaluations/run",
            path=str(walked.workspace),
            run_id=walked.evaluation_run_id,
        )
        assert run["ok"] is False
        assert run["diagnostics"][0]["code"] == "EVALUATION.STORE_ERROR"

        cli_main(["proposals", str(walked.workspace), "--json"])
        expected_proposals = json.loads(capsys.readouterr().out)
        proposals = post(
            make_client(roots), "proposals", path=str(walked.workspace)
        )
        assert proposals == {"api_version": "v1", **expected_proposals}
        assert proposals["diagnostics"][0]["code"] == (
            "DECISION.STORE_ERROR"
        )

    def test_unindexed_proposals_are_not_backfilled(
        self, walked, roots
    ) -> None:
        # Legacy state: the diagnosis run holds proposals that were never
        # indexed into the proposals table (e.g. recorded before the
        # table existed). The CLI's read-write path backfills them on
        # first `proposals`; the read-only API must not — it reports the
        # index as-is while the trace view still surfaces them.
        db = walked.workspace / ".ontology" / "canon.db"
        conn = sqlite3.connect(db)
        try:
            # The table is append-only; the test drops and restores the
            # guard trigger to simulate a store that predates the index.
            conn.execute("DROP TRIGGER proposals_no_delete")
            conn.execute("DELETE FROM proposals")
            conn.execute(
                "CREATE TRIGGER proposals_no_delete BEFORE DELETE ON "
                "proposals BEGIN SELECT RAISE(ABORT, 'proposals are "
                "append-only'); END"
            )
            conn.commit()
        finally:
            conn.close()

        before = fingerprint(walked.workspace / ".ontology")
        client = make_client(roots)
        path = str(walked.workspace)

        body = post(client, "proposals", path=path)
        assert body["ok"] is True
        assert body["proposals"] == []

        detail = post(
            client,
            "proposals/show",
            path=path,
            proposal_id=walked.proposal_id,
        )
        assert detail["ok"] is False
        assert detail["diagnostics"][0]["code"] == (
            "DECISION.UNKNOWN_PROPOSAL"
        )

        overview = post(client, "trace", path=path)
        assert overview["proposals"]["unindexed"] == 1

        assert fingerprint(walked.workspace / ".ontology") == before

    def test_failure_payloads_match_cli(
        self, walked, roots, capsys
    ) -> None:
        # Domain failures are CLI-identical payloads, not just codes.
        client = make_client(roots)
        path = str(walked.workspace)

        expected = cli_payload(
            capsys, "trace", path, "--run", "dgx-nope", "--json"
        )
        assert post(client, "trace/run", path=path, run_id="dgx-nope") == {
            "api_version": "v1",
            **expected,
        }

        expected = cli_payload(
            capsys, "trace", path, "--subject", "nope", "--json"
        )
        assert post(
            client, "trace/subject", path=path, subject="nope"
        ) == {"api_version": "v1", **expected}

        expected = cli_payload(
            capsys, "evaluate", path, "--run", "evl-nope", "--json"
        )
        assert post(
            client, "evaluations/run", path=path, run_id="evl-nope"
        ) == {"api_version": "v1", **expected}

        expected = cli_payload(
            capsys, "proposals", path, "--show", "dgx-x-p9", "--json"
        )
        assert post(
            client, "proposals/show", path=path, proposal_id="dgx-x-p9"
        ) == {"api_version": "v1", **expected}

    def test_trace_survives_broken_definitions(
        self, walked, roots, capsys
    ) -> None:
        # History inspection never loads the definitions — a later
        # invalid edit cannot hide the recorded run.
        (walked.workspace / "domain.yaml").write_text("not: [valid\n")
        client = make_client(roots)
        path = str(walked.workspace)

        expected = cli_json(
            capsys,
            "trace", str(walked.workspace),
            "--run", walked.proposal_id, "--json",
        )
        body = post(client, "trace/run", path=path,
                    run_id=walked.proposal_id)
        assert body == {"api_version": "v1", **expected}
        assert body["trace"]["record"]["status"] == "approved"

        run = post(
            client,
            "evaluations/run",
            path=path,
            run_id=walked.evaluation_run_id,
        )
        assert run["ok"] is True

        # Proposal inspection loads the workspace like the CLI does and
        # reports the workspace's own diagnostics when it is invalid.
        cli_main(["proposals", str(walked.workspace), "--json"])
        expected_proposals = json.loads(capsys.readouterr().out)
        body = post(client, "proposals", path=path)
        assert body == {"api_version": "v1", **expected_proposals}
        assert body["ok"] is False
        assert body["diagnostics"]
        assert "proposals" not in body


class TestLegacyStoreCompat:
    """A ``canon.db`` written before the history tables existed degrades
    to empty sections, not store errors — a read-only open never
    migrates the schema."""

    def test_missing_history_tables(self, walked, roots) -> None:
        db = walked.workspace / ".ontology" / "canon.db"
        conn = sqlite3.connect(db)
        try:
            for table in (
                "fact_links",
                "executions",
                "decisions",
                "proposals",
                "diagnosis_runs",
                "evaluation_runs",
            ):
                conn.execute(f"DROP TABLE {table}")
            # Columns added by migration are absent on the same older
            # revision.
            conn.execute(
                "ALTER TABLE entities DROP COLUMN properties_changed_at"
            )
            conn.execute("ALTER TABLE entities DROP COLUMN last_ingest_id")
            conn.commit()
        finally:
            conn.close()

        before = fingerprint(walked.workspace / ".ontology")
        client = make_client(roots)
        path = str(walked.workspace)

        runs = post(client, "evaluations", path=path)
        assert runs == {
            "api_version": "v1",
            "workspace": path,
            "ok": True,
            "evaluation_runs": [],
        }

        run = post(
            client,
            "evaluations/run",
            path=path,
            run_id=walked.evaluation_run_id,
        )
        assert run["ok"] is False
        assert run["diagnostics"][0]["code"] == "EVALUATION.UNKNOWN_RUN"

        proposals = post(client, "proposals", path=path)
        assert proposals["ok"] is True
        assert proposals["proposals"] == []

        proposal = post(
            client,
            "proposals/show",
            path=path,
            proposal_id=walked.proposal_id,
        )
        assert proposal["ok"] is False
        assert proposal["diagnostics"][0]["code"] == (
            "DECISION.UNKNOWN_PROPOSAL"
        )

        overview = post(client, "trace", path=path)
        assert overview["ok"] is True
        assert overview["canonical"]["entities"] > 0
        assert overview["evaluation_runs"] == []
        assert not any(
            d["code"] == "TRACE.STORE_ERROR"
            for d in overview["diagnostics"]
        )

        subject = post(
            client, "trace/subject", path=path, subject="Job/C01"
        )
        assert subject["ok"] is True
        entity = subject["trace"]["entity"]
        # The absent column reads as the migration backfill — the last
        # winning observation time — not an empty string.
        assert entity["properties_changed_at"] == entity["updated_at"]
        assert entity["properties_changed_at"] != ""

        # Read-only inspection never writes the missing schema back.
        assert fingerprint(walked.workspace / ".ontology") == before

    def test_missing_table_is_not_a_store_error(
        self, walked, roots, capsys
    ) -> None:
        """CLI parity: the CLI's read-only trace path degrades the same
        way on a pre-upgrade database."""
        db = walked.workspace / ".ontology" / "canon.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("DROP TABLE evaluation_runs")
            conn.commit()
        finally:
            conn.close()

        expected = cli_json(capsys, "trace", str(walked.workspace), "--json")
        body = post(
            make_client(roots), "trace", path=str(walked.workspace)
        )
        assert body == {"api_version": "v1", **expected}
        assert body["ok"] is True
        assert body["evaluation_runs"] == []


class TestPathGuards:
    """Transport-level failures keep the ``{error}`` shape."""

    def test_runtime_symlink_escape(self, walked, roots, tmp_path) -> None:
        """``.ontology`` resolving outside the roots is refused — reads
        never follow a planted symlink into external runtime state."""
        runtime = walked.workspace / ".ontology"
        external = tmp_path / "outside-roots" / ".ontology"
        external.parent.mkdir()
        runtime.rename(external)
        try:
            runtime.symlink_to(external)
        except OSError:
            pytest.skip("symlinks not supported on this platform")

        client = make_client(roots)
        for route, body in (
            ("trace", {"path": str(walked.workspace)}),
            ("evaluations", {"path": str(walked.workspace)}),
            ("proposals", {"path": str(walked.workspace)}),
        ):
            response = client.post(
                f"/api/v1/workspaces/{route}", json=body
            )
            assert response.status_code == 403, route
            assert response.json()["error"]["code"] == (
                "API.RUNTIME_OUTSIDE_ROOTS"
            )

        # A symlinked entry inside a real runtime dir is refused too.
        runtime.unlink()
        runtime.mkdir()
        (runtime / "canon.db").symlink_to(external / "canon.db")
        response = client.post(
            "/api/v1/workspaces/trace",
            json={"path": str(walked.workspace)},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == (
            "API.RUNTIME_OUTSIDE_ROOTS"
        )

        # A directory link that stays inside the roots can still hide a
        # nested link to external state — the check traverses it.
        hidden = roots / "hidden-observations"
        hidden.mkdir()
        (hidden / "evil.jsonl").symlink_to(external / "canon.db")
        (runtime / "canon.db").unlink()
        (runtime / "observations").symlink_to(hidden)
        response = client.post(
            "/api/v1/workspaces/trace",
            json={"path": str(walked.workspace)},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == (
            "API.RUNTIME_OUTSIDE_ROOTS"
        )

    def test_path_outside_roots(self, roots, tmp_path) -> None:
        outside = apisupport.copy_example(tmp_path, "outside")
        client = make_client(roots)
        for route, body in (
            ("trace", {"path": str(outside)}),
            ("evaluations", {"path": str(outside)}),
            ("proposals", {"path": str(outside)}),
        ):
            response = client.post(
                f"/api/v1/workspaces/{route}", json=body
            )
            assert response.status_code == 403, route
            assert (
                response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"
            )

    def test_missing_workspace(self, roots) -> None:
        client = make_client(roots)
        for route, body in (
            ("trace", {}),
            ("trace/subject", {"subject": "Job/101"}),
            ("trace/run", {"run_id": "dgx-x"}),
            ("evaluations", {}),
            ("evaluations/run", {"run_id": "evl-x"}),
            ("proposals", {}),
            ("proposals/show", {"proposal_id": "x"}),
        ):
            response = client.post(
                f"/api/v1/workspaces/{route}",
                json={"path": str(roots / "nope"), **body},
            )
            assert response.status_code == 404, route
            assert (
                response.json()["error"]["code"] == "API.WORKSPACE_NOT_FOUND"
            )

    def test_no_roots(self, workspace) -> None:
        client = make_client()
        for route, body in (
            ("trace", {}),
            ("evaluations", {}),
            ("proposals", {}),
        ):
            response = client.post(
                f"/api/v1/workspaces/{route}",
                json={"path": str(workspace), **body},
            )
            assert response.status_code == 503, route
            assert (
                response.json()["error"]["code"] == "API.ROOTS_UNCONFIGURED"
            )

    def test_malformed_requests(self, roots) -> None:
        client = make_client(roots)
        bad = [
            ("trace", {}),
            ("trace", {"path": ""}),
            ("trace/subject", {"path": "x"}),
            ("trace/subject", {"path": "x", "subject": ""}),
            ("trace/subject", {"path": "x", "subject": 4}),
            ("trace/run", {"path": "x"}),
            ("trace/run", {"path": "x", "run_id": 4}),
            ("trace/run", {"path": "x", "run_id": "r" * 2000}),
            ("evaluations", {}),
            ("evaluations/run", {"path": "x"}),
            ("evaluations/run", {"path": "x", "run_id": None}),
            ("proposals", {"path": None}),
            ("proposals/show", {"path": "x"}),
            ("proposals/show", {"path": "x", "proposal_id": ""}),
            ("proposals/show", {"path": "x", "proposal_id": "p" * 2000}),
        ]
        for route, body in bad:
            response = client.post(
                f"/api/v1/workspaces/{route}", json=body
            )
            assert response.status_code == 422, (route, body)
            assert (
                response.json()["error"]["code"] == "API.MALFORMED_REQUEST"
            )


class TestDecide:
    """Issue #63 / ADR 0003: the operational surface's single write —
    a human approve/reject on a persisted proposal, recorded through
    the shared core's ``decide`` on the same ``canon.db`` the CLI uses.
    A decision made over HTTP is honored by ``ontology execute``
    exactly like a CLI decision; the API never gains an execute or
    run-trigger route."""

    ROUTE = "/api/v1/workspaces/proposals/decide"

    def _decide(self, client: TestClient, workspace: Path, **over) -> dict:
        body = {
            "path": str(workspace),
            "proposal_id": "p",
            "decision": "approved",
            "reviewer": "fde-sato",
            **over,
        }
        response = client.post(self.ROUTE, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def test_contract_lists_decide(self, roots) -> None:
        operations = make_client(roots).get("/api/v1/contract").json()[
            "operations"
        ]
        assert operations["decide"] == (
            "POST /api/v1/workspaces/proposals/decide"
        )
        # Still no run triggers or action authority anywhere.
        for op in (
            "execute", "diagnose", "project", "ingest", "evaluate",
            "approve",
        ):
            assert op not in operations

    def test_no_execute_route(self, roots, proposed) -> None:
        response = make_client(roots).post(
            "/api/v1/workspaces/execute",
            json={
                "path": str(proposed.workspace),
                "proposal_id": proposed.proposal_id,
            },
        )
        assert response.status_code == 404

    def test_approve_is_recorded_and_visible(
        self, proposed, roots
    ) -> None:
        client = make_client(roots)
        body = self._decide(
            client,
            proposed.workspace,
            proposal_id=proposed.proposal_id,
            reason="oldest uninvoiced job",
        )
        assert body["ok"] is True
        assert body["recorded"] is True
        assert body["decision"] == "approved"
        assert body["proposal_id"] == proposed.proposal_id
        assert body["decision_id"].startswith("dec-")
        assert body["diagnostics"] == []

        view = post(
            client, "proposals/show",
            path=str(proposed.workspace),
            proposal_id=proposed.proposal_id,
        )
        proposal = view["proposal"]
        assert proposal["status"] == "approved"
        assert proposal["latest_decision"]["reviewer"] == "fde-sato"
        assert proposal["latest_decision"]["reason"] == (
            "oldest uninvoiced job"
        )
        assert proposal["latest_decision"]["decision_id"] == (
            body["decision_id"]
        )
        # The decision is bound to the exact persisted payload digest.
        assert proposal["latest_decision"]["payload_digest"] == (
            proposal["payload_digest"]
        )
        assert len(proposal["decisions"]) == 1

    def test_api_decision_matches_cli_payload(
        self, proposed, roots, capsys
    ) -> None:
        """Same decide call, same payload shape as ``decide --json`` —
        plus ``api_version``."""
        body = self._decide(
            make_client(roots),
            proposed.workspace,
            proposal_id=proposed.proposal_id,
        )
        expected_keys = {
            "workspace", "ok", "decision_id", "proposal_id",
            "decision", "recorded", "diagnostics",
        }
        assert set(body) == {"api_version", *expected_keys}

    def test_reject_records_and_latest_wins(
        self, proposed, roots
    ) -> None:
        client = make_client(roots)
        first = self._decide(
            client, proposed.workspace,
            proposal_id=proposed.proposal_id,
        )
        second = self._decide(
            client, proposed.workspace,
            proposal_id=proposed.proposal_id,
            decision="rejected",
            reviewer="fde-hana",
            reason="needs more evidence",
        )
        assert second["ok"] is True
        assert second["decision"] == "rejected"
        assert second["decision_id"] != first["decision_id"]

        proposal = post(
            client, "proposals/show",
            path=str(proposed.workspace),
            proposal_id=proposed.proposal_id,
        )["proposal"]
        # Append-only: both attempts remain, latest decision wins.
        assert [d["decision"] for d in proposal["decisions"]] == [
            "approved", "rejected",
        ]
        assert proposal["status"] == "rejected"
        assert proposal["latest_decision"]["reviewer"] == "fde-hana"

    def test_api_approval_honored_by_cli_execute(
        self, proposed, roots, capsys
    ) -> None:
        """The gate is shared: an API approval unlocks
        ``ontology execute`` exactly like a CLI one."""
        self._decide(
            make_client(roots), proposed.workspace,
            proposal_id=proposed.proposal_id,
        )
        cli(capsys, "execute", str(proposed.workspace),
            proposed.proposal_id)

    def test_api_rejection_blocks_cli_execute(
        self, proposed, roots, capsys
    ) -> None:
        self._decide(
            make_client(roots), proposed.workspace,
            proposal_id=proposed.proposal_id,
            decision="rejected",
        )
        body = cli_payload(
            capsys, "execute", str(proposed.workspace),
            proposed.proposal_id, "--json",
        )
        assert body["ok"] is False
        assert any(
            d["code"] == "DECISION.NOT_APPROVED"
            for d in body["diagnostics"]
        )

    def test_undecided_proposal_refused_by_cli_execute(
        self, proposed, capsys
    ) -> None:
        body = cli_payload(
            capsys, "execute", str(proposed.workspace),
            proposed.proposal_id, "--json",
        )
        assert body["ok"] is False
        assert any(
            d["code"] == "DECISION.NOT_APPROVED"
            for d in body["diagnostics"]
        )

    def test_unknown_proposal(self, proposed, roots) -> None:
        body = self._decide(
            make_client(roots), proposed.workspace,
            proposal_id="dgx-x-p9",
        )
        assert body["ok"] is False
        assert body["recorded"] is False
        assert body["decision_id"] is None
        assert body["diagnostics"][0]["code"] == (
            "DECISION.UNKNOWN_PROPOSAL"
        )

    def test_invalid_decision(self, proposed, roots) -> None:
        body = self._decide(
            make_client(roots), proposed.workspace,
            proposal_id=proposed.proposal_id,
            decision="maybe",
        )
        assert body["ok"] is False
        assert body["recorded"] is False
        assert any(
            d["code"] == "DECISION.INVALID_DECISION"
            for d in body["diagnostics"]
        )

    def test_blank_reviewer(self, proposed, roots) -> None:
        """Whitespace slips past schema min_length and fails in the
        core, same as the CLI."""
        body = self._decide(
            make_client(roots), proposed.workspace,
            proposal_id=proposed.proposal_id,
            reviewer="   ",
        )
        assert body["ok"] is False
        assert body["recorded"] is False
        assert any(
            d["code"] == "DECISION.NO_REVIEWER"
            for d in body["diagnostics"]
        )

    def test_failed_attempts_still_trace(
        self, proposed, roots
    ) -> None:
        """A rejected decide attempt is itself auditable: the journal
        gains a ``decision.failed`` event."""
        self._decide(
            make_client(roots), proposed.workspace,
            proposal_id="dgx-x-p9",
        )
        journal = proposed.workspace / ".ontology" / "trace.jsonl"
        events = [
            json.loads(line)
            for line in journal.read_text().splitlines()
        ]
        assert any(
            e.get("stage") == "decision"
            and e.get("kind") == "decision.failed"
            and e.get("data", {}).get("proposal_id") == "dgx-x-p9"
            for e in events
        )

    def test_write_contention_is_503(
        self, proposed, roots, monkeypatch
    ) -> None:
        """Lock contention is a transport failure — 503
        ``STORAGE.WRITE_BUSY`` — never a domain diagnostic, so the
        caller knows a retry is meaningful."""
        import ontology_api.service as service_module
        from ontology_core import SqliteCanonicalStore

        class FastBusyStore(SqliteCanonicalStore):
            def __init__(self, runtime, **kwargs):
                kwargs.setdefault("busy_timeout_ms", 50)
                super().__init__(runtime, **kwargs)

        monkeypatch.setattr(
            service_module, "SqliteCanonicalStore", FastBusyStore
        )

        db = proposed.workspace / ".ontology" / "canon.db"
        holder = sqlite3.connect(db)
        holder.execute("BEGIN IMMEDIATE")
        try:
            response = make_client(roots).post(
                self.ROUTE,
                json={
                    "path": str(proposed.workspace),
                    "proposal_id": proposed.proposal_id,
                    "decision": "approved",
                    "reviewer": "fde-sato",
                },
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == (
                "STORAGE.WRITE_BUSY"
            )
        finally:
            holder.rollback()
            holder.close()

        # Retrying after the holder released the lock records normally.
        body = self._decide(
            make_client(roots), proposed.workspace,
            proposal_id=proposed.proposal_id,
        )
        assert body["ok"] is True

    def test_outside_roots(self, proposed, tmp_path) -> None:
        outside = apisupport.copy_example(tmp_path, "outside")
        response = make_client(  # no roots configured for `outside`
        ).post(
            self.ROUTE,
            json={
                "path": str(outside),
                "proposal_id": "p",
                "decision": "approved",
                "reviewer": "r",
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "API.ROOTS_UNCONFIGURED"

    def test_malformed_requests(self, roots) -> None:
        client = make_client(roots)
        bad = [
            {},
            {"path": "x"},
            {"path": "x", "proposal_id": "p"},
            {"path": "x", "proposal_id": "p", "decision": "approved"},
            {
                "path": "x", "proposal_id": "p",
                "decision": "approved", "reviewer": "",
            },
            {
                "path": "x", "proposal_id": "p",
                "decision": 5, "reviewer": "r",
            },
            {
                "path": "x", "proposal_id": "p",
                "decision": "approved", "reviewer": 5,
            },
        ]
        for body in bad:
            response = client.post(self.ROUTE, json=body)
            assert response.status_code == 422, body
            assert (
                response.json()["error"]["code"] == "API.MALFORMED_REQUEST"
            )

    def test_missing_workspace(self, roots) -> None:
        response = make_client(roots).post(
            self.ROUTE,
            json={
                "path": str(roots / "nope"),
                "proposal_id": "p",
                "decision": "approved",
                "reviewer": "r",
            },
        )
        assert response.status_code == 404
        assert (
            response.json()["error"]["code"] == "API.WORKSPACE_NOT_FOUND"
        )

    def test_runtime_symlink_escape(
        self, proposed, roots, tmp_path
    ) -> None:
        """The write endpoint refuses a planted symlink too — a decision
        must never be written through an ``.ontology`` that escapes the
        configured roots."""
        runtime = proposed.workspace / ".ontology"
        external = tmp_path / "outside-roots" / ".ontology"
        external.parent.mkdir()
        runtime.rename(external)
        try:
            runtime.symlink_to(external)
        except OSError:
            pytest.skip("symlinks not supported on this platform")

        response = make_client(roots).post(
            self.ROUTE,
            json={
                "path": str(proposed.workspace),
                "proposal_id": proposed.proposal_id,
                "decision": "approved",
                "reviewer": "fde-sato",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == (
            "API.RUNTIME_OUTSIDE_ROOTS"
        )

        # Nothing was written through the link.
        conn = sqlite3.connect(external / "canon.db")
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM decisions"
            ).fetchone()[0] == 0
        finally:
            conn.close()
