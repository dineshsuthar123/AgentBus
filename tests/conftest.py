import os
import tempfile
from pathlib import Path


TEST_TEMP_DIR = Path(__file__).resolve().parents[1] / ".pytest_tmp"
TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)

os.environ["TMP"] = str(TEST_TEMP_DIR)
os.environ["TEMP"] = str(TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(TEST_TEMP_DIR)
tempfile.tempdir = str(TEST_TEMP_DIR)
