import enum
import logging
import os
import sys
import types

if not hasattr(logging, "getLevelNamesMapping"):
    def _get_level_names_mapping():
        return dict(logging._nameToLevel)

    logging.getLevelNamesMapping = _get_level_names_mapping  # type: ignore[attr-defined]

# Provide StrEnum on Python < 3.11 for import compatibility.
if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        pass

    enum.StrEnum = StrEnum  # type: ignore[attr-defined]

# Avoid optional vector DB imports during test collection.
os.environ.setdefault("VECTOR_DB", "milvus")
fake_vector_factory = types.ModuleType("open_webui.retrieval.vector.factory")
fake_vector_factory.VECTOR_DB_CLIENT = None
sys.modules.setdefault("open_webui.retrieval.vector.factory", fake_vector_factory)

from open_webui.utils.middleware import process_tool_result


def test_process_tool_result_unescapes_html_entities():
    raw = "&quot;$ curl -s http://localhost:8080/api/v1/tools/&quot;"
    result, files, embeds = process_tool_result(
        request=None,
        tool_function_name="test_tool",
        tool_result=raw,
        tool_type="external",
    )
    assert result == '"$ curl -s http://localhost:8080/api/v1/tools/"'
    assert files == []
    assert embeds == []


def test_process_tool_result_keeps_plain_text():
    raw = "plain text output"
    result, files, embeds = process_tool_result(
        request=None,
        tool_function_name="test_tool",
        tool_result=raw,
        tool_type="external",
    )
    assert result == raw
    assert files == []
    assert embeds == []
