"""
SparkSession factory, partitioning helpers, and output writer.
"""

import logging
from typing import Any, Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession

logger = logging.getLogger(__name__)

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


def build_spark_session(
    app_name: str,
    spark_conf: Optional[Dict[str, Any]] = None,
    extra_jars: Optional[List[str]] = None,
) -> SparkSession:
    """
    Build a production-grade SparkSession.

    Parameters
    ----------
    app_name   : value shown in the Spark UI
    spark_conf : caller-supplied config overrides (merged on top of defaults)
    extra_jars : additional JAR paths to add to ``spark.jars``

    Notes
    -----
    spark-xml must be on the classpath before this function is called,
    either via ``--packages`` on spark-submit or by adding the JAR path
    to ``extra_jars``.
    """
    builder = SparkSession.builder.appName(app_name)

    merged = {**_SPARK_DEFAULTS, **(spark_conf or {})}
    for key, value in merged.items():
        builder = builder.config(key, str(value))

    if extra_jars:
        builder = builder.config("spark.jars", ",".join(extra_jars))

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    logger.info(
        "SparkSession ready: app='%s'  Spark %s", app_name, spark.version
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
    path         : output path (local, HDFS, s3a://, gs://, abfs://)
    mode         : write mode (overwrite | append | ignore | error)
    partition_by : list of column names for directory-partitioning
    options      : dict of format-specific writer options
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
