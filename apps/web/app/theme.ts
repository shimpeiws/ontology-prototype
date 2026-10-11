import type { ThemeConfig } from "antd";

/**
 * The Web Console's single Ant Design theme configuration (#92).
 *
 * The app runs the standard light theme — every shared token the app
 * needs lives here so per-page code never carries ad-hoc theme values.
 * Keep overrides minimal; do not add per-page themes or a custom
 * design-system layer on top.
 */
export const appTheme: ThemeConfig = {
  token: {
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif",
    fontFamilyCode:
      "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      headerPadding: "0 16px",
      siderBg: "#ffffff",
    },
  },
};
