from __future__ import annotations

from agentbus.product.demos import create_demo
from agentbus.repo.test_detection import TestCommandDetector


def test_owned_unittest_fixture_needs_no_pytest_dependency(tmp_path):
    create_demo("python", tmp_path)
    assert TestCommandDetector(str(tmp_path)).detect()["command"] == ["python", "-B", "-m", "unittest", "discover", "-v"]


def test_guided_marker_does_not_hide_function_style_tests(tmp_path):
    create_demo("python", tmp_path)
    (tmp_path / "test_extra.py").write_text("def test_real_behavior():\n    assert False\n", encoding="utf-8")
    assert TestCommandDetector(str(tmp_path)).detect()["command"] == ["python", "-m", "pytest"]


def test_unowned_and_oversized_markers_do_not_change_collection(tmp_path):
    create_demo("python", tmp_path)
    marker = tmp_path / ".syndra-demo.json"
    marker.write_text("x" * 4097, encoding="utf-8")
    assert TestCommandDetector(str(tmp_path)).detect()["command"] == ["python", "-m", "pytest"]
    marker.unlink()
    assert TestCommandDetector(str(tmp_path)).detect()["command"] == ["python", "-m", "pytest"]
