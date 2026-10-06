/**
 * Fetch `GET /api/v1/contract` — the shared vocabulary the structured
 * editors need (property types, etc.). The values are authoritative core
 * constants; the Web app never hardcodes them.
 */

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import type { ContractResponse } from "./types";

export interface ContractState {
  contract: ContractResponse | null;
  error: ApiError | null;
  loading: boolean;
  retry: () => void;
}

export function useContract(): ContractState {
  const [result, setResult] = useState<{
    contract?: ContractResponse;
    error?: ApiError;
  }>({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    api
      .contract(controller.signal)
      .then((contract) => {
        if (live) setResult({ contract });
      })
      .catch((err) => {
        if (!live) return;
        setResult({
          error:
            err instanceof ApiError
              ? err
              : new ApiError("WEB.UNEXPECTED", String(err), null, false),
        });
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [attempt]);

  return {
    contract: result.contract ?? null,
    error: result.error ?? null,
    loading: !result.contract && !result.error,
    retry: useCallback(() => {
      setResult({});
      setAttempt((a) => a + 1);
    }, []),
  };
}
