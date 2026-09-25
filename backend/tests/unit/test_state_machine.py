import pytest

from apps.messaging.state import META_STATUS_MAP, MessageStatus, should_transition

S = MessageStatus


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        # forward progress
        (S.QUEUED, S.SENDING, True),
        (S.SENDING, S.ACCEPTED, True),
        (S.ACCEPTED, S.SENT, True),
        (S.SENT, S.DELIVERED, True),
        (S.DELIVERED, S.READ, True),
        (S.ACCEPTED, S.READ, True),  # skipped intermediate webhooks
        # no regression on late/out-of-order webhooks
        (S.READ, S.DELIVERED, False),
        (S.READ, S.SENT, False),
        (S.DELIVERED, S.SENT, False),
        (S.SENT, S.ACCEPTED, False),
        # idempotent repeats
        (S.READ, S.READ, False),
        (S.FAILED, S.FAILED, False),
        # failure before delivery evidence
        (S.QUEUED, S.FAILED, True),
        (S.SENDING, S.FAILED, True),
        (S.ACCEPTED, S.FAILED, True),
        (S.SENT, S.FAILED, True),
        # failure never overrides delivery evidence
        (S.DELIVERED, S.FAILED, False),
        (S.READ, S.FAILED, False),
        # delivery evidence overrides an earlier failure
        (S.FAILED, S.DELIVERED, True),
        (S.FAILED, S.READ, True),
        (S.FAILED, S.SENT, False),
        # inbound messages never transition
        (S.RECEIVED, S.READ, False),
    ],
)
def test_should_transition(current: str, target: str, expected: bool) -> None:
    assert should_transition(current, target) is expected


def test_meta_status_mapping_covers_verified_values() -> None:
    assert set(META_STATUS_MAP) == {"sent", "delivered", "read", "played", "failed"}
    assert META_STATUS_MAP["played"] == S.READ
