S3 Replication
==============

Once you have finished :doc:`backup-deploy`, the new items are automatically replicated
from the source bucket to the backup bucket.

Copying items back into source account bucket
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To restore files from the backup bucket into the main account bucket, we are going to
[assume the role](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-role.html#cli-role-prereqs)
created by SdsDataManager stack in the source account. Here, "source account" refers to
the account with SdsDataManager deployed into it (i.e. dev or prod).

#. Update ``~/.aws/config`` to include a new profile for this role. Here, ``source_profile`` should be the source account profile
```
[profile backup-role]
role_arn = arn:aws:iam::<source account number>:role/BackupRole
source_profile = imap
```
#. Update the role in the dev account to include a new principal:

    * Under the "Trust relationships" tab, edit the trust policy
    * add a new principal to the trust policy with your username. You can do this via the GUI or just add under "principal": ``"AWS": "arn:aws:iam::<source account>>:user/hartnett"`` (update with your username)
    * We can then assume this role in the AWS CLI with ``--profile``

#. Check your credentials: ``aws s3 ls --profile backup-role s3://sds-data-dev``
#. To transfer all files from the backup bucket to the main bucket: ``aws s3 sync --profile backup-role s3://sds-data-backup s3://sds-data-dev``
#. If you have any issues, you can check permissions by downloading and uploading locally (replace one of the bucket names with a local file path)
