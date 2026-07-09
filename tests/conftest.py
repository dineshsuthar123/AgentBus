import os
import tempfile
from pathlib import Path


TEST_TEMP_DIR = Path("C:/tmp/agentbus_pytest")
TEST_TEMP_DIR.mkdir(exist_ok=True)

os.environ["TMP"] = str(TEST_TEMP_DIR)
os.environ["TEMP"] = str(TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(TEST_TEMP_DIR)
tempfile.tempdir = str(TEST_TEMP_DIR)
