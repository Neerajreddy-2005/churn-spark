# Surface: signed-in workbench (all routes in frontend/)

Mode: Operate. Users: a telecom retention team (manager, analyst) and one platform admin. See PRODUCT.md.

## Direction contract

THESIS: A warm workroom for retention work. The apricot frame holds the tools; the near-white sheet holds the work; cobalt marks data and the one action; red only ever means a customer at risk.

FIRST VIEWPORT: Overview shows the month's four key figures as one divided strip (each explains itself on hover/focus: formula + source run + checks), churn by tenure as cobalt bars, and the top at-risk customers one click from their page. A company with no data sees a welcome with the CSV drop zone in place.

SIGNATURE INTERACTION: The customer ID travels from its row into the customer page title (view transition), the risk meter fills, and the activity timeline settles in. Logging an action shows an Undo toast; nothing is written until the undo window passes.

WORLD: Framed sheet. Apricot frame (sidebar + top bar), sheet with 1px edge and 16px corners, no shadows at rest. Bricolage Grotesque for page titles and wordmark only; Manrope for everything else with tabular figures. Light and dark.

MOTION: Route crossfade with a short rise; staggered arrival (70 ms) of panels; bars and meters grow from zero; KPI figures count up once. Exponential ease-out. Everything off under reduced motion; content visible at rest.

REFUSE: Icon-in-circle KPI tiles, sparklines, gradients, glass, eyebrows, uppercase tracked headers, zebra tables, confirm() dialogs for reversible actions, accent colour on chrome, currency symbols.

## Unresolved

None.
