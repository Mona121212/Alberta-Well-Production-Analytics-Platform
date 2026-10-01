# Defect Log

Issues found while building a Bronze–Silver–Gold pipeline for Alberta well data in Microsoft Fabric. Data: Petrinex monthly volumes for 2026-05 and 2026-06 (1,061,082 rows) and AER ST37 well tables (658,266 and 538,629 rows). Where a figure covers one month, it says so.

| ID    | Issue                                                          | Impact                                         | Handling                                                 | Status    |
| ----- | -------------------------------------------------------------- | ---------------------------------------------- | -------------------------------------------------------- | --------- |
| D-001 | Well IDs use different formats in the two sources              | A direct join matches nothing                  | Normalization rule, 99.99% coverage (June)               | Fixed     |
| D-002 | Some producing wells are missing from ST37 `production_string` | 0.34% of rows, but 7.1% of gas volume (June)   | Combined with `BH`, kept the volume, 8 placeholder wells | Mitigated |
| D-003 | Negative volumes in adjustment activities                      | Rejecting them would remove normal adjustments | Negative check applies to `PROD` rows only               | Fixed     |
| D-004 | Masked values (`***`) in `Volume` and `Hours`                  | 26 and 3,075 values cannot be cast             | Raw value kept, flagged as masked                        | Mitigated |
| D-005 | Whitespace-only and padded values                              | Null checks miss them, joins fail              | Trim, empty to NULL in Silver                            | Fixed     |
| D-006 | My cast-failure check compared a column with itself            | The check always showed 0                      | Rebuilt with raw columns                                 | Fixed     |

## Details

**D-001 Well IDs.** Petrinex uses `100010206504W400`, ST37 uses `00/06-06-001-01W4/0`. After normalizing, 160,640 of 161,098 Petrinex wells matched `production_string` (99.7%). Some Petrinex IDs also had a lowercase `w`, which I uppercase.

**D-002 Missing wells.** In June, 423 producing wells were not in `production_string`. They were only 815 of 241,705 `PROD` rows, but carried 7.1% of gas and 22.7% of condensate volume. 415 of them were in `BH`, so `dim_well` is the union of both tables: 135,146 of 135,154 producing wells covered (99.99%). The last 8 wells (23 rows) keep their volume with placeholder rows and a warning. _Cause not confirmed._

**D-003 Negative volumes.** 1,310 negative values in June, all in `DIFF`, `INVADJ`, `LDINVADJ` and `FRAC`. No `PROD` row is negative in either month. I did not check the official definition of each activity.

**D-004 Masked values.** 26 `Volume` values (none in `PROD`) and 3,075 `Hours` values, of which 1,128 are in `PROD` rows. Production volume is not affected. Known limitation: the `Hours` check cannot see those 1,128 rows.

**D-005 Whitespace.** 1,055,638 of 1,061,082 `CCICode` values are NULL after trimming. That includes values empty from the start. In June, 500,395 held only a space. ST37 text columns are padded with trailing spaces too.

**D-006 My own check.** Spark column names are case-insensitive, so the typed `volume` column overwrote the raw `Volume`. The check compared the column with itself. I noticed when the raw counts showed 26 and 3,075 failed casts against a reported 0. Fixed with `*_raw` columns. Result now: 26 and 3,075 masked, 0 unexpected.

## Known limitations

- Only two months are loaded, so month-gap checks have not found a gap yet.
- Volumes use different units by product and are not summed across products. There is no `unit` column yet.
- Rule tests used constructed bad rows for the 4 reject rules and the unknown-well warning. The masked-value rules have no constructed case yet.
