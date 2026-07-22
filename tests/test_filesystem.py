import pytest

from agentbus.tools.filesystem import FileSystemTools
from agentbus.tools.filesystem_operations import FileMutationOperation
from agentbus.tools.filesystem_security import ProtectedFileSystemPath


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


def test_blocks_absolute_path(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path / "workspace"))

    with pytest.raises(ValueError):
        fs.write_file(str(tmp_path / "evil.txt"), "bad")


def test_list_files(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))

    fs.write_file("a.txt", "A")
    fs.write_file("folder/b.txt", "B")

    files = fs.list_files()

    assert "a.txt" in files
    assert "folder" in files


def test_legacy_listing_excludes_generated_and_protected_files(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))
    fs.write_file("src/module.py", "source")
    fs.write_file("build/output.txt", "generated")
    for index in range(20):
        fs.write_file(f"node_modules/package-{index}/index.js", "generated")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")

    files = fs.list_files()

    assert "src/module.py" in files
    assert "build/output.txt" not in files
    assert "node_modules" not in files
    assert ".env" not in files


def test_legacy_binary_read_returns_metadata_only(tmp_path):
    (tmp_path / "binary.dat").write_bytes(b"prefix\x00payload")

    content = FileSystemTools(workspace=str(tmp_path)).read_file("binary.dat")

    assert content == "Binary file not displayed: binary.dat (14 bytes)."
    assert "payload" not in content


def test_structured_filesystem_facade_preserves_mutation_attribution(tmp_path):
    fs = FileSystemTools(workspace=str(tmp_path))

    created = fs.create_file(
        "module.py",
        "value = 1\n",
        task_id="task-1",
        invocation_id="invocation-1",
    )
    patched = fs.patch_file(
        "module.py",
        "value = 1",
        "value = 2",
        task_id="task-1",
        invocation_id="invocation-2",
        expected_sha256=created.after_sha256,
    )

    assert created.operation == FileMutationOperation.CREATE
    assert patched.operation == FileMutationOperation.PATCH
    assert patched.task_id == "task-1"
    assert patched.invocation_id == "invocation-2"
    assert fs.stat_path("module.py").is_file is True


def test_facade_denies_protected_file_reads(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")

    with pytest.raises(ProtectedFileSystemPath):
        FileSystemTools(workspace=str(tmp_path)).read_file(".env")
