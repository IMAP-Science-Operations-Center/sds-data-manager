# IMAP Public Data Releases

This document describes how IMAP science data is made public: the Phase E release schedule, what instrument teams deliver for each release, how the SDC performs a release, and how the Space Physics Data Facility (SPDF) picks up the released data.

## Release Schedule (Phase E)

| Data Release # | Data Date Range           | Delivery Date | Public Release Deadline | Margin     |
|----------------|---------------------------|---------------|-------------------------|------------|
| 1              | 01 Feb 2026 – 30 Apr 2026 | 15 Jun 2026   | 01 Aug 2026             | 1.5 months |
| 2              | 01 May 2026 – 31 Jul 2026 | 01 Sep 2026   | 01 Nov 2026             | 2 months   |
| 3              | 01 Aug 2026 – 31 Oct 2026 | 01 Dec 2026   | 01 Feb 2027             | 2 months   |
| 4              | 01 Nov 2026 – 31 Jan 2027 | 01 Mar 2027   | 01 May 2027             | 2 months   |
| 5              | 01 Feb 2027 – 30 Apr 2027 | 01 Jun 2027   | 01 Aug 2027             | 2 months   |
| 6              | 01 May 2027 – 31 Jul 2027 | 01 Sep 2027   | 01 Nov 2027             | 2 months   |
| 7              | 01 Aug 2027 – 31 Oct 2027 | 01 Dec 2027   | 01 Feb 2028             | 2 months   |
| 8              | 01 Nov 2027 – 31 Jan 2028 | 01 Mar 2028   | 01 May 2028             | 2 months   |

### Column Descriptions

- **Data Release #**: The release number. Releases cover the Phase E period of performance.
- **Data Date Range**: The range of data covered by the release. For this range, instrument teams should:
  - Review the data.
  - Validate the data, and document any bugs or science enhancements in GitHub tickets.
- **Delivery Date**: By this date, instrument teams must deliver to the SDC (see [Release Process at the SDC](#release-process-at-the-sdc) for where):
  - A release manifest `.txt` file listing which products to release. See [Release Manifest File](#release-manifest-file).
  - Release notes, preferably as PDF or plain text.
- **Public Release Deadline**: The date by which the data must be public.
- **Margin**: Time between the delivery date and the public release deadline.

## Release Process at the SDC

Release requests are made through a GitHub ticket containing:

- Which instrument(s). One ticket per instrument, or several instruments in one ticket, are both fine.
- The date range to make public.
- Which data levels and descriptors to release.
- Release notes, if any were provided.

The SDC works through the release tickets and runs a release for each manifest file with [`imap-data-access`](https://github.com/IMAP-Science-Operations-Center/imap-data-access):

```bash
IMAP_API_KEY=$prod_key imap-data-access release --release-type release --manifest-file <path-to-txt-file>
```

The API backend sets the `released` flag to true on the matching files, which makes them public. Once the flag is set, the SPDF can access the data.

**The release process applies only to science files.** All other files managed by the IMAP SDC, including ancillary and SPICE files, are public as soon as they are ingested.

After a release is complete, **notify the SPDF**. The SPDF does not currently have a scheduled job that pulls newly released data, so it will not see the release until someone tells them. See [SPDF Archival](#spdf-archival).

### Metadata Validation

Metadata fields must comply with both [ISTP](https://spdf.gsfc.nasa.gov/istp_guide/istp_guide.html) and SPDF requirements.

The project will produce an easy-to-read document containing the metadata for all IMAP products. It will help instrument teams check that their metadata is accurate and contains the level of information they want about their products.

## Release Manifest File

### Filename Convention

```
imap_<instrument>_<descriptor>_<start_date>_<end_date>_<version>.<extension>
```

Release manifest files follow the [ancillary filename convention](https://imap-processing.readthedocs.io/en/latest/filename-convention/calibration-files.html), with these restrictions:

- `<descriptor>` is one of:
  - `data-release-<###>`: a release number from the [release schedule](#release-schedule-phase-e), for example `data-release-002`.
  - `early-release` (**not supported yet**): release selected files ahead of a scheduled release.
  - `unrelease` (**not supported yet**): un-release selected files.
- `<start_date>` is the start date of the data being released (`YYYYMMDD`).
- `<end_date>` is the end date of the data being released (`YYYYMMDD`).
- `<version>` is the version of the manifest file (`vXXX`).
- `<extension>` is `txt`.

### File Contents

The manifest is a CSV with the header `instrument,data_level,descriptor,release_flag`, followed by one line per selection. `data_level` and `descriptor` accept `all`:

| Line                                             | Releases                                          |
|--------------------------------------------------|---------------------------------------------------|
| `<instrument>,all,all,true`                      | All data for the instrument                       |
| `<instrument>,<data_level>,all,true`             | All descriptors for the instrument and data level |
| `<instrument>,<data_level>,<descriptor>,true`    | One data level and descriptor                     |

> [!WARNING]
> **Every line in a manifest should have `release_flag` set to `true`.** The release API currently skips any line whose `release_flag` is `false`. Such a line does not exclude anything, and it does not override a broader `true` line. For example, a manifest that releases `hit,all,all` releases all HIT data, including any data level or descriptor listed on a `false` line.
>
> To withhold a product, leave it out of every `true` line. If you need to release most, but not all, of an instrument's products, list each released data level or descriptor on its own `true` line instead of using `all`.
>
> This is almost certainly unintended, but it is how the API currently behaves.

### Examples

Release all GLOWS data:

`imap_glows_data-release-002_20260501_20260731_v001.txt`
```
instrument,data_level,descriptor,release_flag
glows,all,all,true
```

Release all GLOWS L0 data:

`imap_glows_data-release-002_20260501_20260731_v001.txt`
```
instrument,data_level,descriptor,release_flag
glows,l0,all,true
```

Release a mix of data levels and descriptors:

`imap_glows_data-release-002_20260501_20260731_v001.txt`
```
instrument,data_level,descriptor,release_flag
glows,l0,all,true
glows,l1a,hist,true
glows,l1b,hist,true
glows,l2,hist,true
glows,l3d,solar-hist,true
```

Manifest used for GLOWS in Data Release 1:

`imap_glows_data-release-001_19470101_20260430_v001.txt`
```
instrument,data_level,descriptor,release_flag
glows,l3d,solar-hist,true
```

### Implementation Notes

The release endpoint is implemented in [`release_api.py`](../sds_data_manager/lambda_code/SDSCode/api_lambdas/release_api.py), and the route is defined in [`sds_api_manager_construct.py`](../sds_data_manager/constructs/sds_api_manager_construct.py). How a release request is handled:

1. `imap-data-access` uploads the manifest through the upload API. It lands under `imap/release/` in S3, and the indexer records it in the `release_files` table (`models.ReleaseFiles`).
2. `imap-data-access` then calls `GET /api-key/release?release_type=release&manifest_file=<filename>`. The route exists only behind the API key authorizer, and keys with `read` scope get a `403`.
3. The lambda downloads the manifest from S3 and takes the date range from its filename.
4. For each line with `release_flag` set to `true`, it sets `released = True` on the **latest version** of each matching file:
   - Science files match when their `start_date` falls within the manifest's date range, inclusive at both ends.
   - A `data_level` of `ancillary` releases ancillary files. A `data_level` of `all` releases both science and ancillary files. Ancillary files match when their validity range overlaps the manifest's date range.

Behavior to be aware of:

- **`false` lines are ignored.** `release_type_handler` skips them before any query runs. See the warning under [File Contents](#file-contents).
- Releasing is one-way for now. The `early-release` and `unrelease` release types return `501 Not Implemented`.
- Only the versions that are latest at release time are flagged. A file reprocessed after a release gets a new version with `released = False`, so it stays private until the date range is released again. This is intentional. See [Reprocessed Data Stays Private Until the Next Release](#reprocessed-data-stays-private-until-the-next-release).

How the `released` flag is enforced:

- Requests on the `/api-key` and `/authorized` routes can see every file. Other requests can only query released files ([`query_api.py`](../sds_data_manager/lambda_code/SDSCode/api_lambdas/query_api.py)), and downloading an unreleased file returns `403` ([`download_api.py`](../sds_data_manager/lambda_code/SDSCode/api_lambdas/download_api.py)).
- `released` defaults to `False` for science and quicklook files, and to `True` for ancillary, SPICE, spin, repoint, and small forces files ([`models.py`](../sds_data_manager/lambda_code/SDSCode/database/models.py)).

## SPDF Archival

The SPDF archives IMAP data and makes it public by downloading released data with `imap-data-access`. Nothing is pushed to the SPDF; it pulls the data itself.

The SPDF currently archives only Level 2 and Level 3 science data, although that may change. Releasing L0 and L1 data is fine. Those files become public through the SDC APIs, but the SPDF does not pick them up.

The SPDF does **not** currently pull data automatically on a schedule. After each release, notify the SPDF so they know to pull the newly released data.

The public can access released IMAP data through the [SPDF archive](https://cdaweb.gsfc.nasa.gov/pub/data/imap/) or [CDAWeb](https://cdaweb.gsfc.nasa.gov/).

### Ancillary Files

All ancillary files are currently public on ingestion. Not all of them are useful to the public, though, so the SDC has sent the SPDF a specific list of ancillary file types to archive. The SPDF pulls only those types and stores them separately from the science data.

<!-- TODO: add the list of ancillary file types sent to the SPDF. -->

## Reprocessing Between Public Releases

### Causes of Reprocessing

- **Algorithm code updates**: require manual reprocessing.
- **Ancillary file updates**: automatically trigger reprocessing for the dates the file covers.
- **SPICE file updates**: trigger reprocessing automatically or need manual reprocessing, depending on the dependency definitions.
- **Major version updates in the dependency config file**.

### Reprocessing Requests

To request reprocessing, create a GitHub ticket containing:

- Which instrument.
- Which product or data level.
- The date range to reprocess.
- A due date. This is required.

The SDC reviews reprocessing tickets in its weekly planning session or in release planning.

### Reprocessed Data Stays Private Until the Next Release

Reprocessing a file that has already been released does not update the public data. Reprocessing creates a new version of the file, and new versions start out unreleased. The public keeps seeing the previously released version until a later release covers that date range.

This is intentional. Public releases happen on the quarterly schedule above, and the released data is meant to be a stable, reviewed snapshot rather than something that changes whenever the SDC reprocesses. Releasing data outside the schedule should be rare, and is reserved for cases where released data has serious problems.
