from pathlib import Path


def test_readme_exists():
    assert Path("README.md").is_file()
