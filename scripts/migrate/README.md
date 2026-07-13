# Science-file S3/DB migration

One-off tooling to re-version IMAP science files: it rewrites each file's
name (and the embedded CDF metadata) to the new versioning scheme and updates
the matching `science_files` rows in the RDS database.

The heavy lifting runs on a short-lived EC2 instance so it has in-region, egress-free
access to the S3 bucket and the RDS instance. The `run` script provisions that
instance, ships the migration code to it, executes it, and tears everything down.

```
run (your laptop)
 ├─ ensures IAM role + instance profile + key pair exist
 ├─ launches (or reuses) a t3.large EC2 instance
 ├─ opens the RDS security group to that instance's IP
 ├─ scp run_remote.sh + migrate.py to the instance
 └─ ssh: run_remote.sh
         ├─ installs uv + Python deps
         └─ python migrate.py   <- the actual S3 / DB migration
```

On exit (success, failure, or Ctrl-C) the instance is terminated and the
security-group rule is revoked automatically.

---

## Prerequisites

On the **local** machine that runs `run`:

- AWS CLI v2, authenticated for the target account. `run` uses
  `AWS_PROFILE=imap-dev` — set up that profile (`aws configure --profile imap-dev`)
  or edit the variable at the top of `run`.
- Permission to manage IAM roles/instance profiles, EC2 key pairs and
  instances, and to modify the RDS security group.
- `ssh`, `scp`, `openssl`, `envsubst` on `PATH`.

Run `bash run` to test out the setup. This won't actually run the migration, simply
print out the old name -> new name mapping.

---

## Configuration

Configure the run by editing the variables near the top of `run`, then execute it.

| Variable | Default | Meaning                                                    |
|----------|---------|------------------------------------------------------------|
| `COPY_FILES` | `0`     | `1` = copy/rewrite files in S3 (stage 1).                  |
| `MODIFY_ROWS` | `0`     | `1` = update `file_path` in the database (stage 2).         |
| `OVERWRITE` | `0`     | `1` = re-copy destination files even if they already exist. |
| `MAX_FILES` | `1000`  | Max CDF/PKTS files to process this run (`0` = all).        |

> **`COPY_FILES` and `MODIFY_ROWS` are mutually exclusive.** `migrate.py`
> asserts you never enable both in the same run — do it in stages (below).

---

## Workflow

### Incremental file copying (stage 1)

```bash
COPY_FILES=1
MODIFY_ROWS=0
```

Reads each source object under `imap/`, rewrites the CDF metadata
(`Data_version`, `Logical_file_id`, `Parents`) and writes it to the new name
under the `renamed/` prefix. PKTS files are copied as-is to the new name.
Existing objects under `renamed/` are skipped, so this stage is resumable and
can be run in batches (see below).

`MAX_FILES` limits how many CDF/PKTS files a single run processes. Because
stage 1 **skips destinations that already exist** under `renamed/`, repeated
runs with the same `MAX_FILES` walk through the whole set a batch at a time:

```bash
# in run: COPY_FILES=1, MODIFY_ROWS=0, OVERWRITE=0, MAX_FILES=1000
bash run   # copies the first 1000 not-yet-copied files
bash run   # copies the next 1000
bash run   # ... repeat until everything is under renamed/
```

Set `MAX_FILES=0` to process everything in one run. *Not recommended.* except for
testing. `prod` has ~281k files. Probably try 1000 first to see how long it takes.

To **regenerate** files you have already copied (e.g. after fixing the metadata
logic), set `OVERWRITE=1`. This disables the skip-if-exists behavior and
re-copies the selected files in place.

Copying runs across multiple CPU cores automatically (one worker per core).
`run` uses `t3.large` (2 vCPUs), so two files are rewritten in parallel;
use a larger instance type if you want more throughput. `t3.xlarge` has 4 vCPUs.
`t3.2xlarge` has 8 vCPUs.


### Manual step - promote the renamed files

After verifying `renamed/`, back up the existing `imap/` tree and then bulk-move
objects from `renamed/..` to their final `imap/..` paths. `run` does not do this move
for you.

You will want to keep the `ancillary/`, `dependency/` and `spice/` trees in `imap/`
intact, since these do not have rows in the science_files table and are not affected by
the renaming.

### Update the database (stage 2)

```bash
COPY_FILES=0
MODIFY_ROWS=1
```

Updates each `science_files.file_path` from the old name to the new one.

Then run it:

```bash
bash run        # or ./run
```

#### Tips

- Inspect s3 bucket in a separate terminal to monitor file copy progress.

  ```bash
  aws s3 ls s3://sds-data-593025701104/renamed/ --recursive --summarize --human-readable
  ```

- Set `INTERACTIVE=1` to provision the instance and drop into an SSH shell
instead of running the migration. The instance is still torn down when you exit
the shell.