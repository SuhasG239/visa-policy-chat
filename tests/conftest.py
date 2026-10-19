"""Shared pytest options."""


def pytest_addoption(parser):
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="also run the end-to-end eval through Claude (costs API credits)",
    )
