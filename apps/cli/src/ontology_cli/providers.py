"""Diagnosis provider adapters for the CLI.

Adapters translate the transport-independent ``ProviderRequest`` (prompt
text + model id) into a provider call and return the model's raw text.
Provider SDK objects never cross into the core — each adapter implements
``DiagnosisProvider``.

- ``OpenRouterProvider`` calls the OpenRouter chat-completions endpoint
  over stdlib ``urllib``; the key comes only from ``OPENROUTER_API_KEY``
  and is never included in errors or stored anywhere.
- ``StubProvider`` is a deterministic local double: it answers with a
  contract-valid payload derived from the prompt's candidate block, so
  the diagnosis pipeline can be exercised without credentials or network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ontology_core import ProviderError, ProviderRequest

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "ONTOLOGY_MODEL"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 60


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the Authorization header can never be re-sent
    to a different host by urllib's default redirect handling."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError(
            f"OpenRouter attempted a redirect (HTTP {code})"
        )


class OpenRouterProvider:
    """OpenRouter chat-completions adapter (stdlib HTTP, no SDK)."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(OPENROUTER_KEY_ENV)
        if not key:
            raise ProviderError(
                f"{OPENROUTER_KEY_ENV} is not set; see docs/diagnosis.md "
                "for credential setup"
            )
        self._api_key = key
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def complete(self, request: ProviderRequest) -> str:
        body = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "response_format": {"type": "json_object"},
        }
        http_request = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with self._opener.open(
                http_request, timeout=self._timeout
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never echo the response body or request headers: they may
            # carry sensitive details.
            raise ProviderError(
                f"OpenRouter returned HTTP {exc.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(
                "OpenRouter response has no message content"
            ) from None
        if not isinstance(content, str):
            raise ProviderError("OpenRouter message content is not text")
        return content


class StubProvider:
    """Deterministic offline provider for tests and credential-free runs.

    Parses the candidate and permitted-action blocks back out of the
    rendered prompt and emits a contract-valid proposal for the first
    candidate, so the full validate-record pipeline runs without a
    network — whatever the workspace's permitted actions are.
    """

    name = "stub"

    def complete(self, request: ProviderRequest) -> str:
        candidates = _prompt_candidates(request.prompt)
        actions = _prompt_actions(request.prompt)
        proposals: list[dict[str, Any]] = []
        if candidates:
            proposal = _stub_proposal(candidates[0], actions)
            if proposal is not None:
                proposals.append(proposal)
        return json.dumps(
            {
                "explanation": (
                    "stub provider: deterministic response listing the "
                    f"{len(candidates)} candidate(s) supplied"
                ),
                "hypotheses": [
                    {
                        "statement": "stub hypothesis (unconfirmed): the job "
                        "may be awaiting a manual invoicing step",
                        "evidence": [
                            c.get("evidence", {}).get("fact_key", "")
                            for c in candidates
                        ],
                    }
                ]
                if candidates
                else [],
                "follow_up_questions": [],
                "action_proposals": proposals,
            }
        )


def _prompt_candidates(prompt: str) -> list[dict[str, Any]]:
    marker = "Candidates (JSON):\n"
    if marker not in prompt:
        return []
    tail = prompt.split(marker, 1)[1].strip()
    try:
        value = json.loads(tail)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _prompt_actions(prompt: str) -> dict[str, Any]:
    marker = "Permitted actions:\n"
    if marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1].strip().splitlines()[0]
    try:
        value = json.loads(tail)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


_PLACEHOLDERS = {
    "string": "stub",
    "integer": 0,
    "number": 0.0,
    "boolean": True,
    "date": "2026-01-01",
    "datetime": "2026-01-01T00:00:00Z",
}


def _stub_proposal(
    candidate: dict[str, Any], actions: dict[str, Any]
) -> dict[str, Any] | None:
    """Build a proposal for the first permitted action whose params the
    stub can satisfy.

    The stub can conjure the candidate's own subject id plus ref-typed
    property values (via ``property_types``); an action qualifies when it
    declares a ref param to the subject entity and every other required
    param is fillable that way or is a scalar.
    """
    subject = candidate["subject"]
    properties = candidate.get("properties", {})
    property_types = candidate.get("property_types", {})
    for name, spec in actions.items():
        params: dict[str, Any] = {}
        has_subject_ref = False
        satisfiable = True
        for pname, summary in spec.get("params", {}).items():
            required = summary.endswith("(required)")
            ptype = summary.split(" ", 1)[0].split(" —", 1)[0]
            if ptype.startswith("ref:"):
                if ptype == f"ref:{subject['entity']}":
                    params[pname] = subject["id"]
                    has_subject_ref = True
                    continue
                if not required:
                    # An optional non-subject ref can't be verified, so
                    # including a guessed value only risks rejection.
                    continue
                # A required ref to another entity: fill it from a
                # candidate property declared with that ref type.
                source = next(
                    (
                        prop
                        for prop, ptype_of in property_types.items()
                        if ptype_of == ptype and properties.get(prop)
                    ),
                    None,
                )
                if source is None and not property_types:
                    # Legacy frozen candidates carry no property_types;
                    # best-effort a string property (the core still
                    # validates whatever the stub emits).
                    source = next(
                        (
                            prop
                            for prop, val in properties.items()
                            if isinstance(val, str) and val != subject["id"]
                        ),
                        None,
                    )
                if source is not None:
                    params[pname] = properties[source]
                else:
                    satisfiable = False
                    break
            else:
                params[pname] = _PLACEHOLDERS.get(ptype, "stub")
        if satisfiable and has_subject_ref:
            return {
                "action": name,
                "params": params,
                "reason": "stub proposal for the first candidate",
            }
    return None


def resolve_provider(name: str):
    """Instantiate a provider adapter by name.

    Raises ``ProviderError`` for unknown names or missing credentials; the
    CLI surfaces that as a diagnostic rather than a traceback.
    """
    if name == "openrouter":
        return OpenRouterProvider()
    if name == "stub":
        return StubProvider()
    raise ProviderError(
        f"unknown provider {name!r}",  # pragma: no cover - argparse restricts
    )


PROVIDERS = ("openrouter", "stub")
