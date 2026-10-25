# GIS track progress

One line per finished item, with key numbers. Newest at the bottom of each section.

## Done (before autonomous run)
- Step 1 convert/standardize: GDB (4 layers) + Alberta boundary -> GeoPackage EPSG:3400, wells SHP + CSV; sample 3,299 wells.
- Wave 2 QA: 22 config-driven rules, defect-log format, scorecard; FAIL stops the pipeline; 9 rule tests.
- Wave 3 liability: Petrinex rebuilt locally (June 2026: 161,098 keys / 135,154 producing, matches Fabric); 99.994% match.

## Phase A - Wave 3 follow-up
- A.1 Pad review class (DEC-014): pads = surface holes within 50 m; >=10 non-cancelled licences, none in Petrinex. Sample: 74 licences (1 pad). Full: 421 licences.
- A.2 Full run timings: stage 82 s (GDB 4 layers + references + Excel sample + Petrinex 4,248,675 WI rows), convert 135 s, QA 119-138 s, liability 82 s.
- A.2 Full QA (539,890 wells): GQA-01 13 border wells (max_failures 13, DEC-015); GQA-02 9; GQA-05 2; GQA-06 1,041 (INFO); GQA-09 2; GQA-11 28; GQA-14 8; GQA-13 162,680 text status_mode (INFO); GQA-22 341,799 vertical-only trajectories (INFO); GQA-12 0 truly degenerate; duplicates/missing 0. 539,847 of 539,890 wells (99.99%) pass all FAIL/WARN rules.
- A.2 Full liability: Petrinex producing wells (12 m) 144,255, 99.994% matched to a surface licence; June 2026 reconciles with Fabric (161,098 / 135,154). Classes: reclaimed 150,276; active reporting 148,479; abandoned 95,971; suspended 63,959; cancelled 54,430; no activity 12 m 17,445; no activity 6 m 3,268; not spudded 3,999; recently spudded 1,642; pad review 421.
- A.2 Top inactive licensees (inactive = no activity 12 m + suspended): Canadian Natural 20,269 (ratio 0.32); Cenovus 7,117 (0.54); Long Run 2,735 (0.92); Imperial 2,137 (0.37); InPlay 1,965 (0.58); AlphaBow 1,881 (1.00); Sequoia 1,657 (1.00); Whitecap 1,561; Suncor 1,071; Sunrise 1,061; Tourmaline 1,041; Karve 972; West Lake 953; Paramount 946; TAQA North 940; Baytex 897; Cardinal 885; Texcal 864; IPC Canada 745; Ovintiv 738. Full table: gis/output/full/liability/top_inactive_licensees.csv.
- A.2 Abandoned not reclaimed: 95,971 wells; most by Canadian Natural (25,661), Cenovus (6,712), Suncor (2,192); top townships 040-01W4 (596), 041-12W4 (592), 066-05W4 (576).

## Phase B - Spatial analysis
- B.0 Full analysis runtime: 464 s first version -> 169 s after vectorizing pad and township geometry (grouped multipoints, no dissolve/lambdas). Sample: 7 s.
- B.1 Townships: 5,756 (5,286 convex hulls, 470 buffers). Top 12-month oil: 076-06W4 10.06 million m3, 095-06W4 8.77 million m3, 083-06W4 6.53 million m3.
- B.2 Hexagons (5 km): 31,093 cells touching Alberta, 21,994 with wells; densest cell 1,455 wells; 13 cells with 500+ wells.
- B.3 Pads (50 m): 382,090 pads; 42,537 multi-well pads hold 141,794 wells (29.5% of drilled wells); largest 137 licences (E-T Energy, 09-13-090-10W4).
- B.4 Laterals: 534,515 wellbores - 352,676 vertical, 65,924 deviated, 115,915 horizontal (offset >= 500 m). Horizontal share by spud year: 1980 0.5%, 2000 11.9%, 2012 51.7%, 2020 79.5%, 2024 91.5%; median horizontal offset 646 m (1960) -> 1,858 m (2024).
- B.5 Reconciliation: township, hexagon and pad well counts = 481,347 drilled wells; hexagon oil = well oil (146,464,009 m3).

## Phase C - Client delivery and change management
- C.1 Client package CLI (licensee / townships / AOI; DEC-017): data.gpkg with embedded QGIS styles (verified in PyQGIS 3.44), wells.csv, data_dictionary.md (every field), README.md, .qml per layer. Top 3 by licences: Canadian Natural 119,787 wells / 82,484 pads / 136,446 QA records (112 MB); Cenovus 42,029 / 29,737 / 34,129; Suncor 16,865 / 11,232 / 11,559. Build time 97 s for all three.
- C.1 Finding: one licence (0159125) has licence_status_date 2030-04-04 -> new QA rule GQA-23 date_out_of_range (WARN, 1 of 539,890); package as-of date now ignores future dates (ST37 latest status date 2026-09-02).
- C.2 Change detection (DEC-018): snapshot (539,890 wells, 15 s) + compare + synthetic demo. Demo on full and sample: all 17 injected changes found (3 new, 3 removed, 5 status, 4 licensee transfers, 2 moved 250 m), no false positives; 3 constructed-snapshot tests.

## Phase D - Maps
- D.1 PyQGIS works (QGIS 3.44.15, python-qgis-ltr.bat). gis/alberta_wells.qgz built with 10 layers and 5 print layouts (DEC-019); maps step runs in 16-49 s.
- D.2 docs/maps/: hexagon_well_density, abandoned_not_reclaimed_by_township, liability_classes (most common class per hexagon), qa_failures (63 located FAIL/WARN failures, 7 rules), client_package_overview (Suncor). Each has title, subtitle with computed counts, legend, scale bar, north arrow, source and CRS note.
- D.2 Fixes found by looking at the PNGs: point overplotting on the class map (switched to hexagons), embedded package style overriding the map style (importNamedStyle), oversized legend.

## Phase E - Productionize
- E.1 python -m gis.run --mode sample|full: 8 steps, run_log.txt + run_manifest.json (git commit, step status/runtime, row counts, QA summary, reconciliation). gis.pipeline removed. Sample run 61 s.
- E.1 Optimizations before the final run: QA 213 -> 102 s (dedup licence/rule pairs, object-dtype isin), analysis 286 -> 127 s (numpy-backed groupby for 382,090 pads).
- E.1 --inject-bad-row demo: one well moved 50 km outside Alberta with unchanged lat/long -> GQA-03 FAIL, run stopped, exit 1, manifest status "stopped".
- E.2 Tests: 47 passing (every QA check enforced by a meta-test, DLS parser/sections, UWI -> well_key, status mapping, dates, pads, hexagons, packages, change detection, injection).
- E.3 README rewritten: goal, mermaid architecture, sources table, how to run, QA approach with the stop example, full-province findings, maps, limitations.
- E.4 Final full run 68c2fca9: success in 569 s (convert 151, QA 137, liability 143, analysis 100, packages 23, changes 6, maps 9). QA 12 PASS / 7 WARN / 4 INFO; 539,846 of 539,890 wells pass FAIL/WARN; all reconciliations PASS. Manifest records commit c762211 as dirty because the run started before the Phase E commit (b5f8e34); no code changed during the run.
- E.4 Pushed feature/gis-layer and opened PR #1 to main: https://github.com/Mona121212/Alberta-Well-Production-Analytics-Platform/pull/1

## Autonomous run 2026-10-23
- 1. GQA-01 `max_failures: 13` replaced by an allowlist of the 13 reviewed border licences, each with a reason (DEC-015 revision); any other surface hole outside Alberta fails. Inject demo now fails GQA-01 and GQA-03 (before: GQA-01 passed as WARN). 2 new tests, 49 passing.
- 2. QGIS log filter: only the exact `ERROR 6: The PNG driver does not support update access to existing datasets.` line is hidden (config `ignored_qgis_messages`, DEC-020); any other ERROR line is logged as WARNING. Sample maps run: 5 hidden, 0 other errors. 2 new tests, 51 passing.
- 3. README "Known limitations": approximate township polygons and DLS section check (no ATS layer), undecoded status codes, change detection tested only on a synthetic snapshot, screening classes are project definitions not AER criteria, plus the border allowlist caveat.
- 4. Phase E refresh: new tests for the DLS parser with project config, township key, GQA-01 allowlist and QGIS log filter (53 passing; QA rules, UWI -> well_key, status mapping, pads and change detection already covered). Sample run 149 s. Final full run 5d4aabb7 on clean commit 8bc661a: success in 487 s (convert 119, QA 95, liability 115, analysis 89, packages 39, changes 14, maps 17); GQA-01 WARN with 13 of 13 failures allowlisted, no stale entries; 539,846 of 539,890 wells pass FAIL/WARN; QA 12 PASS / 7 WARN / 4 INFO; all 10 reconciliations PASS; key numbers unchanged. README updated (runtime, tests, limitations).
- 5. docs/final_report.md: interview talking points mapped to each job requirement (at-a-glance table plus evidence and a short answer per requirement).

## Blocked
- None.
