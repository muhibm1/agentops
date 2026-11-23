"""Check a model answer against the schema its request names.

python -m live.check_answer KEY --work W
"""

import argparse
import json
from pathlib import Path

import agentops.state as state_models


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("key")
    p.add_argument("--work", required=True)
    a = p.parse_args()
    folder = Path(a.work) / "model"
    req = json.loads((folder / "requests" / f"{a.key}.json").read_text())
    schema = getattr(state_models, req["schema_name"])
    schema.model_validate_json((folder / "answers" / f"{a.key}.json").read_text())
    print("valid", req["schema_name"])


if __name__ == "__main__":
    main()
