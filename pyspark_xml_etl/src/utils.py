"""
SparkSession factory, partitioning helpers, and output writer.

GCP / Dataproc notes
--------------------
On Dataproc the GCS connector (``gs://`` support) is pre-installed on every
node image.  The ``_DATAPROC_DEFAULTS`` below apply extra GCS connector
tuning that is safe to include on all environments — they are no-ops locally
when the GCS connector is absent.

When Dataproc submits the job it injects Application Default Credentials
automatically, so no explicit credential configuration is needed in this code.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession

logger = logging.getLogger(__name__)

# ── Spark defaults ────────────────────────────────────────────────────────────

_SPARK_DEFAULTS: Dict[str, str] = {
    # Wide schemas with 1 000–3 000 columns need more shuffle partitions
    "spark.sql.shuffle.partitions": "800",
    # Disable broadcast join to prevent OOM with huge schemas
    "spark.sql.autoBroadcastJoinThreshold": "-1",
    # Kryo is faster and more compact than Java serialisation for Spark RDDs
    "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
    # AQE dynamically coalesces small partitions and handles skew (Spark 3+)
    "spark.sql.adaptive.enabled": "true",
    "spark.sql.adaptive.coalescePartitions.enabled": "true",
    "spark.sql.adaptive.skewJoin.enabled": "true",
    # Raise the limit for very wide DataFrames (default 100 fails at 1 000 cols)
    "spark.sql.debug.maxToStringFields": "3000",
}

# GCS connector performance tuning — applied on top of defaults when running
# on Dataproc (detected via DATAPROC_IMAGE_VERSION env var).
_DATAPROC_GCS_TUNING: Dict[str, str] = {
    # 128 MB GCS block size — matches typical Parquet row-group size
    "spark.hadoop.fs.gs.block.size": "134217728",
    # 8 MB read buffer — reduces round-trips for sequential XML reads
    "spark.hadoop.fs.gs.io.buffersize": "8388608",
    # Skip expensive implicit-dir-repair scans on large GCS prefixes
    "spark.hadoop.fs.gs.implicit.dir.repair.enable": "false",
    # Allow Spark to write multiple GCS objects in parallel
    "spark.hadoop.fs.gs.outputstream.type": "FLUSHABLE_COMPOSITE",
    # Avoid small GCS list overhead when checking existing output paths
    "spark.hadoop.fs.gs.glob.flatlist.enable": "true",
}


def _is_dataproc() -> bool:
    """Return True when the process is running on a Dataproc cluster node."""
    return (
        os.environ.get("DATAPROC_IMAGE_VERSION") is not None
        or os.environ.get("DATAPROC_CLUSTER_NAME") is not None
    )


def build_spark_session(
    app_name: str,
    spark_conf: Optional[Dict[str, Any]] = None,
    extra_jars: Optional[List[str]] = None,
) -> SparkSession:
    """
    Build a production-grade SparkSession.

    On Dataproc, GCS connector tuning is applied automatically.
    Locally, GCS tuning entries are silently ignored.

    Parameters
    ----------
    app_name   : value shown in the Spark UI and Cloud Logging
    spark_conf : caller-supplied overrides (merged on top of defaults)
    extra_jars : additional JAR paths added to ``spark.jars``
                 (spark-xml must be on the classpath via ``--packages``
                 or listed here when not using spark-submit --packages)

    Notes
    -----
    On Dataproc, Application Default Credentials are injected automatically
    by the cluster; no credential config is needed here.
    """
    builder = SparkSession.builder.appName(app_name)

    base = dict(_SPARK_DEFAULTS)
    if _is_dataproc():
        logger.info("Dataproc environment detected — applying GCS connector tuning")
        base.update(_DATAPROC_GCS_TUNING)

    merged = {**base, **(spark_conf or {})}
    for key, value in merged.items():
        builder = builder.config(key, str(value))

    if extra_jars:
        builder = builder.config("spark.jars", ",".join(extra_jars))

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    logger.info(
        "SparkSession ready: app='%s'  Spark %s  master=%s",
        app_name,
        spark.version,
        spark.sparkContext.master,
    )
    return spark


def repartition_df(
    df: DataFrame,
    num_partitions: int,
    partition_col: Optional[str] = None,
) -> DataFrame:
    """
    Repartition a DataFrame, optionally by a column to co-locate related rows.

    Parameters
    ----------
    df              : DataFrame to repartition
    num_partitions  : target partition count
    partition_col   : column to hash-partition by (improves downstream joins
                      and sorted writes when a natural key exists)
    """
    if partition_col:
        logger.info(
            "Repartitioning by '%s' → %d partitions", partition_col, num_partitions
        )
        return df.repartition(num_partitions, partition_col)

    logger.info("Repartitioning → %d partitions", num_partitions)
    return df.repartition(num_partitions)


def write_output(df: DataFrame, sink_cfg: Dict[str, Any]) -> None:
    """
    Write the DataFrame using parameters from the sink config block.

    Supported sink config keys
    --------------------------
    format       : output format (parquet | delta | orc | csv | json)
    path         : output path — local, gs://, HDFS, abfs://
    mode         : write mode (overwrite | append | ignore | error)
    partition_by : list of column names for directory-level partitioning
    options      : dict of format-specific writer options

    GCS note: on Dataproc, ``gs://`` paths are handled natively by the
    pre-installed GCS connector — no extra configuration is required here.
    """
    fmt = sink_cfg.get("format", "parquet")
    path = sink_cfg["path"]
    mode = sink_cfg.get("mode", "overwrite")
    options = sink_cfg.get("options", {})
    partition_by: List[str] = sink_cfg.get("partition_by", [])

    logger.info(
        "Writing %s → %s  (mode=%s, partitioned_by=%s)",
        fmt.upper(), path, mode, partition_by or "none",
    )

    writer = df.write.format(fmt).mode(mode).options(**options)

    if partition_by:
        writer = writer.partitionBy(*partition_by)

    writer.save(path)
    logger.info("Write complete.")
