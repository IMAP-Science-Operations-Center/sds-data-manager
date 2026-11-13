# SPICE Files

The SPICE and SPICE-like files the SDC receives: what each one is, which ones actually arrive, how often, and how
much time each covers. Use it when setting staleness alarm thresholds, judging whether a gap is unusual, or reasoning
about which file the metakernel builder picks. How the builder ranks files is in
[imap-architecture.md](imap-architecture.md#spice-dependency-resolution).

## Where SPICE information lives

| What | Where |
|---|---|
| File name patterns, and how each maps to a kernel type and S3 folder | [`imap-data-access`](https://github.com/IMAP-Science-Operations-Center/imap-data-access), `file_validation.py` (`SPICEFilePath`, `_SPICE_TYPE_MAPPING`, `_SPICE_DIR_MAPPING`) |
| User docs for the `/spice-query` and `/metakernel` APIs, with a short list of kernel types | [`imap_processing`](https://github.com/IMAP-Science-Operations-Center/imap_processing), `docs/source/data-access/spice-files.rst` |
| What is actually delivered, how often, monitoring, and how the SDC picks files | This page |

## Products

Each type the SDC has received, with its file name pattern and S3 folder under `imap/spice/`.

| Kernel type | What it is | Source | File name | Folder | Files on the SDC |
|---|---|---|---|---|---|
| `leapseconds` | Leap second table, for UTC conversions | NAIF | `naif####.tls` | `lsk` | 1 (`naif0012.tls`) |
| `planetary_constants` | Sizes, shapes and orientation of solar system bodies | NAIF | `pck#####.tpc` | `pck` | 1 (`pck00011.tpc`) |
| `planetary_ephemeris` | Planetary and lunar ephemerides | NAIF | `de###.bsp` | `spk` | 1 (`de440.bsp`) |
| `lagrange_point` | Ephemeris of the Sun–Earth L1 point. Not loaded by the metakernel builder | NAIF | `L1_de###.bsp` | `spk` | 1 (`L1_de431.bsp`) |
| `earth_attitude` | High-precision Earth orientation, history plus ~3 months of prediction | NAIF | `earth_000101_yymmdd_yymmdd.bpc` | `pck` | 405 |
| `spacecraft_clock` | Spacecraft clock coefficients, for SCLK ↔ ET conversion | MOC | `imap_sclk_####.tsc` | `sclk` | 249 versions of one file |
| `imap_frames` | Spacecraft frame definitions | MOC | `imap_###.tf` | `fk` | 4 |
| `science_frames` | Instrument frame definitions | SDC/APL | `imap_science_###.tf` | `fk` | 4 |
| `attitude_history` | Definitive spacecraft attitude from telemetry: the onboard estimate, not ground-corrected | MOC | `imap_yyyy_doy_yyyy_doy_###.ah.bc` | `ck` | 431 |
| `pointing_attitude` | Attitude for each pointing (DPS) | SDC | `imap_dps_yyyy_doy_yyyy_doy_###.ah.bc` | `ck` | 335 |
| `lo_pivot_attitude` | IMAP-Lo pivot platform attitude, one file per repoint | SDC | `imap_lopivot-repoint#####_yyyy_doy_yyyy_doy_###.bc` | `ck` | 323 |
| `ephemeris_reconstructed` | Where the spacecraft was, from orbit determination | MOC (MDNAV) | `imap_recon_yyyymmdd_yyyymmdd_v##.bsp` | `spk` | 33 |
| `ephemeris_predicted` | Where the spacecraft will be, assuming no maneuvers; used onboard and for timekeeping | MOC (MDNAV) | `imap_pred_odNNN_yyyymmdd_yyyymmdd_v##.bsp` | `spk` | 47 |
| `ephemeris_nominal` | Designed trajectory for the rest of the mission, updated after maneuvers; for planning | MOC (mission design) | `imap_nom_yyyymmdd_yyyymmdd_v##.bsp` | `spk` | 8 |
| `spin` | Spin period and phase table (not a SPICE kernel) | MOC | `imap_yyyy_doy_yyyy_doy_##.spin` | `spin` | 1114 |
| `repoint` | Repoint maneuver start and end times (not a SPICE kernel) | MOC | `imap_yyyy_doy_##.repoint.csv` | `repoint` | Indexed into its own table, not `spice_files`. **TODO**: count |
| `thruster` | Small forces file: thruster firings at 20 ms resolution (not a SPICE kernel) | MOC | `imap_yyyy_doy_yyyy_doy_hist_##.sff` | `activities` | 646 |
| `metakernel` | MOC attitude (`a##`) and ephemeris (`e##`) metakernels | MOC | `imap_yyyy_doy_a##.spice.mk`, `imap_yyyy_doy_e##.mk` | `mk` | 2, both in Sep–Oct 2025. The SDC builds its own through `/metakernel` |

Where the Confluence table differs from what's delivered:

- Spin files are named `.spin`, not `.spin.csv`.
- Earth attitude lands in `pck`, not `bpc`.
- Small forces files have a single `_hist_`, not `_sff_hist__`.
- Science frame versions have three digits.

## Planned but never delivered

These appear in the pre-launch plan or the Confluence table, but no file has ever reached the SDC.

| Product | File name | Status |
|---|---|---|
| Attitude predict | `imap_yyyy_doy_yyyy_doy_##.ap.bc` | Kernel type `attitude_predict` still exists in `imap-data-access` and the APIs. |
| Launch ephemeris | `imap_launch_yyyymmdd_yyyymmdd_v##.bsp` | Launch trajectory for each launch day. Only relevant before launch. |
| 90-day predicted ephemeris | `imap_90days_yyyymmdd_yyyymmdd_v###.bsp` | For DSN scheduling. Crossed out in the Confluence table, but still listed in Hi, Lo, Ultra and I-ALiRT dependencies. |
| Long-term predicted ephemeris | `imap_long_yyyymmdd_yyyymmdd_v##.bsp` | 6-month predict for DSN long-term scheduling. |
| Burn / no-burn predicted ephemeris | `imap_burn_...bsp`, `imap_noburn_...bsp` | Predict with or without a planned maneuver. No kernel type; `imap-data-access` rejects these names. |
| STK attitude | `*.ah.a`, `*.ap.a` | STK versions of the attitude kernels. `imap-data-access` rejects these names. |

The pre-launch plan also lists MOC products that were never meant to be ingested as SPICE files: the SCLK/TDT time
history, the Mission Design Data File, and DSN view periods.

## Planned vs. observed cadence

| Product | Planned before launch | Observed |
|---|---|---|
| Attitude history | Three times a week | Every one or two days |
| SCLK | Every DSN track | About every two days |
| Small forces | Every DSN track | About daily |
| Predicted ephemeris | 4-week span | 42-day span, about every 12 days |
| Attitude and ephemeris metakernels | Weekly | Once each, then stopped |

## Delivery cadence and coverage

| Type | Delivery cadence (median / 90th pct / max gap) | Time covered per file | Ingested how long after coverage ends |
|---|---|---|---|
| `ephemeris_reconstructed` | 14 d / 22 d / 28 d | Launch to delivery date (cumulative) | ~0.8 d |
| `ephemeris_predicted` | 12 d / 20 d / 21 d | 42 d, from the OD epoch | Covers ~41 d ahead |
| `ephemeris_nominal` | 43 d / 79 d / 88 d | ~20 months, to 2027-12 – 2028-06 | Covers ~20 months ahead |
| `ephemeris_90days` | Never delivered | – | – |
| `attitude_history` | 1.3 d / 2.7 d / 8.4 d | Growing file, up to ~90 d | ~0.25 d |
| `pointing_attitude` | 1.5 d / 2.8 d / 24.1 d | Growing file, up to ~90 d | ~1.2 d |
| `spin` | 1.3 d / 2.7 d / 10.9 d | ~1 d | ~0.6 d |
| `thruster` | 0.8 d / 2.3 d / 9.2 d | ~1 d | ~0.7 d |
| `spacecraft_clock` | 2 d / 3 d / 6 d | Whole mission | – |
| `earth_attitude` | 1 d / 1 d / 2 d | 2010 to ~87 d ahead | Covers ~87 d ahead |
| `lo_pivot_attitude` | One backfill so far (2026-10-20, 323 files) | ~1 d (one per repoint) | – |
| `imap_frames`, `science_frames` | Irregular: 4 deliveries each since launch | Whole mission | – |
| `leapseconds`, `planetary_constants`, `planetary_ephemeris`, `lagrange_point` | Once | Whole mission | – |
| Repoint files | **TODO**: in their own table, not in this snapshot | | |

## Spacecraft ephemeris

- **Reconstructed** (`imap_recon_<start>_<end>_v01.bsp`): each delivery covers from 2025-09-25 up to about the
  delivery date, so the newest file replaces all earlier ones. Usually delivered together with a predicted file.
- **Predicted** (`imap_pred_od<NNN>_<start>_<end>_v01.bsp`): one file per orbit determination (OD), starting at the OD
  epoch and running 42 days (a few early files ran 27–28 days). A skipped OD (e.g. od035, od043) makes a gap of about
  21 days. Predicted ephemeris doesn't trigger processing (`NON_TRIGGERING_KERNEL_TYPES`).
- **Nominal** (`imap_nom_<start>_<end>_v01.bsp`): a long-range trajectory delivered every one to three months. End
  dates don't increase from one delivery to the next (the 2026-04-07 file ends in 2027-12, earlier ones in 2028-06).
- **OD numbers in file names**: predicted files always have one. Nominal and reconstructed files dropped it after the
  first few deliveries (od001–od005), so file names can't be used to order deliveries.
- **90-day** (`ephemeris_90days`): no file has ever been delivered, but Hi, Lo, Ultra and I-ALiRT dependencies still
  list it. The Confluence table crosses it out. **TODO**: confirm it's defunct.

## Attitude and pointing (CK)

- `attitude_history` (`imap_<YYYY_DOY>_<YYYY_DOY>_<VVV>.ah.bc`) and `pointing_attitude` (`imap_dps_...`) are
  **growing files**. A series keeps a fixed start and is re-delivered with a later end date every one or two days. A
  new series starts roughly every three months. The end date is part of the file name, so each re-delivery is a new
  file name with its version restarting at `_001`.


## Spin and small forces

- `spin` (`imap_<YYYY_DOY>_<YYYY_DOY>_<VV>.spin`) and `thruster` (`..._hist_<VV>.sff`) arrive as roughly one-day files,
  usually less than a day after the data ends. Revisions of the same day get a higher version.

## Clock, Earth attitude and static kernels

- **SCLK** (`imap_sclk_<NNNN>.tsc`): a single file name whose version (`NNNN`) goes up about every two days. Only the
  latest version is used.
- **Earth attitude** (`earth_000101_<end>_<made>.bpc`): a new file daily, each covering up to ~87 days past its
  creation date.
- **Frames** (`imap_<NNN>.tf`, `imap_science_<NNN>.tf`): versions 100–140 so far, delivered when the frame definitions
  change.
- **Leapseconds, planetary constants, planetary ephemeris**: standard NAIF kernels (`naif0012.tls`, `pck00011.tpc`,
  `de440.bsp`), replaced only when NAIF releases a new one.
- **`lagrange_point`** (`L1_de431.bsp`): the Sun–Earth L1 point. Indexed but never put in a metakernel.
- **Lo pivot attitude** (`imap_lopivot-repoint<NNNNN>_...bc`): one file per repoint, so far delivered only as a single
  backfill. **TODO**: expected cadence once routine.

## Staleness alarms

`spice_data_monitoring` runs daily at 10:00 UTC and alarms when the newest file of a group is older than its threshold
(set in `stackbuilder.py`):

| Alarm | Threshold | Why |
|---|---|---|
| Predicted ephemeris | 21 d | Normal gaps run up to 21 d when one OD is skipped, so this alarms on the second missed delivery and still leaves ~3 weeks of the latest file's coverage. |
| CK kernels, spin, SCLK, repoint | 4 d | Normally delivered every one or two days. |

## TODO

- [ ] Who produces each type, and who to contact when one is late.
- [ ] The agreed delivery schedule for each type, to compare against what's observed above.
- [ ] Repoint file cadence.
- [ ] Confirm `ephemeris_90days` is defunct (it's crossed out in Confluence) and remove it from the dependencies.
- [ ] Whether the metakernel builder should load `lagrange_point`.
- [ ] Why `attitude_predict` was never delivered.
- [ ] Who produces the Lo pivot attitude kernels, and how often they'll arrive.
