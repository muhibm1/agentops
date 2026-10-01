"""Fail CI when fewer tests passed than the floor, so a run that collected nothing is not green.

Usage: python scripts/check_test_count.py <junit.xml> <floor>
Exit 0 at or above the floor, 1 below it.
"""

import sys
import xml.etree.ElementTree as ElementTree

COUNTED_OUTCOMES_THAT_ARE_NOT_PASSES = ("skipped", "failures", "errors")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check_test_count.py <junit.xml> <floor>", file=sys.stderr)
        return 1

    junit_path = argv[1]
    try:
        floor = int(argv[2])
        passed = count_passed_tests(junit_path)
    except (ValueError, OSError, ElementTree.ParseError) as error:
        print(
            f"Cannot check the floor: {type(error).__name__} reading {junit_path} "
            f"with floor {argv[2]!r}; the floor must be an integer and the file valid junit XML.",
            file=sys.stderr,
        )
        return 1
    print(f"{passed} tests passed; the floor is {floor}.")
    if passed < floor:
        print(f"Below the floor: {passed} passed is fewer than {floor}.", file=sys.stderr)
        return 1
    return 0


def count_passed_tests(junit_path: str) -> int:
    """Sum tests minus skipped, failures and errors over every testsuite element."""
    root = ElementTree.parse(junit_path).getroot()
    passed = 0
    for suite in root.iter("testsuite"):
        passed += int(suite.get("tests", "0"))
        for outcome in COUNTED_OUTCOMES_THAT_ARE_NOT_PASSES:
            passed -= int(suite.get(outcome, "0"))
    return passed


if __name__ == "__main__":
    sys.exit(main(sys.argv))
