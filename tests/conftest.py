"""Test-run settings: a time limit per test, and the long checks kept out of the default run.

    python -m pytest              everyday run: tests marked slow are skipped, 20 s per test
    python -m pytest --slow       everything, the slow ones allowed 180 s each
    python -m pytest --max-seconds 60   another limit for the everyday tests

A test past its limit fails with a message saying so. The limit takes effect as soon as the
geometry kernel hands control back to Python (a single kernel call can't be interrupted), so
a test can overrun by the length of its longest kernel call. Only on macOS and Linux; on
Windows tests run without a limit.
"""
import signal

import pytest

DEFAULT_LIMIT = 20.0
SLOW_LIMIT = 180.0


def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", help="also run the long shell checks (marked slow)")
    parser.addoption("--max-seconds", type=float, default=DEFAULT_LIMIT, help="time limit per everyday test")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long checks (shells of lofted parts), run with --slow")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--slow"):
        return
    skip = pytest.mark.skip(reason="slow: run with --slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _time_limit(request):
    if not hasattr(signal, "setitimer"):
        yield
        return
    limit = SLOW_LIMIT if "slow" in request.node.keywords else request.config.getoption("--max-seconds")

    def stop(signum, frame):
        pytest.fail(f"took longer than {limit:g} s (the time limit per test)", pytrace=False)
    old = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
