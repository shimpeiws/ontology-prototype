/**
 * Shared plumbing for the structured definition forms (#18).
 *
 * The draft YAML text in the workspace store is the single source of
 * truth: the form reads field values from a parsed Document of that text
 * and commits edits by mutating the document and writing its text back
 * with `editFile`. Changes made anywhere else — the raw DraftEditor,
 * revert, remove — re-parse the document, so the form never clobbers or
 * goes stale against the textarea below it.
 */

import { useCallback, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Document } from "yaml";

import { useWorkspace } from "../workspace/context";
import type { Diagnostic, WorkspaceFileName } from "../lib/types";
import {
  domainReferenceNames,
  parseDraftDoc,
  skeletonText,
} from "../lib/draftDoc";
import type { DomainReferences, DraftDocState } from "../lib/draftDoc";

/**
 * The file's draft document plus a mutator that emits new draft text.
 * `update` runs synchronously against the cached Document — consecutive
 * calls within one event accumulate on the same document, so multi-step
 * commits (e.g. rename + retag) never lose earlier writes.
 */
export function useDraftDoc(file: WorkspaceFileName): {
  parsed: DraftDocState;
  update: <T>(mutate: (doc: Document) => T) => T | undefined;
} {
  const { state, editFile } = useWorkspace();
  const draft = state.drafts[file] ?? null;

  const cache = useRef<{ source: string | null; parsed: DraftDocState } | null>(
    null,
  );
  if (cache.current?.source !== draft) {
    cache.current = { source: draft, parsed: parseDraftDoc(draft) };
  }
  const parsed = cache.current.parsed;

  const update = useCallback(
    <T,>(mutate: (doc: Document) => T): T | undefined => {
      const cached = cache.current;
      if (!cached || cached.parsed.status !== "ready") return undefined;
      const result = mutate(cached.parsed.doc);
      const text = cached.parsed.doc.toString();
      // Mark the emitted text as ours so the next render keeps the mutated
      // document instead of needlessly reparsing it.
      cached.source = text;
      editFile(file, text);
      return result;
    },
    [file, editFile],
  );

  return { parsed, update };
}

/** Diagnostics for one file — draft validation results when present, else
 *  the diagnostics of the active bundle (same rule as DiagnosticsList). */
export function useFileDiagnostics(file: WorkspaceFileName): Diagnostic[] {
  const { state } = useWorkspace();
  const all = state.validation
    ? state.validation.diagnostics
    : (state.workspace?.diagnostics ?? []);
  return all.filter((d) => d.file === file);
}

/** Entity/event/projection names for reference suggestions (see draftDoc). */
export function useDomainReferences(): DomainReferences {
  const { state } = useWorkspace();
  return domainReferenceNames(
    state.drafts["domain.yaml"] ?? null,
    state.workspace?.definitions?.domain ?? null,
  );
}

/** Diagnostics whose `path` lands exactly on `path`; `deep` also matches
 *  descendants (`path.foo`, `path[0]`) — for containers that render no
 *  per-child field of their own. */
export function FieldDiagnostics({
  diagnostics,
  path,
  deep = false,
}: {
  diagnostics: Diagnostic[];
  path: string;
  deep?: boolean;
}) {
  const items = diagnostics.filter(
    (d) =>
      d.path === path ||
      (deep &&
        d.path !== null &&
        (d.path.startsWith(`${path}.`) || d.path.startsWith(`${path}[`))),
  );
  if (items.length === 0) return null;
  return (
    <ul className="field-diagnostics">
      {items.map((d, i) => (
        <li key={i} className={`diagnostic severity-${d.severity}`}>
          <code className="diagnostic-code">{d.code}</code>{" "}
          <span className="diagnostic-message">{d.message}</span>
          {d.hint ? <span className="diagnostic-hint">{d.hint}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function hasErrorAt(diagnostics: Diagnostic[], path: string): boolean {
  return diagnostics.some((d) => d.path === path && d.severity === "error");
}

/**
 * Text input bound to one scalar path. Keeps the typed text while the
 * commit writes a normalized value to the doc (e.g. `4.` stays visible
 * while the draft holds `4`); external draft changes re-sync it.
 */
export function TextField({
  label,
  value,
  commit,
  diagnostics,
  path,
  datalist,
  inputMode,
}: {
  label: string;
  /** Display text of the value currently in the doc. */
  value: string;
  /** Write the typed text to the doc; returns the stored display text. */
  commit: (text: string) => string;
  diagnostics: Diagnostic[];
  /** Dotted path core diagnostics use for this field. */
  path: string;
  datalist?: { id: string; options: string[] };
  inputMode?: "decimal";
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const lastSync = useRef(value);
  if (value !== lastSync.current) {
    lastSync.current = value;
    setText(value);
  }
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        value={text}
        inputMode={inputMode}
        list={datalist?.id}
        aria-invalid={hasErrorAt(diagnostics, path) || undefined}
        onChange={(e) => {
          setText(e.target.value);
          lastSync.current = commit(e.target.value);
        }}
      />
      {datalist ? (
        <datalist id={datalist.id}>
          {datalist.options.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      ) : null}
      <FieldDiagnostics diagnostics={diagnostics} path={path} />
    </div>
  );
}

/**
 * Select bound to contract vocabulary options. An unsupported stored value
 * stays selectable (marked) so the draft is never silently rewritten; a
 * null `options` (contract metadata unavailable) degrades to a text input.
 */
export function SelectField({
  label,
  value,
  options,
  commit,
  diagnostics,
  path,
}: {
  label: string;
  value: string;
  options: string[] | null;
  commit: (text: string) => string;
  diagnostics: Diagnostic[];
  path: string;
}) {
  const id = useId();
  const unsupported = value !== "" && options !== null && !options.includes(value);
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      {options === null ? (
        <input
          id={id}
          type="text"
          value={value}
          aria-invalid={hasErrorAt(diagnostics, path) || undefined}
          onChange={(e) => commit(e.target.value)}
        />
      ) : (
        <select
          id={id}
          value={value}
          aria-invalid={hasErrorAt(diagnostics, path) || undefined}
          onChange={(e) => commit(e.target.value)}
        >
          <option value="">(unset)</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
          {unsupported ? (
            <option value={value}>{value} — unsupported</option>
          ) : null}
        </select>
      )}
      {options === null ? (
        <p className="hint">Contract vocabulary unavailable — free text.</p>
      ) : null}
      <FieldDiagnostics diagnostics={diagnostics} path={path} />
    </div>
  );
}

export function CheckField({
  label,
  checked,
  onChange,
  diagnostics,
  path,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  diagnostics: Diagnostic[];
  path: string;
}) {
  const id = useId();
  return (
    <div className="form-field form-field-check">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        aria-invalid={hasErrorAt(diagnostics, path) || undefined}
        onChange={(e) => onChange(e.target.checked)}
      />
      <label htmlFor={id}>{label}</label>
      <FieldDiagnostics diagnostics={diagnostics} path={path} />
    </div>
  );
}

export function FormSection({
  title,
  path,
  diagnostics,
  deep = false,
  action,
  children,
}: {
  title: string;
  path: string;
  diagnostics: Diagnostic[];
  deep?: boolean;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <fieldset className="form-section">
      <legend>{title}</legend>
      {action}
      <FieldDiagnostics diagnostics={diagnostics} path={path} deep={deep} />
      {children}
    </fieldset>
  );
}

/**
 * Shared gate for a definition form's draft-document states: missing file
 * (with a create-skeleton affordance) or unparseable YAML get a notice —
 * the raw DraftEditor below remains the escape hatch in both cases.
 */
export function DraftDocGate({
  file,
  kind,
  parsed,
  children,
}: {
  file: WorkspaceFileName;
  kind: "evaluation" | "agent";
  parsed: DraftDocState;
  children?: ReactNode;
}) {
  const { editFile } = useWorkspace();
  if (parsed.status === "missing") {
    return (
      <div className="state state-empty">
        <p>
          <code>{file}</code> is not in the draft — the structured form has
          nothing to edit.
        </p>
        <button type="button" onClick={() => editFile(file, skeletonText(kind))}>
          Create {file} skeleton
        </button>
      </div>
    );
  }
  if (parsed.status === "invalid") {
    return (
      <p className="hint" role="status">
        The <code>{file}</code> draft is not a readable YAML mapping (
        {parsed.message}) — fix it in the draft editor below; the structured
        form reappears once the text parses.
      </p>
    );
  }
  return <>{children}</>;
}
