/**
 * Contract vocabulary hook for the structured forms.
 *
 * `GET /api/v1/contract` returns the core's authoritative vocabularies
 * (metrics, aggregations, units, comparators, deliverables, property
 * types). The forms consume them as select options instead of hardcoding
 * lists, so the UI can never drift from what core validation accepts.
 * A fetch failure degrades selects to free-text inputs — editing must
 * keep working when the metadata call fails.
 */

import { useEffect, useState } from "react";

import { api, ApiError } from "./api";
import type { ContractResponse } from "./types";

export interface ContractVocabulary {
  metrics: string[];
  aggregations: string[];
  units: string[];
  comparators: string[];
  guardrailComparators: string[];
  deliverables: string[];
  propertyTypes: string[];
  sourceFormats: string[];
  castTypes: string[];
  /** Casts allowed on event time assignments (date/datetime). */
  timeCasts: string[];
  conditionOperators: {
    comparison: string[];
    set: string[];
    null: string[];
    logic: string[];
  };
  /** Declarative projection-selection vocabulary (#55). */
  projectionSelection: { comparators: string[]; durationUnits: string[] };
  previewLimits: { maxBytes: number; maxRecords: number } | null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** Extract the editor vocabularies from a contract response. */
export function contractVocabulary(
  contract: ContractResponse | null,
): ContractVocabulary | null {
  if (!contract) return null;
  const c = contract.contract;
  const evaluation = asRecord(c["evaluation"]);
  const ops = asRecord(c["condition_operators"]);
  const limits = asRecord(c["preview_limits"]);
  return {
    metrics: stringList(evaluation["metrics"]),
    aggregations: stringList(evaluation["aggregations"]),
    units: stringList(evaluation["units"]),
    comparators: stringList(evaluation["comparators"]),
    guardrailComparators: stringList(evaluation["guardrail_comparators"]),
    deliverables: stringList(c["deliverables"]),
    propertyTypes: stringList(c["property_types"]),
    sourceFormats: stringList(c["source_formats"]),
    castTypes: stringList(c["cast_types"]),
    timeCasts: stringList(c["time_casts"]),
    conditionOperators: {
      comparison: stringList(ops["comparison"]),
      set: stringList(ops["set"]),
      null: stringList(ops["null"]),
      logic: stringList(ops["logic"]),
    },
    projectionSelection: {
      comparators: stringList(asRecord(c["projection_selection"])["comparators"]),
      durationUnits: stringList(
        asRecord(c["projection_selection"])["duration_units"],
      ),
    },
    previewLimits:
      typeof limits["max_bytes"] === "number" &&
      typeof limits["max_records"] === "number"
        ? {
            maxBytes: limits["max_bytes"],
            maxRecords: limits["max_records"],
          }
        : null,
  };
}

// The contract is constant for a server session — cache the promise so
// both editor pages share one request. A failure clears the cache so the
// next mount retries rather than latching a transient error.
let inflight: Promise<ContractResponse> | null = null;

function loadContract(): Promise<ContractResponse> {
  inflight ??= api.contract().catch((err: unknown) => {
    inflight = null;
    throw err;
  });
  return inflight;
}

/** Test hook: drop the module-level cache between tests. */
export function clearContractCache(): void {
  inflight = null;
}

export function useContract(): {
  contract: ContractResponse | null;
  error: ApiError | null;
} {
  const [result, setResult] = useState<{
    contract: ContractResponse | null;
    error: ApiError | null;
  }>({ contract: null, error: null });

  useEffect(() => {
    let cancelled = false;
    loadContract().then(
      (contract) => {
        if (!cancelled) setResult({ contract, error: null });
      },
      (err: unknown) => {
        if (cancelled) return;
        setResult({
          contract: null,
          error:
            err instanceof ApiError
              ? err
              : new ApiError("WEB.UNEXPECTED", String(err), null, false),
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return result;
}
