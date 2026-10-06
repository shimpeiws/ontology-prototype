import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "react-router";
import type { LinksFunction } from "react-router";

import { WorkspaceProvider } from "./workspace/context";
import stylesheet from "./app.css?url";

export const links: LinksFunction = () => [
  { rel: "stylesheet", href: stylesheet },
];

// SPA mode (ssr:false) prerenders this shell into index.html; the
// HydratedFallback is what renders before the client hydrates.
export function HydratedFallback() {
  return (
    <p role="status" aria-live="polite">
      Loading workspace UI…
    </p>
  );
}

export default function App() {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        <WorkspaceProvider>
          <Outlet />
        </WorkspaceProvider>
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}
