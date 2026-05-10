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

Namespace handling
------------------
spark-xml with ``ignoreNamespace=true`` strips all namespace prefixes from
element and attribute names before they are mapped to DataFrame columns.
This means:

  XML source                        DataFrame column
  --------------------------------  ----------------
  <ehr:record_id>REC-001</...>      record_id
  <dental:tooth><dental:id>11<...>  tooth.id (inside the teeth array)
  xsi:nil="true" on an element      element reads as null (correct)
  xmlns:ehr="http://..."            excluded automatically

Rules you must follow when namespaces are present
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. ``row_tag`` must be the LOCAL name only — never a prefixed name.
   Correct:   row_tag = "record"
   Wrong:     row_tag = "ehr:record"

2. Schema field names must use LOCAL names only (no prefixes).
   The schema_config.json already follows this rule.

3. If two elements in DIFFERENT namespaces share the same local name,
   spark-xml will treat them as the same field after stripping prefixes.
   This is a known spark-xml limitation.  Workaround: rename one element
   in a pre-processing step, or use a namespace-aware SAX parser for that
   sub-tree and pass the result to this pipeline as JSON/Parquet.

4. xsi:type attributes are stripped along with the namespace prefix;
   they will appear as attr_type if attributePrefix="attr_".
   Add "xsi:type" to excludeAttribute in reader_options to suppress them.

Multi-document XML (multiple <?xml?> declarations, no root wrapper)
-------------------------------------------------------------------
spark-xml uses ``XmlInputFormat`` — a Hadoop InputFormat that scans the
raw bytes of a file looking for the exact byte sequence ``<rowTag>`` and
``</rowTag>``.  It does NOT attempt to parse the whole file as a single
XML document.

This means a file structured like:

    <?xml version="1.0" encoding="UTF-8"?>
    <record>...</record>

    <?xml version="1.0" encoding="UTF-8"?>
    <record>...</record>

is handled correctly by default.  The ``<?xml?>`` bytes between records
fall outside the ``<record>…</record>`` byte range and are silently
skipped.  No root wrapper element is required.

Two failure modes to avoid
~~~~~~~~~~~~~~~~~~~~~~~~~~
1. ``wholeFile=true`` — causes spark-xml to load the entire file as a
   single XML document, which will fail or read only the first record.
   Never set this option for multi-document files (or large files in
   general; it prevents parallelism).

2. Mid-file encoding changes — if documents in the same file declare
   different encodings (e.g., UTF-8 then ISO-8859-1), the JVM XML
   parser may reject the second record.  Ensure all documents use the
   same encoding (UTF-8 is strongly recommended).

Safety-net: ``normalize_multi_doc_xml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
If you must pass the file to a strict XML parser outside of Spark
(e.g., for XSLT transforms or schema validation), use
``normalize_multi_doc_xml()`` to strip the duplicate ``<?xml?>``
declarations before processing.  This function is NOT needed in the
normal spark-xml pipeline path.
"""

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType

logger = logging.getLogger(__name__)

_XML_DECL_RE = re.compile(r"^\s*<\?xml\b", re.IGNORECASE)

_SAFE_READER_DEFAULTS: Dict[str, str] = {
    "mode": "PERMISSIVE",
    "columnNameOfCorruptRecord": "_corrupt_record",
    "inferSchema": "false",
    "multiLine": "true",
    # Strip all namespace prefixes from element and attribute local names.
    # Schema field names and rowTag must use local names only (see module doc).
    "ignoreNamespace": "true",
    "attributePrefix": "attr_",
    "valueTag": "_value",
    "treatEmptyValuesAsNulls": "true",
    "charset": "UTF-8",
}

# Attributes that are purely namespace bookkeeping and should never become
# DataFrame columns.  Passed to excludeAttribute when namespaces are active.
_NAMESPACE_NOISE_ATTRS: List[str] = [
    "xmlns",
    "xsi:schemaLocation",
    "xsi:noNamespaceSchemaLocation",
]


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
    row_tag  : XML element that maps to one DataFrame row.
               Must be the LOCAL name only — never a namespace-prefixed name.
               e.g. ``"record"`` not ``"ehr:record"``
    schema   : pre-defined StructType — mandatory for 200 GB+ files.
               All field names must be local names (no namespace prefixes).
    options  : caller-supplied reader options; override the safe defaults.
               Use ``excludeAttribute`` to suppress unwanted attributes, e.g.:
               ``{"excludeAttribute": "xsi:type,xsi:schemaLocation"}``

    Returns
    -------
    DataFrame with one row per ``row_tag`` element
    """
    if ":" in row_tag:
        raise ValueError(
            f"row_tag '{row_tag}' contains a namespace prefix. "
            "Use the local name only (e.g. 'record', not 'ehr:record'). "
            "spark-xml with ignoreNamespace=true matches on local names."
        )

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


def read_xml_namespaced(
    spark: SparkSession,
    path: str,
    row_tag: str,
    schema: StructType,
    exclude_attrs: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> DataFrame:
    """
    Convenience wrapper for XML files with heavy namespace usage.

    Identical to ``read_xml`` but pre-configures ``excludeAttribute`` to
    suppress common namespace-declaration attributes (``xmlns``,
    ``xsi:schemaLocation``, etc.) that would otherwise produce noisy columns.

    Parameters
    ----------
    exclude_attrs : additional attribute names to suppress beyond the defaults;
                   e.g. ``["xsi:type", "xsi:nil"]``
    """
    combined = list(_NAMESPACE_NOISE_ATTRS)
    if exclude_attrs:
        combined.extend(exclude_attrs)

    ns_options = {"excludeAttribute": ",".join(combined)}
    if options:
        ns_options.update(options)

    return read_xml(spark, path, row_tag, schema, options=ns_options)


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


def normalize_multi_doc_xml(
    input_path: str,
    output_path: Optional[str] = None,
) -> str:
    """
    Strip duplicate ``<?xml?>`` declarations from a multi-document XML file.

    spark-xml handles multi-document XML natively (see module docstring), so
    this function is NOT needed in the normal pipeline path.  Use it only when
    you need to pass the file to a strict single-document XML parser outside
    of Spark (e.g., XSLT transforms, XSD validation, or debugging tools).

    The function reads the file line-by-line (streaming), keeps the first
    ``<?xml?>`` declaration it encounters, and silently drops all subsequent
    ones.  Memory usage is O(line length), not O(file size), so it is safe
    for large files on local disk — but it cannot process ``gs://`` paths
    directly; stage the file locally first.

    Parameters
    ----------
    input_path  : local path to the source multi-document XML file
    output_path : destination path for the normalized file.
                  Defaults to a temporary file (caller is responsible for
                  deleting it when done).

    Returns
    -------
    str — path to the normalized output file
    """
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".xml", prefix="xml_etl_norm_")
        os.close(fd)

    seen_declaration = False
    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
    ):
        for line in fin:
            if _XML_DECL_RE.match(line):
                if not seen_declaration:
                    fout.write(line)
                    seen_declaration = True
                # else: silently drop duplicate declaration
            else:
                fout.write(line)

    logger.info(
        "Normalized multi-doc XML: %s → %s (duplicated <?xml?> declarations removed)",
        input_path,
        output_path,
    )
    return output_path
