"""python -m brain status                 what's installed and set up, as JSON
python -m brain try <role> "<text>"     run a tiny task and show which backend answered (for debugging)
"""

import json
import sys

from . import config
from .router import run, skip_reason
from .status import status
from .task import Task


def main(argv):
    if argv[:1] == ["status"]:
        print(json.dumps(status(), indent=2))
        return 0
    if len(argv) >= 3 and argv[0] == "try":
        role, text = argv[1], " ".join(argv[2:])
        for name in config.chain(role):
            print(f"  {name}: {skip_reason(name) or 'usable'}")
        answer = run(Task(role, "Answer briefly.", text, max_tokens=100))
        print(answer if answer is not None else "(no backend answered)")
        return 0 if answer is not None else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
