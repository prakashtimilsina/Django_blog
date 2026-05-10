#!/usr/bin/env python3
"""
Generate the pipeline schema JSON from a sample XML file.

Two modes
---------
infer   Use spark-xml to infer the schema from a small sample XML file,
        then save it as the JSON format expected by schema_builder.load_schema().
        Run this ONCE on a representative sample, then use the saved JSON in
        production (never infer on 200 GB files).

        Requires: PySpark + spark-xml JAR on the classpath
        Run via:
            spark-submit \\
                --packages com.databricks:spark-xml_2.12:0.17.0 \\
                scripts/generate_schema.py infer \\
                --input  tests/fixtures/sample_deep_ns.xml \\
                --output config/schema_deep_ns_inferred.json \\
                --row-tag record

        Or on GCS:
            spark-submit \\
                --packages com.databricks:spark-xml_2.12:0.17.0 \\
                gs://<bucket>/scripts/generate_schema.py infer \\
                --input  gs://<bucket>/samples/small_sample.xml \\
                --output gs://<bucket>/config/schema_inferred.json \\
                --row-tag record

show    Print the pre-built StructType for sample_deep_ns.xml to stdout in a
        readable tree format.  No Spark or JAR required.

        python scripts/generate_schema.py show
"""

import argparse
import json
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Mode: infer
# ─────────────────────────────────────────────────────────────────────────────

def infer_schema(input_path: str, output_path: str, row_tag: str,
                 sampling_ratio: float) -> None:
    """
    Infer the StructType schema from a sample XML file using spark-xml,
    then write it as JSON so it can be used with schema_builder.load_schema().

    Steps
    -----
    1. Read the sample with inferSchema=true (expensive — only on small files!)
    2. Print the inferred schema to the console for review
    3. Serialize to JSON (Spark native format accepted by StructType.fromJson())
    4. Save to output_path (local or gs://)
    5. Print a diff-friendly flattened field list for quick auditing

    After running this:
    - Review the JSON to fix any type mismatches (Spark may infer integers as
      strings, dates as strings, etc. depending on the sample data)
    - Add the output JSON path to config/pipeline_config.yaml under schema.path
    """
    try:
        from pyspark.sql import SparkSession
    except ImportError:
        sys.exit("PySpark not found. Install it or run via spark-submit.")

    spark = (
        SparkSession.builder
        .appName("SchemaInference")
        .config("spark.sql.debug.maxToStringFields", "3000")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    print(f"\nReading XML sample: {input_path}  (rowTag={row_tag})")

    df = (
        spark.read
        .format("com.databricks.spark.xml")
        .option("rowTag",            row_tag)
        .option("inferSchema",       "true")
        .option("samplingRatio",     str(sampling_ratio))
        .option("ignoreNamespace",   "true")
        .option("attributePrefix",   "attr_")
        .option("valueTag",          "_value")
        .option("multiLine",         "true")
        .option("treatEmptyValuesAsNulls", "true")
        .load(input_path)
    )

    # ── Print schema tree for visual inspection ────────────────────────────
    print("\n── Inferred schema (tree) ──────────────────────────────────────")
    df.printSchema()

    # ── Print flat field list for audit ───────────────────────────────────
    print("\n── Flat field inventory ────────────────────────────────────────")
    _print_flat_fields(df.schema)

    # ── Serialize to JSON ─────────────────────────────────────────────────
    schema_json = df.schema.jsonValue()

    _write_json(schema_json, output_path)
    print(f"\nSchema JSON written to: {output_path}")
    print("Review it, fix type overrides if needed, then set schema.path in")
    print("config/pipeline_config.yaml (or config/env/<env>.yaml for GCP).")

    spark.stop()


def _print_flat_fields(schema, prefix=""):
    """Recursively print every leaf field as a dotted path."""
    from pyspark.sql.types import ArrayType, StructType
    for field in schema.fields:
        path = f"{prefix}.{field.name}" if prefix else field.name
        dtype = field.dataType
        if isinstance(dtype, StructType):
            _print_flat_fields(dtype, path)
        elif isinstance(dtype, ArrayType) and isinstance(dtype.elementType, StructType):
            print(f"  {path}[]  → ArrayType(StructType)")
            _print_flat_fields(dtype.elementType, f"{path}[]")
        else:
            print(f"  {path}  → {dtype.simpleString()}  nullable={field.nullable}")


def _write_json(data: dict, path: str) -> None:
    if path.startswith("gs://"):
        try:
            from google.cloud import storage as gcs
        except ImportError:
            sys.exit("pip install google-cloud-storage for gs:// output")
        without_scheme = path[5:]
        bucket_name, _, blob_name = without_scheme.partition("/")
        content = json.dumps(data, indent=2)
        gcs.Client().bucket(bucket_name).blob(blob_name).upload_from_string(
            content, content_type="application/json"
        )
    else:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Mode: show  (no Spark needed)
# ─────────────────────────────────────────────────────────────────────────────

def show_schema() -> None:
    """
    Load and pretty-print the pre-built schema for sample_deep_ns.xml
    without starting a SparkSession.  Useful for quick inspection and
    for understanding the structure before editing the JSON.
    """
    schema_path = Path(__file__).parent.parent / "config" / "schema_deep_ns.json"
    if not schema_path.exists():
        sys.exit(f"Schema file not found: {schema_path}")

    schema_dict = json.loads(schema_path.read_text())

    print(f"\nSchema: {schema_path}")
    print(f"Top-level fields: {len(schema_dict.get('fields', []))}\n")
    _print_dict_tree(schema_dict, indent=0)

    # ── Summary statistics ─────────────────────────────────────────────────
    total, max_depth = _count_fields(schema_dict)
    print(f"\n── Summary ─────────────────────────────────────────────────────")
    print(f"  Total leaf fields : {total}")
    print(f"  Maximum depth     : {max_depth}")
    print(f"  Array columns     : {_count_arrays(schema_dict)}")
    print(f"\nTo use this schema in the pipeline:")
    print(f"  Set  schema.path: config/schema_deep_ns.json")
    print(f"  Or   schema.path: gs://<bucket>/pyspark_xml_etl/config/schema_deep_ns.json")


def _print_dict_tree(node: dict, indent: int, parent_name: str = "root") -> None:
    """Recursively print the schema JSON as a readable tree."""
    type_str = node.get("type", "?")
    name     = node.get("name", parent_name)
    nullable = node.get("nullable", True)
    null_tag = "" if nullable else " [required]"

    pad = "  " * indent

    if type_str == "struct":
        print(f"{pad}{'└─ ' if indent else ''}{name}  (struct){null_tag}")
        for child in node.get("fields", []):
            _print_dict_tree(child, indent + 1)

    elif type_str == "array":
        et = node.get("elementType", {})
        if isinstance(et, str):
            print(f"{pad}└─ {name}[]  (array<{et}>){null_tag}")
        else:
            et_type = et.get("type", "?")
            print(f"{pad}└─ {name}[]  (array<{et_type}>){null_tag}")
            if et_type == "struct":
                for child in et.get("fields", []):
                    _print_dict_tree(child, indent + 2)

    else:
        print(f"{pad}└─ {name}  ({type_str}){null_tag}")


def _count_fields(node: dict, depth: int = 0) -> tuple[int, int]:
    """Return (total_leaf_fields, max_depth)."""
    type_str = node.get("type", "string")
    if type_str == "struct":
        total = 0
        max_d = depth
        for child in node.get("fields", []):
            c_total, c_depth = _count_fields(child, depth + 1)
            total += c_total
            max_d  = max(max_d, c_depth)
        return total, max_d
    elif type_str == "array":
        et = node.get("elementType", {})
        if isinstance(et, dict):
            return _count_fields(et, depth + 1)
        return 1, depth
    else:
        return 1, depth


def _count_arrays(node: dict) -> int:
    """Count the total number of ArrayType columns."""
    type_str = node.get("type", "string")
    if type_str == "array":
        et = node.get("elementType", {})
        inner = _count_arrays(et) if isinstance(et, dict) else 0
        return 1 + inner
    elif type_str == "struct":
        return sum(_count_arrays(f) for f in node.get("fields", []))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate or inspect the pipeline schema JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # ── infer sub-command ──────────────────────────────────────────────────
    infer_p = sub.add_parser(
        "infer",
        help="Infer schema from a sample XML via spark-xml → save JSON",
    )
    infer_p.add_argument(
        "--input", required=True,
        help="Sample XML path (local or gs://). Use a small file — 10 000 rows max.",
    )
    infer_p.add_argument(
        "--output", default="config/schema_inferred.json",
        help="Output JSON path (local or gs://). Default: config/schema_inferred.json",
    )
    infer_p.add_argument(
        "--row-tag", default="record",
        help="XML element representing one row. Default: record",
    )
    infer_p.add_argument(
        "--sampling-ratio", type=float, default=1.0,
        help="Fraction of rows to sample for inference (default: 1.0 = all rows).",
    )

    # ── show sub-command ───────────────────────────────────────────────────
    sub.add_parser(
        "show",
        help="Pretty-print config/schema_deep_ns.json (no Spark needed)",
    )

    args = parser.parse_args()

    if args.mode == "infer":
        infer_schema(args.input, args.output, args.row_tag, args.sampling_ratio)
    else:
        show_schema()


if __name__ == "__main__":
    main()
