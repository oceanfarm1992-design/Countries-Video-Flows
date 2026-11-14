import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from catch_up import STALE_MINUTES, decide  # noqa: E402


def test_starts_todays_workflow_when_its_schedule_went_silent():
    start, reason = decide("rankings", "comparison", posted=False, minutes=370, status="completed")
    assert start and "comparison" in reason


def test_leaves_it_alone_when_already_posted():
    assert decide("rankings", "comparison", posted=True, minutes=370, status="completed")[0] is False


def test_leaves_it_alone_while_it_is_running():
    assert decide("rankings", "comparison", posted=False, minutes=500, status="in_progress")[0] is False


def test_leaves_it_alone_while_its_schedule_ticks():
    assert decide("rankings", "comparison", posted=False, minutes=STALE_MINUTES - 5,
                  status="completed")[0] is False


def test_never_restarts_itself():
    assert decide("comparison", "comparison", posted=False, minutes=None, status=None)[0] is False
