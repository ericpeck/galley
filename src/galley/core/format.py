"""Minimal JSON and YAML formatting."""

import json
from io import StringIO

from ruamel.yaml import YAML


def format_json(text: str) -> str:
    """Return ``text`` parsed as JSON and pretty-printed."""
    parsed = json.loads(text)
    return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"


def format_yaml(text: str) -> str:
    """Return ``text`` parsed as YAML and dumped as a block document."""
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    parsed = yaml.load(text)
    buffer = StringIO()
    yaml.dump(parsed, buffer)
    return buffer.getvalue()
