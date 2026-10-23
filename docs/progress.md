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

## Phase E - Productionize

## Blocked
