"""Strict JSON decoding for operator-reviewed input configuration."""

import json


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def decode_config(text: str):
    return json.loads(text, object_pairs_hook=_unique_fields)
