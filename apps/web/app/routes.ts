import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("workspace", "routes/workspace.tsx", [
    index("routes/workspace.overview.tsx"),
    route("domain", "routes/workspace.domain.tsx"),
    route("mapping", "routes/workspace.mapping.tsx"),
    route("evaluation", "routes/workspace.evaluation.tsx"),
    route("agent", "routes/workspace.agent.tsx"),
    route("proposals", "routes/workspace.proposals.tsx"),
    route("run", "routes/workspace.run.tsx"),
    route("trace", "routes/workspace.trace.tsx"),
    route("reports", "routes/workspace.reports.tsx"),
  ]),
] satisfies RouteConfig;
