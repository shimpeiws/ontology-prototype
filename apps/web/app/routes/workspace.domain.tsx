import { useMemo, useState } from "react";

import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { DomainEditor } from "../components/DomainForms";
import { ErrorState, LoadingState } from "../components/states";
import { parseDomainDraft } from "../lib/domainEdit";
import { diagnosticsForFile } from "../lib/diagnostics";
import { useContract } from "../lib/useContract";
import type { WorkspaceFileName } from "../lib/types";

export function meta() {
  return [{ title: "ontology — domain" }];
}

export default function DomainPage() {
  const { state, editFile } = useWorkspace();
  const contract = useContract();
  const [editError, setEditError] = useState<string | null>(null);

  const draftText = state.drafts["domain.yaml"] ?? null;
  const parsed = useMemo(
    () =>
      draftText === null
        ? {
            view: null,
            problem:
              "domain.yaml is not in the draft — restore it with the raw editor below.",
          }
        : parseDomainDraft(draftText),
    [draftText],
  );

  const editDomain = (fn: (text: string) => string | null) => {
    if (draftText === null) return;
    const next = fn(draftText);
    if (next === null) {
      setEditError(
        "Could not apply the structured edit — the draft may need fixing in the raw editor first.",
      );
    } else {
      setEditError(null);
      editFile("domain.yaml", next);
    }
  };

  const editFiles = (files: Partial<Record<WorkspaceFileName, string>>) => {
    for (const [file, text] of Object.entries(files)) {
      editFile(file as WorkspaceFileName, text);
    }
  };

  // Property types come from the API contract (core constants) — never a
  // hardcoded client copy.
  const propertyTypes = (() => {
    const pt = contract.contract?.contract?.property_types;
    return Array.isArray(pt)
      ? pt.filter((t): t is string => typeof t === "string")
      : [];
  })();

  return (
    <DefinitionPage
      title="Domain"
      file="domain.yaml"
      requireDefinitions={false}
    >
      {contract.error ? (
        <ErrorState error={contract.error} onRetry={contract.retry} />
      ) : null}
      {editError ? (
        <p className="field-error" role="alert">
          {editError}
        </p>
      ) : null}
      {parsed.view ? (
        <DomainEditor
          view={parsed.view}
          diagnostics={diagnosticsForFile(state, "domain.yaml")}
          propertyTypes={propertyTypes}
          editDomain={editDomain}
          editFiles={editFiles}
        />
      ) : (
        <p className="hint">
          Structured editor unavailable: {parsed.problem}
        </p>
      )}
      {contract.loading && parsed.view ? (
        <LoadingState label="Loading contract vocabulary…" />
      ) : null}
    </DefinitionPage>
  );
}
