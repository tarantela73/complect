import pytest

from apps.common.services.file_pipeline.clamav_result import parse_clamd_instream_result


def test_clean_clamd_reply_is_clean():
    assert parse_clamd_instream_result({"stream": ("OK", None)}) == "CLEAN"

def test_infected_clamd_reply_cannot_be_clean():
    assert parse_clamd_instream_result({"stream": ("FOUND", "Synthetic-test-signature")}) == "INFECTED"

@pytest.mark.parametrize("reply", [None, {}, {"stream": ("ERROR", "unavailable")}])
def test_missing_or_unknown_clamd_reply_requires_retry(reply):
    with pytest.raises(RuntimeError, match="LEGACY_SCAN_RESULT_UNAVAILABLE"):
        parse_clamd_instream_result(reply)
