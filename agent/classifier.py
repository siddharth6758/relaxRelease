def parse_version(tag: str) -> tuple[int, int, int]:
    """
    Parses a version tag like v1.2.3 or 1.2.3 into (major, minor, patch).
    """
    cleaned = tag.lstrip("v").strip()
    parts = cleaned.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version tag: {tag}. Expected format: 1.2.3 or v1.2.3")
    return tuple(int(p) for p in parts)


def classify_release(previous_tag: str, new_tag: str) -> str:
    """
    Compares two version tags and returns 'major' or 'minor'.

    Major: the major version number increased (e.g. 1.x.x -> 2.0.0)
    Minor: anything else (minor or patch bump)
    """
    prev = parse_version(previous_tag)
    curr = parse_version(new_tag)

    if curr[0] > prev[0]:
        return "major"
    return "minor"


if __name__ == "__main__":
    test_cases = [
        ("1.0.0", "1.0.1", "minor"),
        ("1.0.0", "1.1.0", "minor"),
        ("1.9.9", "2.0.0", "major"),
        ("2.5.3", "3.0.0", "major"),
        ("v1.0.0", "v1.2.0", "minor"),
        ("v2.0.0", "v3.0.0", "major"),
    ]

    all_passed = True
    for prev, curr, expected in test_cases:
        result = classify_release(prev, curr)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_passed = False
        print(f"{status} {prev} -> {curr} = {result} (expected: {expected})")

    print()
    print("All tests passed!" if all_passed else "Some tests failed — check above.")