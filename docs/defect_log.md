# Defect Log

Issues found while building a Bronze–Silver–Gold pipeline for Alberta well data in Microsoft Fabric. Data: Petrinex
monthly volumes for 2025-07 to 2026-06 (12 months, 6,481,961 rows) and AER ST37 well tables (658,266 and 538,629
rows). Figures cover all 12 months unless a month is named. Statistics from Silver run `024b8cef`.

The single source is `config/defect_log.yaml` (DEC-024). The table below is built from it by
`scripts/build_defect_log.py`; `load_defect_log` loads the same file into the Lakehouse table `defect_log`. Edit the
yaml, not the table. The details are written by hand; `build_defect_log.py --check` fails if a number in the yaml is
missing from its paragraph.

<!-- BEGIN GENERATED: defect table (scripts/build_defect_log.py) -->

| ID | Issue | Impact | Handling | Severity | Status |
| --- | --- | --- | --- | --- | --- |
| D-001 | Well IDs use different formats in the two sources | A direct join matches nothing | Normalization rule; 99.7% of Petrinex well keys match production_string (12 months) | medium | Fixed |
| D-002 | Some producing wells are missing from ST37 `production_string` | 0.14% of rows over 12 months, but up to 7.1% of gas and 22.7% of condensate volume in one month (June) | Combined with `BH`, kept the volume, 9 placeholder wells; cause investigated in D-009 | high | Mitigated |
| D-003 | Negative volumes in adjustment activities | Rejecting them would remove normal adjustments | Negative check applies to `PROD` rows only; no `PROD` row is negative in any month | low | Fixed |
| D-004 | Masked values (`***`) in `Volume` and `Hours` | 73 and 7,094 values cannot be cast (12 months) | Raw value kept, flagged as masked | medium | Mitigated |
| D-005 | Whitespace-only and padded values | Null checks miss them, joins fail | Trim, empty to NULL in Silver | low | Fixed |
| D-006 | My cast-failure check compared a column with itself | The check always showed 0 | Rebuilt with raw columns | medium | Fixed |
| D-007 | Exact duplicate rows in Petrinex volumes | Duplicated rows would double count volume | Reject rule `exact_duplicate`; 0 rows flagged in 12 months | low | Fixed |
| D-008 | Duplicate keys when `FromToID` is masked | Rows with a masked `FromToID` cannot be told apart by key | Warning rule `duplicate_key_masked_fromto`, rows kept | low | Mitigated |
| D-009 | Producing wells missing from `production_string` grow every month | Missing wells rise from 19 (2025-07) to 423 (2026-06); likely cause of D-002 | Recorded only; the ST37 snapshot is not older than Petrinex, cause not confirmed | medium | Open |
| D-010 | `hours_above_month_max` rejects valid November rows (daylight saving time) | 166 `PROD` rows and 3.06% of November oil volume are missing from Gold | Recorded only; the rule uses days x 24 and does not allow the extra DST hour | high | Open |

<!-- END GENERATED: defect table -->

## Details

**D-001 Well IDs.** Petrinex uses `100010206504W400`, ST37 uses `00/06-06-001-01W4/0`. After normalizing, 166,433
of 166,901 distinct Petrinex well keys (`FromToIDType = WI`) match `production_string` (99.7%). By month the match
rate falls from 99.98% (2025-07) to 99.72% (2026-06); D-009 covers that trend. June 2026: 160,641 of 161,098.
One Petrinex ID has a lowercase `w` (`100022002216w400`, 12 rows, one per month), which I uppercase. The June figure
first recorded here, 160,640, was counted before that uppercase step: the same query on the raw IDs still returns
160,640, so the difference of 1 is this well.

**D-002 Missing wells.** 446 of 144,259 producing wells (`PROD`, `WI`) were not in `production_string`. They were
only 4,076 of 2,908,768 `PROD` rows (0.14%), but carried 2.48% of gas, 5.82% of condensate and 0.67% of oil volume.
The share grows every month (D-009): gas from 0.11% (2025-07) to 7.1% (2026-06), condensate from 0.38% (2025-08) to
22.7% (2026-06). All CO2 and helium volume on `PROD` well rows comes from these wells. 437 of the 446 are in `BH`,
so `dim_well` is the union of both tables: 144,250 of 144,259 producing wells covered (99.99%). The last 9 wells
(147 fact rows) keep their volume with placeholder rows and a warning. June 2026, the month first recorded here: 423
wells, 815 of 241,705 rows, 415 in `BH`, 135,146 of 135,154 producing wells covered, 8 placeholder wells with 23
rows. Severity is high because of the volume share. _Cause not confirmed; D-009 records what was checked._

**D-003 Negative volumes.** 16,958 negative values, in six activities: `DIFF` 13,263, `INVADJ` 1,603, `LDINVADJ`
1,032, `FRAC` 947, `PROC` 58 and `SHR` 55. By month from 1,310 (2026-06) to 1,514 (2025-09). No `PROD` row is
negative in any of the 12 months. Correction: the first version of this entry said all negative values in June were
in `DIFF`, `INVADJ`, `LDINVADJ` and `FRAC`; June also has 2 in `PROC` and 2 in `SHR` (`DIFF` 1,051, `INVADJ` 125,
`FRAC` 85, `LDINVADJ` 45). I did not check the official definition of each activity.

**D-004 Masked values.** 73 `Volume` values (none in `PROD`) and 7,094 `Hours` values, of which 2,625 are in `PROD`
rows. Masked `Hours` grow every month, from 6 (each month 2025-07 to 2025-09) to 1,709 (2026-06); masked `Volume`
from 1 to 15 (2026-06). The first two months loaded (2026-05 and 2026-06) had 26, 3,075 and 1,128. Production volume
is not affected. Known limitation: the `Hours` check cannot see those 2,625 rows. _Cause of the growth not checked._

**D-005 Whitespace.** 6,449,828 of 6,481,961 `CCICode` values are NULL after trimming. That includes 284,463 values
empty from the start. 6,165,365 held only a space, per month from 500,395 (2026-06) to 527,878 (2025-10). The first
two months loaded had 1,055,638 NULL of 1,061,082. ST37 text columns are padded with trailing spaces too.

**D-006 My own check.** Spark column names are case-insensitive, so the typed `volume` column overwrote the raw
`Volume`. The check compared the column with itself. I noticed when the raw counts for 2026-05 and 2026-06 showed 26
and 3,075 failed casts against a reported 0. Fixed with `*_raw` columns. Result now: 73 and 7,094 masked, 0
unexpected.

**D-007 Exact duplicates.** The reject rule `exact_duplicate` compares all business columns and rejects every copy
after the first, so a duplicated row cannot double count volume. On the 12 months loaded it flags 0 rows, in all 5
logged runs (`dq_results`, 2026-10-03 to 2026-10-28). The rule stays as a guard and its constructed test case
passes. The data that first showed duplicates is not recorded.

**D-008 Masked `FromToID`.** When `FromToID` is `***`, several rows can share one key (`production_month`,
`ReportingFacilityID`, `ActivityID`, `ProductID`, `FromToID`): 72 rows in 36 key groups, from 0 (2025-07) to 10 per
month (2025-11, 2026-01, 2026-04). The warning rule `duplicate_key_masked_fromto` keeps them. There are no other key
duplicates: without exact duplicates and masked `FromToID`, every key is unique.

**D-009 Growing gap in `production_string`.** The producing wells missing from `production_string` (D-002) rise from
19 in 2025-07 to 423 in 2026-06, 446 in total. First hypothesis: the ST37 snapshot is older than Petrinex. Checked
read-only and rejected: the ST37 files were downloaded on 2026-09-29, and their latest spud date (2026-08-26) and
final drill date (2026-08-30) are after the last Petrinex month. What the data shows instead: 437 of the 446 wells are
in `BH`, and 415 were spudded in 2025 or 2026 (median spud date 2025-10-29). Of the producing wells spudded in 2025-07
or later, 1,683 are in `production_string` and 404 are not. New wells reach ST37 `BH` but often not yet
`production_string`. Hypothesis, not confirmed: AER adds the production string some time after a well starts
reporting production. Not fixed; check again with the next ST37 release.

**D-010 Daylight saving time.** `hours_above_month_max` rejects a row when `Hours` is above days x 24. All 509 rows it
rejects in 12 months are in 2025-11 (0 in every other month), and every one reports 721 hours against a limit of 720:
Alberta moved its clocks back on 2025-11-02, so that month had 721 hours. The rows belong to 507 wells. 282 are steam
injection rows (outside the Gold scope), but 166 are `PROD` rows, so Gold drops them: 152 oil rows with 376,518.5 m3,
3.06% of the in-scope oil volume for 2025-11, which also lowers the MoM measures for 2025-11 and 2025-12. Most
operators still report 720 hours for that month. In 2026-03 (one hour shorter) the rule cannot catch a 744, which is
one hour too many. Recorded only, not fixed.

## Known limitations

- Month gaps: `dq_month_coverage` for run `024b8cef` shows 12 consecutive months and 0 missing calendar months.
  Producing wells per month range from 135,088 (2025-08) to 135,962 (2025-11); between months, 1,309 to 2,311 wells
  are new and 1,427 to 2,220 are gone. The check only sees gaps between the first and last month loaded.
- Volumes use different units by product and are not summed across products. There is no `unit` column yet.
- Rule tests used constructed bad rows for the 4 reject rules and the unknown-well warning. The masked-value rules
  have no constructed case yet.
