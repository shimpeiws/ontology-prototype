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
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

/** Extract the editor vocabularies from a contract response. */
export function contractVocabulary(
  contract: ContractResponse | null,
): ContractVocabulary | null {
  if (!contract) return null;
  const c = contract.contract;
  const evaluation =
    typeof c["evaluation"] === "object" && c["evaluation"] !== null
      ? (c["evaluation"] as Record<string, unknown>)
      : {};
  return {
    metrics: stringList(evaluation["metrics"]),
    aggregations: stringList(evaluation["aggregations"]),
    units: stringList(evaluation["units"]),
    comparators: stringList(evaluation["comparators"]),
    guardrailComparators: stringList(evaluation["guardrail_comparators"]),
    deliverables: stringList(c["deliverables"]),
    propertyTypes: stringList(c["property_types"]),
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
