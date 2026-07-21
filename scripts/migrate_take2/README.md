# Science-file S3 migration

One-off tooling to re-version IMAP science files: it rewrites each file's
name (and the embedded CDF metadata) to the new versioning scheme.

The heavy lifting runs on a short-lived EC2 instance so it has in-region, egress-free
access to the S3 buckets. The `run` script provisions that instance, ships the
migration code to it, executes it, and tears everything down.

```
run (your laptop)
 ├─ ensures IAM role + instance profile + key pair exist
 ├─ ensures an SSH security group (port 22) exists
 ├─ launches (or reuses) an m9g.48xlarge EC2 instance
 ├─ scp run_remote_rename.sh + rename.py to the instance
 └─ ssh: run_remote_rename.sh
         ├─ builds the NASA CDF C library + installs uv + Python deps
         └─ python rename.py   <- the actual S3 migration
```

On exit (success, failure, or Ctrl-C) the instance is terminated automatically.
The SSH security group is created once and left in place between runs.

---

## Prerequisites

On the **local** machine that runs `run`:

- AWS CLI v2, authenticated for the target account. `run` uses
  `AWS_PROFILE=imap-dev` — set up that profile (`aws configure --profile imap-dev`)
  or edit the variable at the top of `run`.
- Permission to manage IAM roles/instance profiles, EC2 key pairs,
  security groups, and instances.
- `ssh`, `scp`, `openssl`, `envsubst` on `PATH`.

Run `bash run` to test out the setup. This won't actually run the migration, simply
print out the old name -> new name mapping.

---

## Configuration

Configure the run by editing the variables near the top of `run`, then execute it.

| Variable | Default | Meaning                                                    |
|----------|---------|------------------------------------------------------------|
| `DRY_RUN`   | `1`     | `1` = only print the old -> new name mapping, write nothing. Set to `0` to actually migrate. |
| `OVERWRITE` | `0`     | `1` = re-copy destination files even if they already exist. |
| `MAX_FILES` | `1000`  | Max CDF/PKTS files to process this run (`0` = all).        |

`SRC_PREFIX`, `DRY_RUN`, and `MAX_FILES` can also be overridden from the
environment without editing `run`, e.g. `DRY_RUN=0 MAX_FILES=500 bash run`.
This is handy for running several prefixes in parallel (see below).

---

## Workflow

### Incremental file copying

Reads each source object under `$SRC_PREFIX` in `$SRC_BUCKET`, rewrites the CDF metadata
(`Data_version`, `Logical_file_id`, `Parents`) and writes it to the new name
under the same `$SRC_PREFIX`, but now in `$DST_BUCKET`. PKTS files are copied as-is to
the new name. Existing objects at destination are skipped, so this stage is resumable
and can be run in batches (see below).

`MAX_FILES` limits how many CDF/PKTS files a single run processes. Because
it **skips destinations that already exist**, repeated
runs with the same `MAX_FILES` walk through the whole set a batch at a time:

```bash
# in run: DRY_RUN=0, OVERWRITE=0, MAX_FILES=1000
bash run   # copies the first 1000 not-yet-copied files
bash run   # copies the next 1000
bash run   # ... repeat until everything is copied to DST_BUCKET
```

Set `MAX_FILES=0` to process everything in one run. *Not recommended.* except for
testing. `prod` has ~281k files. Probably try 1000 first to see how long it takes.

To **regenerate** files you have already copied (e.g. after fixing the metadata
logic), set `OVERWRITE=1`. This disables the skip-if-exists behavior and
re-copies the selected files in place.

This is single threaded since I'm having trouble getting `spacepy` to behave in a
multi-processing environment. However, a bigger instance will still make it go faster.

### Running several prefixes in parallel

To speed things up you can shard the work by `SRC_PREFIX` and run several copies
of `run` at once, one per prefix. `SRC_PREFIX`, `DRY_RUN`, and `MAX_FILES` can
be overridden from the environment, so **do not edit `run` in place** for this —
pass them on the command line instead:

```bash
DRY_RUN=0 SRC_PREFIX=imap/lo   bash run     # terminal 1
DRY_RUN=0 SRC_PREFIX=imap/mag  bash run     # terminal 2
DRY_RUN=0 SRC_PREFIX=imap/swe  bash run     # terminal 3
```

Note the default is `DRY_RUN=1`, which only prints the `old -> new` mapping and
writes **nothing** — pass `DRY_RUN=0` to actually migrate.

Each copy derives its own EC2 instance name from the prefix
(`INSTANCE_TAG=s3-transition-<slug>`), so every terminal gets its **own**
instance, its own `$HOME` on that instance, and a teardown that only kills its
own box — the runs don't interfere. The shared IAM role, key pair, and security
group are created-if-missing, so a quick warm-up `bash run` before fanning out
avoids a first-run creation race on those.

Caveats:

- Give every terminal a **different** `SRC_PREFIX`. Two runs with the same
  prefix resolve to the same instance name and will collide.
- Each shard launches its own `m9g.48xlarge`, so N terminals = N instances
  running at once — watch the cost and any vCPU quota.
