"""API integration checks for the local workspace endpoints.

Covers the issue #14 validation surface: valid/invalid configurations,
field-level diagnostics, missing workspaces, malformed requests, and
access outside configured roots — with results compared against direct
core calls.
"""

from __future__ import annotations

from pathlib import Path

import apisupport
import pytest
from fastapi.testclient import TestClient

from ontology_api import (
    ApiConfig,
    ApiError,
    DraftResult,
    OpenResult,
    __version__,
    create_app,
)
from ontology_core import bundle_digest, load_workspace, load_workspace_texts


def make_client(*roots: Path, service=None) -> TestClient:
    return TestClient(
        create_app(ApiConfig(workspace_roots=tuple(roots)), service=service)
    )


def codes(diagnostics: list[dict]) -> list[str]:
    return [d["code"] for d in diagnostics]


class TestContract:
    def test_contract_metadata(self) -> None:
        client = make_client()
        response = client.get("/api/v1/contract")
        assert response.status_code == 200
        body = response.json()
        assert body["api_version"] == "v1"
        assert body["schema_version"] == 1
        assert body["workspace_files"] == {
            "domain": "domain.yaml",
            "mapping": "mapping.yaml",
            "evaluation": "evaluation.yaml",
            "agent": "agent.yaml",
        }
        contract = body["contract"]
        assert "ref" in contract["property_types"]
        assert contract["evaluation"]["comparators"] == ["lt", "lte", "gt", "gte"]
        assert "action_proposals" in contract["deliverables"]
        assert sorted(contract["condition_operators"]["comparison"]) == [
            "eq",
            "gt",
            "gte",
            "lt",
            "lte",
            "ne",
        ]

    def test_contract_needs_no_roots(self) -> None:
        # Metadata is readable even with nothing configured.
        assert make_client().get("/api/v1/contract").status_code == 200


class TestOpen:
    def test_open_valid_workspace(self, roots: Path, workspace: Path) -> None:
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["api_version"] == "v1"
        assert body["valid"] is True
        assert body["diagnostics"] == []
        assert body["workspace"]["path"] == str(workspace.resolve())
        assert set(body["files"]) == {
            "domain.yaml",
            "mapping.yaml",
            "evaluation.yaml",
            "agent.yaml",
        }
        assert set(body["definitions"]) == {
            "domain",
            "mapping",
            "evaluation",
            "agent",
        }
        assert "Job" in body["definitions"]["domain"]["entities"]
        assert body["workspace"]["digest"] == bundle_digest(
            apisupport.workspace_texts(workspace)
        )

    def test_open_parity_with_core_invalid(
        self, roots: Path, workspace: Path
    ) -> None:
        apisupport.rewrite(
            workspace,
            "domain",
            lambda d: d["domain"]["entities"]["Job"]["properties"][
                "assignee"
            ].update(ref="Nobody"),
        )
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert body["definitions"] is None
        core = load_workspace(workspace)
        assert body["diagnostics"] == [d.to_dict() for d in core.diagnostics]
        assert any(
            d["path"] == "domain.entities.Job.properties.assignee.ref"
            for d in body["diagnostics"]
        )

    def test_open_missing_workspace(self, roots: Path) -> None:
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(roots / "nope")}
        )
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "API.WORKSPACE_NOT_FOUND"
        assert "diagnostics" not in body

    def test_open_rejects_path_outside_roots(
        self, roots: Path, tmp_path: Path
    ) -> None:
        outside = apisupport.copy_example(tmp_path, "outside")
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(outside)}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"

    def test_open_rejects_dotdot_escape(
        self, roots: Path, tmp_path: Path
    ) -> None:
        outside = apisupport.copy_example(tmp_path, "escapee")
        client = make_client(roots)
        sneaky = str(roots / ".." / ".." / outside.name)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": sneaky}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"

    def test_open_rejects_symlink_escape(
        self, roots: Path, tmp_path: Path
    ) -> None:
        outside = apisupport.copy_example(tmp_path, "symlink-target")
        link = roots / "link-workspace"
        link.symlink_to(outside, target_is_directory=True)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(link)}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"

    def test_open_rejects_root_prefix_lookalike(
        self, roots: Path, tmp_path: Path
    ) -> None:
        # /roots/workspaces must not match /roots/workspaces-evil
        lookalike_root = roots.parent / (roots.name + "-evil")
        lookalike_root.mkdir()
        evil = apisupport.copy_example(lookalike_root, "evil")
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(evil)}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"

    def test_open_digest_is_byte_exact(
        self, roots: Path, workspace: Path
    ) -> None:
        # CRLF bytes must survive: the returned files and the digest are
        # computed over the exact bytes on disk, not a newline-normalized
        # form — otherwise a draft digest could never match an open one.
        domain = workspace / "domain.yaml"
        domain.write_bytes(domain.read_bytes().replace(b"\n", b"\r\n"))
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        )
        assert response.status_code == 200
        body = response.json()
        assert "\r\n" in body["files"]["domain.yaml"]
        disk_texts = {
            name: (workspace / name).read_bytes().decode("utf-8")
            for name in (
                "domain.yaml",
                "mapping.yaml",
                "evaluation.yaml",
                "agent.yaml",
            )
        }
        assert body["workspace"]["digest"] == bundle_digest(disk_texts)

    def test_open_does_not_disclose_file_symlinked_outside_root(
        self, roots: Path, workspace: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret.yaml"
        secret.write_text("schema_version: 1\nkind: agent\nagent: {}\n")
        (workspace / "agent.yaml").unlink()
        (workspace / "agent.yaml").symlink_to(secret)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        )
        assert response.status_code == 200
        body = response.json()
        assert "agent.yaml" not in body["files"]
        # Part of the on-disk bundle was withheld, so no digest that
        # claims to represent it can be returned.
        assert body["workspace"]["digest"] is None

    def test_open_relative_path_resolves_against_root(
        self, roots: Path, workspace: Path, tmp_path: Path, monkeypatch
    ) -> None:
        # Client paths are resolved against the configured roots, never
        # against the server's working directory.
        monkeypatch.chdir(tmp_path)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": workspace.name}
        )
        assert response.status_code == 200
        assert response.json()["workspace"]["path"] == str(workspace.resolve())

    def test_open_missing_relative_path_is_not_found(
        self, roots: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/open", json={"path": "nope"}
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "API.WORKSPACE_NOT_FOUND"

    def test_open_malformed_requests(self, roots: Path) -> None:
        client = make_client(roots)
        for payload in ({}, {"path": ""}, {"path": 42}, {"path": None}):
            response = client.post("/api/v1/workspaces/open", json=payload)
            assert response.status_code == 422, payload
            assert response.json()["error"]["code"] == "API.MALFORMED_REQUEST"

    def test_no_roots_configured(self, workspace: Path) -> None:
        client = make_client()
        response = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "API.ROOTS_UNCONFIGURED"


class TestDraftValidate:
    def test_validate_draft_valid(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["diagnostics"] == []
        assert body["definitions"]["agent"]["name"] == "billing-delay-diagnosis"
        # The draft digest of identical content equals the on-disk digest.
        opened = client.post(
            "/api/v1/workspaces/open", json={"path": str(workspace)}
        ).json()
        assert body["digest"] == opened["workspace"]["digest"]

    def test_validate_draft_parity_with_core(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        broken = files["evaluation.yaml"].replace("median", "bogus")
        files["evaluation.yaml"] = broken
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        core = load_workspace_texts(files, path=workspace)
        assert body["diagnostics"] == [d.to_dict() for d in core.diagnostics]

    def test_validate_draft_missing_files(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        del files["agent.yaml"]
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert "WORKSPACE.MISSING_FILE" in codes(body["diagnostics"])

    def test_validate_draft_malformed_yaml(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        files["mapping.yaml"] = "mapping: [unclosed"
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert "WORKSPACE.YAML_ERROR" in codes(body["diagnostics"])

    def test_validate_draft_rejects_unknown_file(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        files["evil.yaml"] = "x: 1"
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "API.UNKNOWN_WORKSPACE_FILE"

    def test_validate_draft_outside_root(
        self, roots: Path, workspace: Path, tmp_path: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(tmp_path), "files": files},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"

    def test_validate_draft_no_roots(self, workspace: Path) -> None:
        client = make_client()
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": "anything", "files": {}},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "API.ROOTS_UNCONFIGURED"

    def test_validate_draft_malformed_request(self, roots: Path) -> None:
        client = make_client(roots)
        bad_payloads = [
            {},
            {"path": "x"},
            {"path": "x", "files": {"domain.yaml": 42}},
            {"path": "x", "files": "not-a-map"},
        ]
        for payload in bad_payloads:
            response = client.post(
                "/api/v1/workspaces/drafts/validate", json=payload
            )
            assert response.status_code == 422, payload
            assert response.json()["error"]["code"] == "API.MALFORMED_REQUEST"

    def test_validate_draft_nonexistent_path_is_identity_only(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(roots / "not-yet-on-disk"), "files": files},
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_validate_draft_has_no_side_effects(
        self, roots: Path, workspace: Path
    ) -> None:
        files = apisupport.workspace_texts(workspace)
        before = {
            p.relative_to(workspace): p.read_bytes()
            for p in sorted(workspace.rglob("*"))
            if p.is_file()
        }
        client = make_client(roots)
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": str(workspace), "files": files},
        )
        assert response.status_code == 200
        after = {
            p.relative_to(workspace): p.read_bytes()
            for p in sorted(workspace.rglob("*"))
            if p.is_file()
        }
        assert before == after
        assert not (workspace / ".ontology").exists()


class TestServiceInjection:
    def test_injected_service_drives_responses(self, roots: Path) -> None:
        class StubService:
            def open(self, path: str) -> OpenResult:
                return OpenResult(
                    path=Path("/stub"),
                    valid=True,
                    diagnostics=[],
                    files={"domain.yaml": "stub"},
                    digest="stub-digest",
                    definitions=None,
                )

            def validate_draft(
                self, path: str, files: dict[str, str]
            ) -> DraftResult:
                return DraftResult(
                    path=Path("/stub"),
                    valid=False,
                    diagnostics=[],
                    digest="stub-draft",
                    definitions=None,
                )

        client = make_client(service=StubService())
        response = client.post(
            "/api/v1/workspaces/open", json={"path": "/anywhere"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["workspace"]["path"] == "/stub"
        assert body["workspace"]["digest"] == "stub-digest"

    def test_injected_service_error_maps_to_http(self) -> None:
        class FailingService:
            def open(self, path: str) -> OpenResult:
                raise ApiError(403, "API.PATH_OUTSIDE_ROOTS", "nope")

            def validate_draft(
                self, path: str, files: dict[str, str]
            ) -> DraftResult:
                raise ApiError(503, "API.ROOTS_UNCONFIGURED", "none")

        client = make_client(service=FailingService())
        response = client.post(
            "/api/v1/workspaces/open", json={"path": "/x"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "API.PATH_OUTSIDE_ROOTS"
        response = client.post(
            "/api/v1/workspaces/drafts/validate",
            json={"path": "/x", "files": {}},
        )
        assert response.status_code == 503
        assert (
            response.json()["error"]["code"] == "API.ROOTS_UNCONFIGURED"
        )


class TestTransport:
    def test_cors_preflight_allowed_from_localhost(
        self, roots: Path
    ) -> None:
        client = make_client(roots)
        response = client.options(
            "/api/v1/workspaces/open",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers["access-control-allow-origin"]
            == "http://localhost:5173"
        )

    def test_cors_preflight_rejected_from_foreign_origin(
        self, roots: Path
    ) -> None:
        client = make_client(roots)
        response = client.options(
            "/api/v1/workspaces/open",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in response.headers

    def test_unexpected_service_failure_returns_structured_500(
        self,
    ) -> None:
        class BoomService:
            def open(self, path: str) -> OpenResult:
                raise RuntimeError("boom")

            def validate_draft(
                self, path: str, files: dict[str, str]
            ) -> DraftResult:
                raise RuntimeError("boom")

        client = TestClient(
            create_app(
                ApiConfig(workspace_roots=()), service=BoomService()
            ),
            raise_server_exceptions=False,
        )
        response = client.post(
            "/api/v1/workspaces/open", json={"path": "/x"}
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "API.INTERNAL_ERROR"

    def test_unknown_route_returns_structured_error(
        self, roots: Path
    ) -> None:
        client = make_client(roots)
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "API.NOT_FOUND"


class TestBoundaries:
    def test_api_never_invokes_subprocess(self) -> None:
        import ontology_api

        package = Path(ontology_api.__file__).parent
        for source in package.glob("*.py"):
            assert "subprocess" not in source.read_text(encoding="utf-8")

    def test_core_has_no_api_dependency(self) -> None:
        # Packaging-level proof is the CI core-isolation job, which installs
        # core alone; this static check keeps the source boundary honest.
        import ontology_core

        package = Path(ontology_core.__file__).parent
        for source in package.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert "ontology_api" not in text
            assert "fastapi" not in text

    def test_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        monkeypatch.setenv(
            "ONTOLOGY_API_ROOTS", os.pathsep.join(["/one", "/two"])
        )
        monkeypatch.setenv("ONTOLOGY_API_HOST", "0.0.0.0")
        monkeypatch.setenv("ONTOLOGY_API_PORT", "9999")
        config = ApiConfig.from_env()
        assert config.workspace_roots == (Path("/one"), Path("/two"))
        assert config.host == "0.0.0.0"
        assert config.port == 9999
