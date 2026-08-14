import pytest

from codelens.review.infrastructure.model_paths import (
    AmbiguousRecursiveGlobError,
    InvalidModelPathError,
    match_model_glob,
    normalize_model_path,
    parse_model_glob,
)


@pytest.mark.parametrize(
    ("requested", "normalized", "scope_type"),
    [
        ("", "", "root"),
        (".", "", "root"),
        ("./", "", "root"),
        ("./src", "src", "directory"),
        ("src/", "src", "directory"),
        ("src/a.py", "src/a.py", "file"),
        ("unicode/中文.py", "unicode/中文.py", "file"),
    ],
)
def test_normalizes_model_paths(requested: str, normalized: str, scope_type: str) -> None:
    path = normalize_model_path(
        requested,
        visible_paths=("src/a.py", "src/deep/a.py", "unicode/中文.py", "link-to-a"),
    )

    assert path.requested_path == requested
    assert path.normalized_path == normalized
    assert path.scope_type == scope_type


@pytest.mark.parametrize(
    "path",
    [
        "src//a.py",
        "/src/a.py",
        "C:/src/a.py",
        "C:\\src\\a.py",
        "..",
        "src/../a.py",
        "src\\a.py",
        "src/\0a.py",
    ],
)
def test_rejects_unsafe_or_ambiguous_paths(path: str) -> None:
    with pytest.raises(InvalidModelPathError):
        normalize_model_path(path, visible_paths=("src/a.py",))


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("*.py", "a.py", True),
        ("*.py", "src/a.py", True),
        ("*.py", "src/deep/a.py", True),
        ("compiler*.py", "src/deep/compiler_plan.py", True),
        ("tests/*.py", "tests/test_a.py", True),
        ("tests/*.py", "tests/unit/test_nested.py", False),
        ("tests/**/*.py", "tests/unit/test_nested.py", True),
        ("tests/**/*.py", "tests/test_a.py", True),
        ("src/?.py", "src/a.py", True),
        ("src/[ab].py", "src/b.py", True),
        ("*.py", ".hidden.py", True),
        ("*.py", "unicode/中文.py", True),
    ],
)
def test_shared_glob_semantics(pattern: str, path: str, matches: bool) -> None:
    assert match_model_glob(path, parse_model_glob(pattern)) is matches


@pytest.mark.parametrize(
    ("pattern", "suggestion"),
    [
        ("**.py", "*.py"),
        ("src/**.py", "src/*.py"),
        ("foo**bar.py", "foo*bar.py"),
    ],
)
def test_rejects_ambiguous_recursive_globs_with_a_valid_suggestion(
    pattern: str, suggestion: str
) -> None:
    with pytest.raises(AmbiguousRecursiveGlobError) as raised:
        parse_model_glob(pattern)

    assert raised.value.suggested_pattern == suggestion
    assert parse_model_glob(suggestion).effective_pattern == suggestion


def test_sorted_matching_paths_are_stable_for_files_and_symlinks() -> None:
    paths = ("src/z.py", "link-to-a", "src/a.py", "unicode/中文.py")
    pattern = parse_model_glob("*")

    matched = sorted(path for path in paths if match_model_glob(path, pattern))

    assert matched == ["link-to-a", "src/a.py", "src/z.py", "unicode/中文.py"]


def test_rejects_impossible_file_and_directory_name_collision() -> None:
    with pytest.raises(InvalidModelPathError, match="both a file and a directory"):
        normalize_model_path("src", visible_paths=("src", "src/a.py"))
