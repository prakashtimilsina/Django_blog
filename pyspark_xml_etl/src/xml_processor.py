"""
Reads large XML files via the Databricks spark-xml library.

The caller must start Spark with the spark-xml JAR on the classpath, e.g.:

    spark-submit --packages com.databricks:spark-xml_2.12:0.17.0 ...

Key design choices
------------------
- Schema is always supplied externally (never inferred) to avoid the full
  scan that schema inference requires on multi-GB files.
- PERMISSIVE mode keeps the pipeline alive on malformed rows; bad records
  land in _corrupt_record for downstream triage.
- attributePrefix / valueTag follow Databricks conventions so attribute-
  heavy XML maps cleanly to DataFrame columns.
"""

import logging
from typing import Any, Dict, Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType

logger = logging.getLogger(__name__)

_SAFE_READER_DEFAULTS: Dict[str, str] = {
    "mode": "PERMISSIVE",
    "columnNameOfCorruptRecord": "_corrupt_record",
    "inferSchema": "false",
    "multiLine": "true",
    "ignoreNamespace": "true",
    "attributePrefix": "attr_",
    "valueTag": "_value",
    "treatEmptyValuesAsNulls": "true",
    "charset": "UTF-8",
}


def read_xml(
    spark: SparkSession,
    path: str,
    row_tag: str,
    schema: StructType,
    options: Optional[Dict[str, Any]] = None,
) -> DataFrame:
    """
    Read XML file(s) into a DataFrame using com.databricks.spark.xml.

    Parameters
    ----------
    spark    : active SparkSession (must have spark-xml JAR loaded)
    path     : glob-compatible path, e.g. ``s3a://bucket/raw/*.xml``
    row_tag  : XML element that maps to one DataFrame row, e.g. ``"record"``
    schema   : pre-defined StructType — mandatory for 200 GB+ files
    options  : caller-supplied reader options; override the safe defaults

    Returns
    -------
    DataFrame with one row per ``row_tag`` element
    """
    merged = {**_SAFE_READER_DEFAULTS, "rowTag": row_tag}
    if options:
        merged.update({k: str(v) for k, v in options.items()})

    logger.info("Reading XML: path=%s  rowTag=%s", path, row_tag)

    df = (
        spark.read.format("com.databricks.spark.xml")
        .options(**merged)
        .schema(schema)
        .load(path)
    )

    logger.info("XML loaded — %d top-level columns", len(df.columns))
    return df


def validate_corrupt_records(df: DataFrame, max_corrupt: int = 0) -> None:
    """
    Raise if the number of corrupt records exceeds *max_corrupt*.

    Call this immediately after ``read_xml`` during development or in
    data-quality gate stages of a production pipeline.
    """
    if "_corrupt_record" not in df.columns:
        return

    count = df.filter(df["_corrupt_record"].isNotNull()).count()
    logger.info("Corrupt record count: %d", count)

    if count > max_corrupt:
        raise ValueError(
            f"Found {count} corrupt XML records (threshold={max_corrupt}). "
            "Check _corrupt_record column for details."
        )
