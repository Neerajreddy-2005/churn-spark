# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Designed solely for the people who would use it every day: a telecom company's customer-retention team.

- **Manager** (one per company): uploads the monthly customer CSV, watches churn and revenue at risk, manages the analysts on their team.
- **Analyst**: works the at-risk list, opens a customer, reads why the model flags them, logs retention actions (calls, offers).
- **Platform admin** (one, separate account, no company, no customer data): creates companies and their managers, reads the audit log, break-glass password resets. Requires two-factor sign-in.

Recruiters and interviewers evaluate the pipeline and functions, not the UI. Do not add portfolio explanations inside the site.

## Product Purpose

A retention workbench: each month's customer data goes through a validated pipeline (data-quality rules, reconciliation, churn model). The site then shows who is likely to leave, why, and what was done about it. Success means an analyst goes from "who's at risk" to "action logged" in a few clicks, and trusts every number.

## Positioning

Every figure traces back to a real pipeline run with data-quality and reconciliation checks. The site should be able to show where a number came from (the run and the check) rather than asking users to take it on trust.

## Operating Context

- Monthly cycle: a manager uploads a CSV, a laptop/Docker worker processes it in about 2–3 minutes, and the results appear in the site (pipeline runs page shows status and data-quality report).
- Daily work: at-risk list (filter, sort, arrow keys up/down + Enter to open), customer page (risk score, model reasons, retention actions, activity timeline), overview by month.
- Long sessions on desktop browsers; must still work at phone width.

## Capabilities and Constraints

- Vite + React + TypeScript + react-router, talking directly to Supabase (auth, Postgres with row-level security, storage, one Edge Function `admin-users`). Deployed to Netlify later.
- Roles: platform admin / manager / analyst. Managers create, deactivate, reactivate and rename analysts only.
- Dataset has no currency: show money without a currency symbol. Percentages with 2 decimals.
- Confirmed additions (2026-11-13): undo toast instead of confirm dialogs, hover on a number to explain how it was calculated and which run it came from, Ctrl+D search, customer activity timeline, friendly empty overview with the upload drop zone, light/dark following the system with a toggle.

## Brand Commitments

- Name: Churn-Spark. Warm and friendly, not a generic template or AI-looking site (user's words).
- Mango palette chosen by the user (apricot frame, cobalt blue for data and the main action, red only for risk). Recorded in DESIGN.md.
- Motion should feel special and polished (page and component transitions), and respect reduced motion.

## Evidence on Hand

Real pipeline results in Supabase and `data/evidence/*.json` (7,043 customers, 26.54% churn, AUC 0.833, risk bands high 953 / medium 1,665 / low 4,425). No testimonials, customers, logos or usage numbers exist; never invent them.

## Product Principles

1. Every number is traceable to a run; show its source on demand.
2. The most frequent job (open an at-risk customer, log an action) is one click away.
3. Red means risk and nothing else.
4. Reversible actions use undo, not confirmation dialogs. Security and admin actions stay explicit.
5. Admins see no customer data; the UI never hints otherwise.

## Accessibility & Inclusion

WCAG 2.2 AA: 4.5:1 text contrast in both themes, full keyboard use, visible focus, reduced-motion support, and status never conveyed by colour alone.
