"""
PySpark XML ETL Pipeline — main entry point.

Local usage
-----------
    spark-submit \\
        --packages com.databricks:spark-xml_2.12:0.17.0 \\
        src/etl_pipeline.py \\
        --config config/pipeline_config.yaml

GCP Dataproc usage (submitted by the Airflow DAG)
--------------------------------------------------
    spark-submit \\
        --packages com.databricks:spark-xml_2.12:0.17.0 \\
        gs://<bucket>/pyspark_xml_etl/scripts/etl_pipeline.py \\
        --config gs://<bucket>/pyspark_xml_etl/config/pipeline_config_prod.yaml

The pipeline is fully configuration-driven: schema, source paths, pivot
rules, performance tuning, and sink settings all live in the YAML config.
The config path supports both local filesystem and ``gs://`` URIs.
No column names are hard-coded in this file.
"""

import argparse
import io
import logging
import sys
from pathlib import Path

import yaml

# Allow running directly from the src/ directory or from GCS-deployed src.zip
sys.path.insert(0, str(Path(__file__).parent))

from flattener import flatten_struct, select_output_columns
from pivot_handler import pivot_repeating_elements
from schema_builder import load_schema
from utils import build_spark_session, repartition_df, write_output
from xml_processor import read_xml, validate_corrupt_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("etl_pipeline")


# ── Config loading (local + GCS) ──────────────────────────────────────────────

def _read_yaml_text(config_path: str) -> str:
    """Return raw YAML text from a local path or a ``gs://`` URI."""
    if config_path.startswith("gs://"):
        try:
            from google.cloud import storage as gcs
        except ImportError as exc:
            raise ImportError(
                "google-cloud-storage is required for gs:// config paths."
            ) from exc
        without_scheme = config_path[5:]
        bucket_name, _, blob_name = without_scheme.partition("/")
        client = gcs.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        if not blob.exists():
            raise FileNotFoundError(f"GCS config not found: {config_path}")
        return blob.download_as_text(encoding="utf-8")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {config_path}")
    return path.read_text(encoding="utf-8")


def load_config(config_path: str) -> dict:
    """Load the YAML pipeline config from a local path or ``gs://`` URI."""
    raw = _read_yaml_text(config_path)
    cfg = yaml.safe_load(raw)
    logger.info("Config loaded from %s", config_path)
    return cfg


# ── Pipeline stages ───────────────────────────────────────────────────────────

def stage_read(spark, cfg: dict):
    schema = load_schema(cfg["schema"]["path"])
    source = cfg["source"]
    df = read_xml(
        spark=spark,
        path=source["path"],
        row_tag=source["row_tag"],
        schema=schema,
        options=source.get("reader_options", {}),
    )
    max_corrupt = cfg.get("quality", {}).get("max_corrupt_records", 0)
    validate_corrupt_records(df, max_corrupt=max_corrupt)
    return df


def stage_repartition_initial(df, cfg: dict):
    n = cfg.get("performance", {}).get("initial_partitions")
    if n:
        col = cfg.get("performance", {}).get("partition_col")
        df = repartition_df(df, n, partition_col=col)
    return df


def stage_flatten(df, cfg: dict):
    if cfg.get("flatten_structs", True):
        logger.info("Flattening nested struct columns")
        df = flatten_struct(df)
    return df


def stage_pivot(df, cfg: dict):
    specs = cfg.get("pivot_specs", [])
    if specs:
        logger.info("Applying %d pivot specification(s)", len(specs))
        df = pivot_repeating_elements(df, specs)
    return df


def stage_select(df, cfg: dict):
    columns = cfg.get("output_columns")
    if columns:
        strict = cfg.get("strict_output_columns", False)
        df = select_output_columns(df, columns, strict=strict)
    return df


def stage_write(df, cfg: dict):
    perf = cfg.get("performance", {})
    n_out = perf.get("output_partitions", 200)
    col_out = perf.get("output_partition_col")
    df = repartition_df(df, n_out, partition_col=col_out)
    write_output(df, cfg["sink"])


# ── Orchestration ─────────────────────────────────────────────────────────────

def run(config_path: str) -> None:
    cfg = load_config(config_path)

    spark = build_spark_session(
        app_name=cfg.get("app_name", "XMLETLPipeline"),
        spark_conf=cfg.get("spark_conf", {}),
    )

    try:
        logger.info("=== Stage 1/6: Read XML ===")
        df = stage_read(spark, cfg)

        logger.info("=== Stage 2/6: Initial repartition ===")
        df = stage_repartition_initial(df, cfg)

        logger.info("=== Stage 3/6: Flatten structs ===")
        df = stage_flatten(df, cfg)

        logger.info("=== Stage 4/6: Pivot repeating elements ===")
        df = stage_pivot(df, cfg)

        logger.info("=== Stage 5/6: Select output columns ===")
        df = stage_select(df, cfg)

        logger.info("=== Stage 6/6: Write output ===")
        stage_write(df, cfg)

        logger.info("Pipeline finished successfully.")

    except Exception:
        logger.exception("Pipeline failed — see traceback above.")
        raise

    finally:
        spark.stop()


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Configuration-driven PySpark XML ETL pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="config/pipeline_config.yaml",
        help="Path to the YAML pipeline configuration file.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(args.config)
