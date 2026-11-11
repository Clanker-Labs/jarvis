"""Write the JSON Schema for the event union (input for the TypeScript generator)."""

import json
import sys
from pathlib import Path

from jarvis_protocol.audio import MIC_SAMPLE_RATE
from jarvis_protocol.events import PROTOCOL_VERSION, EventAdapter

schema = EventAdapter.json_schema(mode="serialization", ref_template="#/definitions/{model}")
schema["definitions"] = schema.pop("$defs")


def strip_field_titles(node: object) -> None:
    """Field-level titles make json-schema-to-typescript emit one alias per field; drop them."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for sub in props.values():
                if isinstance(sub, dict):
                    sub.pop("title", None)
        for v in node.values():
            strip_field_titles(v)
    elif isinstance(node, list):
        for v in node:
            strip_field_titles(v)


strip_field_titles(schema)
schema["title"] = "JarvisEvent"
schema["$comment"] = f"protocol={PROTOCOL_VERSION} mic_sample_rate={MIC_SAMPLE_RATE}"
out = Path(sys.argv[1])
out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
