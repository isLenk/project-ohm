import os
import tempfile

# Must be set before curator_service.app.config is imported.
_DATA = tempfile.mkdtemp(prefix="curator-test-")
os.environ["CURATOR_DATA_DIR"] = _DATA
os.environ["CURATOR_MODELS_DIR"] = os.path.join(_DATA, "models")
os.environ["CURATOR_TRAIN_SIMULATE"] = "1"
os.environ.pop("CURATOR_TOKEN", None)
os.environ["MEMORY_API_URL"] = ""
os.environ["CURATOR_LLM_URL"] = "http://127.0.0.1:9/v1"  # nothing listens here

import pytest  # noqa: E402

from curator_service.app.project import Project  # noqa: E402
from curator_service.tests.seed import seed  # noqa: E402


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path / "demo.ohm", "Demo")
    seed(p)
    yield p
    p.close()
