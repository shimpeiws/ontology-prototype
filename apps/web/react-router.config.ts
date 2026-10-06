import type { Config } from "@react-router/dev/config";

// ADR 0001: SPA output — no runtime server rendering for the local
// authoring tool. The browser calls the Python API directly.
export default {
  ssr: false,
} satisfies Config;
