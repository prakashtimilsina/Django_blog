"""
Builds a PySpark StructType schema from a JSON configuration file.

The JSON format mirrors Spark's native schema representation so schemas can
be round-tripped with `df.schema.json()` / `StructType.fromJson()`.  A
lightweight custom field-list format is also supported for hand-authored
configs.

GCS support
-----------
Paths starting with ``gs://`` are read directly from Google Cloud Storage.
The ``google-cloud-storage`` library must be available (pre-installed on
Dataproc; add to requirements.txt for local dev).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DateType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

logger = logging.getLogger(__name__)

_PRIMITIVE_MAP: Dict[str, Any] = {
    "string": StringType(),
    "integer": IntegerType(),
    "int": IntegerType(),
    "long": LongType(),
    "bigint": LongType(),
    "double": DoubleType(),
    "float": FloatType(),
    "boolean": BooleanType(),
    "bool": BooleanType(),
    "short": ShortType(),
    "date": DateType(),
    "timestamp": TimestampType(),
}


def _resolve_type(field_def: Dict[str, Any]) -> Any:
    """Recursively resolve a field-definition dict to a Spark DataType."""
    raw = field_def.get("type", "string")

    # Native Spark JSON schema uses a nested dict for complex types
    if isinstance(raw, dict):
        return _resolve_type(raw)

    kind = raw.lower()

    if kind == "array":
        element_def = field_def.get("elementType", {"type": "string"})
        if isinstance(element_def, str):
            element_def = {"type": element_def}
        element_type = _resolve_type(element_def)
        contains_null = field_def.get("containsNull", True)
        return ArrayType(element_type, contains_null)

    if kind == "struct":
        return _build_struct_type(field_def.get("fields", []))

    return _PRIMITIVE_MAP.get(kind, StringType())


def _build_struct_type(fields: List[Dict[str, Any]]) -> StructType:
    """Build a StructType from a list of field-definition dicts."""
    struct_fields = []
    for fdef in fields:
        name = fdef["name"]
        data_type = _resolve_type(fdef)
        nullable = fdef.get("nullable", True)
        struct_fields.append(StructField(name, data_type, nullable))
    return StructType(struct_fields)


def _read_json_content(path: str) -> dict:
    """
    Read and parse a JSON file from a local path or ``gs://`` URI.

    On GCP Dataproc, ``google-cloud-storage`` is pre-installed and Dataproc
    service-account credentials are available automatically via ADC.
    """
    if path.startswith("gs://"):
        try:
            from google.cloud import storage as gcs
        except ImportError as exc:
            raise ImportError(
                "google-cloud-storage is required for gs:// paths. "
                "Install it with: pip install google-cloud-storage"
            ) from exc

        # gs://bucket-name/path/to/file.json
        without_scheme = path[5:]
        bucket_name, _, blob_name = without_scheme.partition("/")
        client = gcs.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        if not blob.exists():
            raise FileNotFoundError(f"GCS object not found: {path}")
        content = blob.download_as_text(encoding="utf-8")
        logger.info("Schema JSON loaded from GCS: %s", path)
        return json.loads(content)

    # Local filesystem
    local = Path(path)
    if not local.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with local.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_schema(schema_path: str) -> StructType:
    """
    Load a StructType schema from a JSON file (local or ``gs://``).

    Accepts two formats:
      1. Native Spark schema JSON  – produced by ``df.schema.json()``
         (top-level key ``"type": "struct"``)
      2. Custom field-list format  – ``{"fields": [...]}``

    Storing the schema in a separate file (rather than hard-coding it) keeps
    the Python source small and lets reviewers audit column definitions
    independently from pipeline logic.  On GCP the schema lives in GCS and
    the path is set per-environment in ``config/env/<env>.yaml``.
    """
    schema_dict = _read_json_content(schema_path)

    if schema_dict.get("type") == "struct":
        schema = StructType.fromJson(schema_dict)
        logger.info(
            "Loaded native Spark schema with %d top-level fields from %s",
            len(schema.fields), schema_path,
        )
    else:
        fields = schema_dict.get("fields", [])
        schema = _build_struct_type(fields)
        logger.info(
            "Loaded custom schema with %d top-level fields from %s",
            len(schema.fields), schema_path,
        )

    return schema


def schema_to_json(schema: StructType, output_path: str) -> None:
    """Serialize a StructType to a local JSON file for later reuse or GCS upload."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(schema.jsonValue(), fh, indent=2)
    logger.info("Schema persisted to %s", output_path)
