/**
 * Structured domain editor: the inspectable list, the per-concept forms,
 * and the rename/remove blast-radius confirmation.
 *
 * Everything here renders the *parsed draft* of domain.yaml — the same
 * text the raw editor edits and the graph visualizes (INV-003). Edits go
 * through `editDomain`/`editFiles`, which only mutate the draft layer;
 * nothing is saved, ingested, or activated (INV-001).
 *
 * Scalar fields (type, required, ref, subject, description) commit
 * immediately. Renames and removals are discrete actions gated behind the
 * reference-impact panel (INV-005).
 */

import { useState } from "react";

import { useWorkspace } from "../workspace/context";
import {
  addEntity,
  addEvent,
  addProperty,
  removeConcept,
  renameDeclaration,
  setDomainMeta,
  setPropertyField,
  setSpecDescription,
  setSubject,
} from "../lib/domainEdit";
import type {
  Concept,
  DomainDraftView,
  EntityDraftView,
  EventDraftView,
  ProjectionDraftView,
  PropertyDraftView,
  OwnerKind,
} from "../lib/domainEdit";
import { applyRename, findReferences } from "../lib/references";
import type { ReferenceHit } from "../lib/references";
import { diagnosticsAtPath } from "../lib/diagnostics";
import type { Diagnostic, WorkspaceFileName } from "../lib/types";
import { EmptyState } from "./states";
import { FieldDiagnostics } from "./DiagnosticsList";
import { DomainGraph } from "./DomainGraph";
import type { GraphSelection } from "./DomainGraph";

export type DomainSelection = GraphSelection | null;

interface PendingChange {
  action: "rename" | "remove";
  concept: Concept;
  newName?: string;
  hits: ReferenceHit[];
  skipped: WorkspaceFileName[];
}

export interface DomainEditorProps {
  view: DomainDraftView;
  /** Diagnostics for domain.yaml plus bundle-level ones. */
  diagnostics: Diagnostic[];
  /** Contract vocabulary; empty when the contract could not be fetched. */
  propertyTypes: string[];
  /** Apply a mutation to the domain.yaml draft text. */
  editDomain: (fn: (text: string) => string | null) => void;
  /** Apply already-rendered draft texts across files (cross-file rename). */
  editFiles: (files: Partial<Record<WorkspaceFileName, string>>) => void;
}

function conceptLabel(concept: Concept): string {
  return concept.kind === "property"
    ? `property ${concept.owner}.${concept.name}`
    : `${concept.kind} ${concept.name}`;
}

function conceptToSelection(concept: Concept): DomainSelection {
  return concept.kind === "property"
    ? null
    : { kind: concept.kind, name: concept.name };
}

/* ---- blast-radius confirmation ---- */

export function ImpactPanel({
  pending,
  onConfirm,
  onCancel,
}: {
  pending: PendingChange;
  /** For renames, `updateRefs` says whether references are retargeted too. */
  onConfirm: (updateRefs: boolean) => void;
  onCancel: () => void;
}) {
  const [updateRefs, setUpdateRefs] = useState(true);
  const isRename = pending.action === "rename";
  return (
    <div
      className="impact-panel"
      role="alertdialog"
      aria-labelledby="impact-panel-title"
    >
      <p id="impact-panel-title">
        <strong>
          {isRename
            ? `Rename ${conceptLabel(pending.concept)} to '${pending.newName}'`
            : `Remove ${conceptLabel(pending.concept)}`}
        </strong>
      </p>
      {pending.hits.length > 0 ? (
        <>
          <p>
            {pending.hits.length} reference
            {pending.hits.length === 1 ? "" : "s"} in the draft bundle:
          </p>
          <ul className="impact-list">
            {pending.hits.map((h, i) => (
              <li key={i}>
                <code>{h.file}</code> — <code>{h.path}</code>: {h.label}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No references to this {pending.concept.kind} in the draft bundle.</p>
      )}
      {pending.skipped.length > 0 ? (
        <p className="field-error">
          Could not scan {pending.skipped.join(", ")} (unparseable draft) — it
          may contain additional references.
        </p>
      ) : null}
      {isRename && pending.hits.length > 0 ? (
        <label className="impact-option">
          <input
            type="checkbox"
            checked={updateRefs}
            onChange={(e) => setUpdateRefs(e.target.checked)}
          />
          Update {pending.hits.length} reference
          {pending.hits.length === 1 ? "" : "s"} to the new name
        </label>
      ) : null}
      {!isRename && pending.hits.length > 0 ? (
        <p className="hint">
          Removing the declaration leaves these references dangling — Validate
          draft flags them until they are fixed.
        </p>
      ) : null}
      <div className="impact-actions">
        <button type="button" onClick={() => onConfirm(updateRefs)} autoFocus>
          {isRename ? "Apply rename" : "Remove"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/* ---- shared controls ---- */

function RenameControl({
  currentName,
  what,
  onRename,
}: {
  currentName: string;
  /** Used in the input's accessible label. */
  what: string;
  onRename: (newName: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(currentName);
  const inputId = `rename-${what}-${currentName.replaceAll(/[^A-Za-z0-9_]/g, "_")}`;
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}>
        Rename…
      </button>
    );
  }
  return (
    <span className="rename-control">
      <label className="sr-only" htmlFor={inputId}>
        New {what} name
      </label>
      <input
        id={inputId}
        value={value}
        autoFocus
        onChange={(e) => setValue(e.target.value)}
      />
      <button
        type="button"
        disabled={value.trim() === "" || value === currentName}
        onClick={() => {
          onRename(value);
          setOpen(false);
        }}
      >
        Apply rename
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </span>
  );
}

function DescriptionInput({
  id,
  label,
  value,
  onCommit,
}: {
  id: string;
  label: string;
  value: string | null;
  onCommit: (v: string) => void;
}) {
  return (
    <label className="field" htmlFor={id}>
      <span className="field-label">{label}</span>
      <input
        id={id}
        value={value ?? ""}
        onChange={(e) => onCommit(e.target.value)}
      />
    </label>
  );
}

/** Diagnostics for an owner node itself, excluding its `properties` subtree
 *  (those render beside the property rows). */
function ownerDiagnostics(
  diagnostics: Diagnostic[],
  pathPrefix: string,
): Diagnostic[] {
  return diagnosticsAtPath(diagnostics, pathPrefix).filter(
    (d) => !(d.path ?? "").startsWith(`${pathPrefix}.properties`),
  );
}

/* ---- property table (shared by entity and event forms) ---- */

function PropertyTable({
  ownerKind,
  ownerName,
  properties,
  entityNames,
  propertyTypes,
  diagnostics,
  onEdit,
  onRename,
  onRemove,
}: {
  ownerKind: OwnerKind;
  ownerName: string;
  properties: PropertyDraftView[];
  entityNames: string[];
  propertyTypes: string[];
  diagnostics: Diagnostic[];
  onEdit: (fn: (text: string) => string | null) => void;
  onRename: (concept: Concept, newName: string) => void;
  onRemove: (concept: Concept) => void;
}) {
  const [newProp, setNewProp] = useState("");
  const owner = { kind: ownerKind, name: ownerName };
  const pathPrefix = `domain.${ownerKind}.${ownerName}.properties`;
  const existing = new Set(properties.map((p) => p.name));

  const rowId = (prop: string, field: string) =>
    `prop-${ownerKind}-${ownerName}-${prop}-${field}`.replaceAll(
      /[^A-Za-z0-9_-]/g,
      "_",
    );

  return (
    <div className="property-editor">
      <table className="property-table">
        <thead>
          <tr>
            <th scope="col">name</th>
            <th scope="col">type</th>
            <th scope="col">required</th>
            <th scope="col">ref target</th>
            <th scope="col">description</th>
            <th scope="col">
              <span className="sr-only">actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {properties.map((p) => {
            const concept: Concept = {
              kind: "property",
              ownerKind,
              owner: ownerName,
              name: p.name,
            };
            return (
              <tr key={p.name}>
                <td>
                  <code>{p.name}</code>
                  <FieldDiagnostics
                    diagnostics={diagnosticsAtPath(
                      diagnostics,
                      `${pathPrefix}.${p.name}`,
                    ).filter(
                      (d) =>
                        !["type", "required", "ref", "description"].some(
                          (f) => d.path === `${pathPrefix}.${p.name}.${f}`,
                        ),
                    )}
                  />
                </td>
                <td>
                  <label className="sr-only" htmlFor={rowId(p.name, "type")}>
                    type of {p.name}
                  </label>
                  {propertyTypes.length > 0 ? (
                    <select
                      id={rowId(p.name, "type")}
                      value={p.type ?? ""}
                      onChange={(e) =>
                        onEdit((t) =>
                          setPropertyField(t, owner, p.name, "type", e.target.value),
                        )
                      }
                    >
                      {p.type === null || !propertyTypes.includes(p.type) ? (
                        <option value={p.type ?? ""}>
                          {p.type ?? "(unset)"}
                        </option>
                      ) : null}
                      {propertyTypes.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <code>{p.type ?? "(unset)"}</code>
                  )}
                  <FieldDiagnostics
                    diagnostics={diagnosticsAtPath(
                      diagnostics,
                      `${pathPrefix}.${p.name}.type`,
                    )}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`required: ${p.name}`}
                    checked={p.required}
                    onChange={(e) =>
                      onEdit((t) =>
                        setPropertyField(
                          t,
                          owner,
                          p.name,
                          "required",
                          e.target.checked,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  {p.type === "ref" ? (
                    <>
                      <label className="sr-only" htmlFor={rowId(p.name, "ref")}>
                        ref target of {p.name}
                      </label>
                      <select
                        id={rowId(p.name, "ref")}
                        value={p.ref ?? ""}
                        onChange={(e) =>
                          onEdit((t) =>
                            setPropertyField(
                              t,
                              owner,
                              p.name,
                              "ref",
                              e.target.value === "" ? null : e.target.value,
                            ),
                          )
                        }
                      >
                        <option value="">(choose entity)</option>
                        {p.ref !== null && !entityNames.includes(p.ref) ? (
                          <option value={p.ref}>{p.ref} (undeclared)</option>
                        ) : null}
                        {entityNames.map((n) => (
                          <option key={n} value={n}>
                            {n}
                          </option>
                        ))}
                      </select>
                    </>
                  ) : (
                    <span className="hint">—</span>
                  )}
                  <FieldDiagnostics
                    diagnostics={diagnosticsAtPath(
                      diagnostics,
                      `${pathPrefix}.${p.name}.ref`,
                    )}
                  />
                </td>
                <td>
                  <label className="sr-only" htmlFor={rowId(p.name, "desc")}>
                    description of {p.name}
                  </label>
                  <input
                    id={rowId(p.name, "desc")}
                    value={p.description ?? ""}
                    onChange={(e) =>
                      onEdit((t) =>
                        setPropertyField(
                          t,
                          owner,
                          p.name,
                          "description",
                          e.target.value,
                        ),
                      )
                    }
                  />
                </td>
                <td className="prop-actions">
                  <RenameControl
                    key={p.name}
                    currentName={p.name}
                    what="property"
                    onRename={(n) => onRename(concept, n)}
                  />
                  <button type="button" onClick={() => onRemove(concept)}>
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="add-row">
        <label className="sr-only" htmlFor={rowId("new", "name")}>
          New property name on {ownerName}
        </label>
        <input
          id={rowId("new", "name")}
          placeholder="new property name"
          value={newProp}
          onChange={(e) => setNewProp(e.target.value)}
        />
        <button
          type="button"
          disabled={newProp.trim() === "" || existing.has(newProp)}
          onClick={() => {
            const name = newProp;
            onEdit((t) => addProperty(t, owner, name));
            setNewProp("");
          }}
        >
          Add property
        </button>
      </div>
    </div>
  );
}

/* ---- per-concept forms ---- */

function EntityForm({
  entity,
  view,
  diagnostics,
  propertyTypes,
  onEdit,
  onRename,
  onRemove,
}: {
  entity: EntityDraftView;
  view: DomainDraftView;
  diagnostics: Diagnostic[];
  propertyTypes: string[];
  onEdit: DomainEditorProps["editDomain"];
  onRename: (concept: Concept, newName: string) => void;
  onRemove: (concept: Concept) => void;
}) {
  const concept: Concept = { kind: "entity", name: entity.name };
  const prefix = `domain.entities.${entity.name}`;
  return (
    <div className="concept-form">
      <p className="concept-form-head">
        <strong>Entity {entity.name}</strong>{" "}
        <RenameControl
          key={entity.name}
          currentName={entity.name}
          what="entity"
          onRename={(n) => onRename(concept, n)}
        />
        <button type="button" onClick={() => onRemove(concept)}>
          Remove entity
        </button>
      </p>
      <DescriptionInput
        id={`entity-${entity.name}-desc`}
        label="description"
        value={entity.description}
        onCommit={(v) =>
          onEdit((t) => setSpecDescription(t, "entities", entity.name, v))
        }
      />
      <FieldDiagnostics diagnostics={ownerDiagnostics(diagnostics, prefix)} />
      <h4>Properties</h4>
      <PropertyTable
        ownerKind="entities"
        ownerName={entity.name}
        properties={entity.properties}
        entityNames={view.entities.map((e) => e.name)}
        propertyTypes={propertyTypes}
        diagnostics={diagnostics}
        onEdit={onEdit}
        onRename={onRename}
        onRemove={onRemove}
      />
    </div>
  );
}

function EventForm({
  event,
  view,
  diagnostics,
  propertyTypes,
  onEdit,
  onRename,
  onRemove,
}: {
  event: EventDraftView;
  view: DomainDraftView;
  diagnostics: Diagnostic[];
  propertyTypes: string[];
  onEdit: DomainEditorProps["editDomain"];
  onRename: (concept: Concept, newName: string) => void;
  onRemove: (concept: Concept) => void;
}) {
  const concept: Concept = { kind: "event", name: event.name };
  const prefix = `domain.events.${event.name}`;
  const subjectId = `event-${event.name}-subject`;
  const entityNames = view.entities.map((e) => e.name);
  return (
    <div className="concept-form">
      <p className="concept-form-head">
        <strong>Event {event.name}</strong>{" "}
        <RenameControl
          key={event.name}
          currentName={event.name}
          what="event"
          onRename={(n) => onRename(concept, n)}
        />
        <button type="button" onClick={() => onRemove(concept)}>
          Remove event
        </button>
      </p>
      <label className="field" htmlFor={subjectId}>
        <span className="field-label">subject</span>
        <select
          id={subjectId}
          value={event.subject ?? ""}
          onChange={(e) =>
            onEdit((t) =>
              setSubject(
                t,
                "events",
                event.name,
                e.target.value === "" ? null : e.target.value,
              ),
            )
          }
        >
          <option value="">(choose entity)</option>
          {event.subject !== null && !entityNames.includes(event.subject) ? (
            <option value={event.subject}>{event.subject} (undeclared)</option>
          ) : null}
          {entityNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <DescriptionInput
        id={`event-${event.name}-desc`}
        label="description"
        value={event.description}
        onCommit={(v) =>
          onEdit((t) => setSpecDescription(t, "events", event.name, v))
        }
      />
      <FieldDiagnostics diagnostics={ownerDiagnostics(diagnostics, prefix)} />
      <h4>Properties</h4>
      <PropertyTable
        ownerKind="events"
        ownerName={event.name}
        properties={event.properties}
        entityNames={entityNames}
        propertyTypes={propertyTypes}
        diagnostics={diagnostics}
        onEdit={onEdit}
        onRename={onRename}
        onRemove={onRemove}
      />
    </div>
  );
}

function ProjectionForm({
  projection,
  view,
  diagnostics,
  onEdit,
  onRename,
  onRemove,
}: {
  projection: ProjectionDraftView;
  view: DomainDraftView;
  diagnostics: Diagnostic[];
  onEdit: DomainEditorProps["editDomain"];
  onRename: (concept: Concept, newName: string) => void;
  onRemove: (concept: Concept) => void;
}) {
  const concept: Concept = { kind: "projection", name: projection.name };
  const prefix = `domain.projections.${projection.name}`;
  const subjectId = `projection-${projection.name}-subject`;
  const entityNames = view.entities.map((e) => e.name);
  return (
    <div className="concept-form">
      <p className="concept-form-head">
        <strong>Projection {projection.name}</strong>{" "}
        <RenameControl
          key={projection.name}
          currentName={projection.name}
          what="projection"
          onRename={(n) => onRename(concept, n)}
        />
        <button type="button" onClick={() => onRemove(concept)}>
          Remove projection
        </button>
      </p>
      <label className="field" htmlFor={subjectId}>
        <span className="field-label">subject</span>
        <select
          id={subjectId}
          value={projection.subject ?? ""}
          onChange={(e) =>
            onEdit((t) =>
              setSubject(
                t,
                "projections",
                projection.name,
                e.target.value === "" ? null : e.target.value,
              ),
            )
          }
        >
          <option value="">(choose entity)</option>
          {projection.subject !== null &&
          !entityNames.includes(projection.subject) ? (
            <option value={projection.subject}>
              {projection.subject} (undeclared)
            </option>
          ) : null}
          {entityNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <DescriptionInput
        id={`projection-${projection.name}-desc`}
        label="description"
        value={projection.description}
        onCommit={(v) =>
          onEdit((t) =>
            setSpecDescription(t, "projections", projection.name, v),
          )
        }
      />
      <FieldDiagnostics diagnostics={ownerDiagnostics(diagnostics, prefix)} />
    </div>
  );
}

/* ---- add-concept row ---- */

function AddRow({
  id,
  placeholder,
  buttonLabel,
  existing,
  onAdd,
}: {
  id: string;
  placeholder: string;
  buttonLabel: string;
  existing: string[];
  onAdd: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return (
    <div className="add-row">
      <label className="sr-only" htmlFor={id}>
        {placeholder}
      </label>
      <input
        id={id}
        placeholder={placeholder}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <button
        type="button"
        disabled={name.trim() === "" || existing.includes(name)}
        onClick={() => {
          onAdd(name);
          setName("");
        }}
      >
        {buttonLabel}
      </button>
    </div>
  );
}

/* ---- the composed editor ---- */

export function DomainEditor({
  view,
  diagnostics,
  propertyTypes,
  editDomain,
  editFiles,
}: DomainEditorProps) {
  const { state } = useWorkspace();
  const [selection, setSelection] = useState<DomainSelection>(null);
  const [pending, setPending] = useState<PendingChange | null>(null);

  const entityNames = view.entities.map((e) => e.name);

  const requestRename = (concept: Concept, newName: string) => {
    const { hits, skipped } = findReferences(state.drafts, concept);
    if (hits.length === 0 && skipped.length === 0) {
      confirmRename(concept, newName, false);
      return;
    }
    setPending({ action: "rename", concept, newName, hits, skipped });
  };

  const requestRemove = (concept: Concept) => {
    const { hits, skipped } = findReferences(state.drafts, concept);
    setPending({ action: "remove", concept, hits, skipped });
  };

  const confirmRename = (
    concept: Concept,
    newName: string,
    updateRefs: boolean,
  ) => {
    if (updateRefs) {
      const result = applyRename(state.drafts, concept, newName);
      editFiles(result.files);
    } else {
      editDomain((t) => renameDeclaration(t, concept, newName));
    }
    // If the renamed concept is selected, follow it to the new name.
    setSelection((sel) =>
      sel && conceptToSelection(concept)?.kind === sel.kind &&
        sel.name === concept.name
        ? { kind: sel.kind, name: newName }
        : sel,
    );
  };

  const confirmPending = (updateRefs: boolean) => {
    if (!pending) return;
    if (pending.action === "rename" && pending.newName !== undefined) {
      confirmRename(pending.concept, pending.newName, updateRefs);
    } else {
      editDomain((t) => removeConcept(t, pending.concept));
      setSelection((sel) =>
        conceptToSelection(pending.concept)?.name === sel?.name &&
        conceptToSelection(pending.concept)?.kind === sel?.kind
          ? null
          : sel,
      );
    }
    setPending(null);
  };

  const selectedEntity =
    selection?.kind === "entity"
      ? view.entities.find((e) => e.name === selection.name)
      : undefined;
  const selectedEvent =
    selection?.kind === "event"
      ? view.events.find((e) => e.name === selection.name)
      : undefined;
  const selectedProjection =
    selection?.kind === "projection"
      ? view.projections.find((p) => p.name === selection.name)
      : undefined;

  const countBadge = (prefix: string) => {
    const n = diagnosticsAtPath(diagnostics, prefix).filter(
      (d) => d.severity === "error",
    ).length;
    return n > 0 ? (
      <span className="badge badge-invalid" title={`${n} error diagnostic(s)`}>
        {n}
      </span>
    ) : null;
  };

  return (
    <div className="domain-editor">
      <section aria-labelledby="domain-meta-heading" className="domain-meta">
        <h2 id="domain-meta-heading">Domain</h2>
        <label className="field" htmlFor="domain-name-input">
          <span className="field-label">name</span>
          <input
            id="domain-name-input"
            value={view.name ?? ""}
            onChange={(e) =>
              editDomain((t) => setDomainMeta(t, "name", e.target.value))
            }
          />
        </label>
        <DescriptionInput
          id="domain-desc-input"
          label="description"
          value={view.description}
          onCommit={(v) => editDomain((t) => setDomainMeta(t, "description", v))}
        />
        {view.policies ? (
          <dl className="definition-facts">
            <dt>entity_id_type</dt>
            <dd>
              <code>{view.policies.entityIdType ?? "(unset)"}</code>
            </dd>
            <dt>event times</dt>
            <dd>
              <code>
                {view.policies.occurrenceField ?? "?"}/
                {view.policies.observationField ?? "?"} ·{" "}
                {view.policies.dateOnlyTimezone ?? "?"}
              </code>
            </dd>
          </dl>
        ) : null}
        <FieldDiagnostics
          diagnostics={diagnosticsAtPath(diagnostics, "domain").filter(
            (d) =>
              !["entities", "events", "projections"].some((k) =>
                (d.path ?? "").startsWith(`domain.${k}`),
              ),
          )}
        />
      </section>

      <section aria-labelledby="domain-graph-heading">
        <h2 id="domain-graph-heading">Model</h2>
        <DomainGraph
          view={view}
          selected={selection}
          onSelect={setSelection}
        />
      </section>

      <section aria-labelledby="entities-heading">
        <h2 id="entities-heading">Entities</h2>
        {view.entities.length === 0 ? (
          <EmptyState title="No entities defined yet." />
        ) : (
          <ul className="definition-list">
            {view.entities.map((entity) => (
              <li key={entity.name}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() =>
                    setSelection({ kind: "entity", name: entity.name })
                  }
                >
                  <strong>{entity.name}</strong>
                </button>{" "}
                {countBadge(`domain.entities.${entity.name}`)}
                {entity.description ? ` — ${entity.description}` : null}
                <ul>
                  {entity.properties.map((p) => (
                    <li key={p.name}>
                      <code>{p.name}</code>: {p.type ?? "?"}
                      {p.ref ? <code>{` → ${p.ref}`}</code> : null}
                      {p.required ? " (required)" : null}
                      {p.description ? ` — ${p.description}` : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
        <AddRow
          id="add-entity-name"
          placeholder="new entity name"
          buttonLabel="Add entity"
          existing={entityNames}
          onAdd={(name) => {
            editDomain((t) => addEntity(t, name));
            setSelection({ kind: "entity", name });
          }}
        />
      </section>

      <section aria-labelledby="events-heading">
        <h2 id="events-heading">Events</h2>
        {view.events.length === 0 ? (
          <EmptyState title="No events defined yet." />
        ) : (
          <ul className="definition-list">
            {view.events.map((event) => (
              <li key={event.name}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() =>
                    setSelection({ kind: "event", name: event.name })
                  }
                >
                  <strong>{event.name}</strong>
                </button>{" "}
                {countBadge(`domain.events.${event.name}`)} on{" "}
                <code>{event.subject ?? "(no subject)"}</code>
                {event.description ? ` — ${event.description}` : null}
              </li>
            ))}
          </ul>
        )}
        <AddRow
          id="add-event-name"
          placeholder="new event name"
          buttonLabel="Add event"
          existing={view.events.map((e) => e.name)}
          onAdd={(name) => {
            editDomain((t) => addEvent(t, name));
            setSelection({ kind: "event", name });
          }}
        />
      </section>

      <section aria-labelledby="projections-heading">
        <h2 id="projections-heading">Projections</h2>
        {view.projections.length === 0 ? (
          <EmptyState title="No projections defined yet." />
        ) : (
          <ul className="definition-list">
            {view.projections.map((p) => (
              <li key={p.name}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() =>
                    setSelection({ kind: "projection", name: p.name })
                  }
                >
                  <strong>{p.name}</strong>
                </button>{" "}
                {countBadge(`domain.projections.${p.name}`)} on{" "}
                <code>{p.subject ?? "(no subject)"}</code>
                {p.description ? ` — ${p.description}` : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {pending ? (
        <ImpactPanel
          pending={pending}
          onConfirm={confirmPending}
          onCancel={() => setPending(null)}
        />
      ) : null}

      {selection &&
      (selectedEntity || selectedEvent || selectedProjection) ? (
        <section
          aria-labelledby="concept-editor-heading"
          className="concept-editor"
        >
          <h2 id="concept-editor-heading">Edit {selection.kind}</h2>
          {selectedEntity ? (
            <EntityForm
              entity={selectedEntity}
              view={view}
              diagnostics={diagnostics}
              propertyTypes={propertyTypes}
              onEdit={editDomain}
              onRename={requestRename}
              onRemove={requestRemove}
            />
          ) : selectedEvent ? (
            <EventForm
              event={selectedEvent}
              view={view}
              diagnostics={diagnostics}
              propertyTypes={propertyTypes}
              onEdit={editDomain}
              onRename={requestRename}
              onRemove={requestRemove}
            />
          ) : selectedProjection ? (
            <ProjectionForm
              projection={selectedProjection}
              view={view}
              diagnostics={diagnostics}
              onEdit={editDomain}
              onRename={requestRename}
              onRemove={requestRemove}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
