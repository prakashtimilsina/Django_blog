"""
Pivot repeating XML array elements into flat, numbered columns.

Problem
-------
XML sources often encode repeated sub-elements as arrays:

    <teeth>
      <tooth><id>11</id></tooth>
      <tooth><id>21</id></tooth>
      ...up to N teeth...
    </teeth>

After spark-xml reads this, ``teeth`` becomes an ArrayType column.
Business requirements typically want exactly K positions as individual columns:

    tooth_id_1, tooth_id_2, ..., tooth_id_10

If an array has fewer than K elements the extra columns are null.
If an array has more than K elements the extras are silently ignored.

This module handles both scalar arrays (ArrayType of primitives) and
struct arrays (ArrayType of StructType) via the ``element_subfield``
parameter.
"""

import logging
from typing import Any, Dict, List, Optional

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType

logger = logging.getLogger(__name__)


def explode_array_to_columns(
    df: DataFrame,
    array_col: str,
    prefix: str,
    n: int,
    element_subfield: Optional[str] = None,
) -> DataFrame:
    """
    Extract exactly *n* positions from an array column into flat columns.

    Parameters
    ----------
    df               : input DataFrame
    array_col        : name of the ArrayType column
    prefix           : column-name prefix, e.g. ``"tooth_id"``
    n                : number of positions to extract (1-indexed in output)
    element_subfield : for struct arrays, the sub-field name to extract;
                       ``None`` for scalar arrays

    Returns
    -------
    DataFrame with *n* new columns named ``{prefix}_1`` … ``{prefix}_{n}``
    and the original ``array_col`` still present (drop it downstream if
    not needed).

    Examples
    --------
    Scalar array::

        explode_array_to_columns(df, "procedure_codes", "proc", 10)
        # → proc_1 … proc_10

    Struct array (pull "id" from each struct element)::

        explode_array_to_columns(df, "teeth", "tooth_id", 10, "id")
        # → tooth_id_1 … tooth_id_10
    """
    if array_col not in df.columns:
        raise ValueError(
            f"Column '{array_col}' not found. Available: {df.columns}"
        )

    field_dtype = df.schema[array_col].dataType
    if not isinstance(field_dtype, ArrayType):
        raise TypeError(
            f"Column '{array_col}' is {type(field_dtype).__name__}, expected ArrayType."
        )

    logger.info(
        "Pivoting '%s' → %d columns  prefix='%s'  subfield=%s",
        array_col, n, prefix, element_subfield,
    )

    for i in range(n):
        col_alias = f"{prefix}_{i + 1}"
        element_expr = F.col(f"`{array_col}`").getItem(i)

        if element_subfield:
            element_expr = element_expr.getField(element_subfield)

        df = df.withColumn(col_alias, element_expr)

    return df


def pivot_repeating_elements(
    df: DataFrame,
    pivot_specs: List[Dict[str, Any]],
) -> DataFrame:
    """
    Apply a list of pivot specifications to a DataFrame.

    Each specification is a dict with the following keys:

    ============= ======= ================================================
    Key           Type    Description
    ============= ======= ================================================
    array_col     str     ArrayType column to pivot (required)
    prefix        str     Output column prefix (required)
    n             int     Number of positions to extract (required)
    element_subfield str  Sub-field for struct arrays; omit for scalars
    drop_source   bool    Drop the source array col after pivoting
                          (default: False)
    ============= ======= ================================================

    Example YAML config block that maps to a list of these dicts::

        pivot_specs:
          - array_col: teeth
            prefix: tooth_id
            n: 10
            element_subfield: id
          - array_col: procedure_codes
            prefix: proc_code
            n: 5
    """
    for spec in pivot_specs:
        array_col = spec["array_col"]
        df = explode_array_to_columns(
            df,
            array_col=array_col,
            prefix=spec["prefix"],
            n=spec["n"],
            element_subfield=spec.get("element_subfield"),
        )
        if spec.get("drop_source", False) and array_col in df.columns:
            df = df.drop(array_col)

    return df
