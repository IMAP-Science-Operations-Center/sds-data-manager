"""Test indexer lambda"""


from sds_data_manager.lambda_code.SDSCode import indexer


def test_batch_job_event(test_engine):
    # TODO: replace event with other event source
    # dict. We don't use "Records" anymore. But
    # leaving for now to test database capabilities.
    # Will remove in upcoming PR.
    event = {
        "Records": [
            {
                "detail-type": "Object Created",
                "source": "aws.s3",
                "s3": {
                    "version": "0",
                    "bucket": {"name": "sds-data-449431850278"},
                    "object": {
                        "key": "imap_hit_l0_sci-test_20240101_20240104_v02-01.pkts",
                        "reason": "PutObject",
                    },
                },
            }
        ]
    }
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value is None
