import pytest

from agentbus.tools.filesystem import FileSystemTools


def test_write_and_read_file(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))

    result = fs.write_file("hello.txt", "hello")
    content = fs.read_file("hello.txt")

    assert "Wrote file" in result
    assert content == "hello"


def test_blocks_path_traversal(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))

    with pytest.raises(ValueError):
        fs.write_file("../evil.txt", "bad")


def test_list_files(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))

    fs.write_file("a.txt", "A")
    fs.write_file("folder/b.txt", "B")

    files = fs.list_files()

    assert "a.txt" in files
    assert "folder" in files