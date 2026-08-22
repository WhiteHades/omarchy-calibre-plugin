#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from typing import Any

from calibre.ebooks.conversion.cli import create_option_parser
from calibre.utils.logging import Log


OMITTED_GROUPS = {"SEARCH AND REPLACE", "METADATA", "DEBUG"}
OMITTED_OPTIONS = {
    "debug_pipeline",
    "extract_to",
    "read_metadata_from_opf",
    "search_replace",
    "transform_css_rules",
    "transform_html_rules",
}
COMMON_OPTIONS = {
    "asciiize",
    "base_font_size",
    "chapter_mark",
    "change_justification",
    "disable_font_rescaling",
    "embed_all_fonts",
    "enable_heuristics",
    "epub_version",
    "extra_css",
    "input_profile",
    "keep_ligatures",
    "line_height",
    "margin_bottom",
    "margin_left",
    "margin_right",
    "margin_top",
    "output_profile",
    "pretty_print",
    "remove_paragraph_spacing",
    "smarten_punctuation",
    "subset_embedded_fonts",
    "use_auto_toc",
}


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


def option_type(option: Any) -> str:
    if option.action in {"store_true", "store_false"}:
        return "boolean"
    if option.type == "choice":
        return "choice"
    if option.type in {"int", "long"}:
        return "integer"
    if option.type in {"float", "complex"}:
        return "number"
    return "string"


def option_descriptor(option: Any) -> dict[str, Any]:
    return {
        "name": option.dest,
        "flag": option.get_opt_string(),
        "label": option.dest.replace("_", " ").capitalize(),
        "type": option_type(option),
        "default": json_value(option.default),
        "choices": json_value(option.choices or []),
        "help": str(option.help or "").strip(),
        "action": option.action,
        "visibility": "common" if option.dest in COMMON_OPTIONS else "advanced",
    }


def group_id(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def conversion_options(input_path: str, output_path: str) -> dict[str, Any]:
    parser, _ = create_option_parser(
        ["ebook-convert", input_path, output_path],
        Log(),
    )
    profile_options = [
        option_descriptor(option)
        for option in parser.option_list
        if option.dest in {"input_profile", "output_profile"}
    ]
    groups = [
        {
            "id": "profiles",
            "label": "Profiles",
            "options": profile_options,
        }
    ]
    for group in parser.option_groups:
        if group.title in OMITTED_GROUPS:
            continue
        options = [
            option_descriptor(option)
            for option in group.option_list
            if option.dest and option.dest not in OMITTED_OPTIONS
        ]
        if options:
            groups.append(
                {
                    "id": group_id(group.title),
                    "label": group.title.title(),
                    "options": options,
                }
            )
    return {"source": "calibre-runtime", "groups": groups}


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "conversion-options":
        raise SystemExit("usage: calibre_runtime.py conversion-options INPUT OUTPUT")
    result = conversion_options(sys.argv[2], sys.argv[3])
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
