import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vitest config is intentionally separate from vite.config.ts: tests run
// component/store units in jsdom with a stubbed fetch (synthetic fixture
// mode — see app/test/fixtures.ts), not through the React Router plugin.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./app/test/setup.ts"],
    include: ["app/**/*.test.{ts,tsx}"],
  },
});
