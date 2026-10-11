import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "react-router";
import type { LinksFunction } from "react-router";
import type { ReactNode } from "react";
import { ConfigProvider, Spin } from "antd";

import { WorkspaceProvider } from "./workspace/context";
import { appTheme } from "./theme";
import stylesheet from "./app.css?url";
import "antd/dist/reset.css";

export const links: LinksFunction = () => [
  { rel: "stylesheet", href: stylesheet },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

// SPA mode (ssr:false) prerenders this into index.html inside Layout;
// it is what renders before the client hydrates.
export function HydrateFallback() {
  return (
    <div role="status" aria-live="polite" className="hydrate-fallback">
      <Spin size="small" aria-hidden="true" /> Loading workspace UI…
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={appTheme}>
      <WorkspaceProvider>
        <Outlet />
      </WorkspaceProvider>
    </ConfigProvider>
  );
}
