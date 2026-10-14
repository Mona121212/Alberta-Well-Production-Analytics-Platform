# Project Guide for Claude Code

## Project context
- Portfolio project: Alberta well production analytics on Microsoft Fabric.
- Data: Petrinex monthly production (12 months) + AER well data.
- Fabric workspace: `well-production-mvp.Workspace`
- Audience: hiring managers for Data Analyst / BI Developer / Junior Data Developer roles. Code must be clean, readable, and reproducible.

## Architecture
- Medallion architecture in a Fabric Lakehouse: `bronze` (raw) -> `silver` (cleaned, conformed, deduplicated) -> `gold` (star schema: fact + dimensions).
- Gold feeds a Power BI semantic model (Direct Lake), then a Power BI report.
- Only 12 months of data: use MoM and rolling 3-month measures. No YoY.

## Code style
- English only: no Chinese in code, comments, strings, logs, or print output.
- Config-driven: table names, paths, keys, and DQ rules live in config files, not hard-coded.
- PySpark for transformations; SQL allowed for Gold.
- DAX measures: clear names, organized in display folders, with descriptions.

## Required in every change
1. Data quality checks: not-null on keys, uniqueness, accepted values, referential integrity. Failed rows go to a defect/quarantine table.
2. Reconciliation tests: row counts and key sums must match across layers. Never break existing passing tests.
3. README.md: problem, architecture diagram, data sources, how to run, screenshots, next steps.
4. DECISIONS.md: Context / Decision / Alternatives / Consequences for each significant choice.

## How to work with Fabric
- Git (GitHub) is the source of truth. The workspace is connected via Fabric Git integration.
- Fabric CLI (`fab`) is installed and authenticated.
- Inspect: `fab ls well-production-mvp.Workspace`
- Run: `fab job run "well-production-mvp.Workspace/<name>.Notebook"`, then check output and fix errors.
- After editing files locally: commit and push, then ask the user to click Source control -> Update all in Fabric before running jobs.
- Semantic model is stored as TMDL files in the repo; edit those for measures, relationships, and tables.

## Workflow rules
- Before writing code, state the plan briefly and list files to be changed.
- Never delete Lakehouse tables or workspace items without explicit approval.
- Keep commits small with clear messages (feat:, fix:, docs:, test:).
- Update DECISIONS.md whenever a design choice is made.
