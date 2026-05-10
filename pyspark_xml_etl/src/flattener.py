"""
Recursive struct flattener and column selector.

After spark-xml parses deeply-nested XML, the DataFrame contains nested
StructType columns.  This module walks the schema tree and promotes every
leaf field to the top level, joining ancestor names with double-underscores.

    patient.address.city  →  patient__address__city

Flattening is done with a single ``select()`` per recursion level (not
``withColumn`` in a loop) so Catalyst generates an efficient plan.
"""

import logging
from typing import List, Optional

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StructType

logger = logging.getLogger(__name__)


def _expand_fields(schema: StructType, prefix: str = "") -> List:
    """
    Recursively collect Column expressions for every leaf field.

    ArrayType columns are left as-is so the pivot_handler can process them
    later; only StructType columns are descended into.
    """
    expressions = []
    for field in schema.fields:
        qualified_name = f"{prefix}.`{field.name}`" if prefix else f"`{field.name}`"
        alias = f"{prefix}__{field.name}" if prefix else field.name

        if isinstance(field.dataType, StructType):
            # Descend into the struct
            nested = _expand_fields(field.dataType, prefix=alias.replace("__", "__"))
            expressions.extend(nested)
        else:
            expressions.append(F.col(qualified_name).alias(alias))

    return expressions


def flatten_struct(df: DataFrame) -> DataFrame:
    """
    Flatten all top-level StructType columns in *df* into individual columns.

    Non-struct columns (including ArrayType) are passed through unchanged.
    The function recurses until no StructType columns remain at the top level.

    Column naming convention: ancestor names joined by ``__``.
    Example: ``address__city``, ``contact__phone__mobile``
    """
    has_struct = any(
        isinstance(f.dataType, StructType) for f in df.schema.fields
    )
    if not has_struct:
        return df

    expressions = _expand_fields(df.schema)
    df = df.select(expressions)
    logger.debug("Flattened struct columns — now %d top-level columns", len(df.columns))

    # Recurse: nested structs become visible only after the outer struct is
    # expanded, so one pass may not be enough for deeply nested schemas.
    return flatten_struct(df)


def select_output_columns(
    df: DataFrame,
    columns: List[str],
    strict: bool = False,
) -> DataFrame:
    """
    Select a specific subset of columns from the DataFrame.

    Parameters
    ----------
    df      : input DataFrame
    columns : ordered list of column names to retain
    strict  : if True, raise on any missing column; if False, skip silently

    Returns
    -------
    DataFrame containing only the requested (present) columns in order
    """
    available = set(df.columns)
    selected = []
    missing = []

    for col in columns:
        if col in available:
            selected.append(col)
        else:
            missing.append(col)

    if missing:
        msg = f"Requested columns not found in DataFrame: {missing}"
        if strict:
            raise KeyError(msg)
        logger.warning(msg)

    logger.info("Selecting %d of %d available columns", len(selected), len(available))
    return df.select(selected)
