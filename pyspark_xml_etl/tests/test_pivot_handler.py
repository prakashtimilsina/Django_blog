"""Tests for pivot_handler module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pivot_handler import explode_array_to_columns, pivot_repeating_elements

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)


def _get_spark():
    return (
        SparkSession.builder.master("local[2]")
        .appName("pivot_tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


class TestExplodeArrayToColumns(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spark = _get_spark()

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _make_scalar_df(self):
        data = [
            (1, ["a", "b", "c"]),
            (2, ["x"]),
            (3, None),
        ]
        schema = StructType(
            [
                StructField("id",   IntegerType(), False),
                StructField("codes", ArrayType(StringType(), True), True),
            ]
        )
        return self.spark.createDataFrame(data, schema)

    def _make_struct_df(self):
        inner_schema = StructType(
            [
                StructField("tooth_id", StringType(), True),
                StructField("surface",  StringType(), True),
            ]
        )
        data = [
            (1, [{"tooth_id": "11", "surface": "B"}, {"tooth_id": "21", "surface": "L"}]),
            (2, [{"tooth_id": "36", "surface": "O"}]),
        ]
        schema = StructType(
            [
                StructField("id",    IntegerType(), False),
                StructField("teeth", ArrayType(inner_schema, True), True),
            ]
        )
        return self.spark.createDataFrame(data, schema)

    # ── Scalar array tests ────────────────────────────────────────────────

    def test_scalar_n_columns_created(self):
        df = explode_array_to_columns(self._make_scalar_df(), "codes", "code", 5)
        for i in range(1, 6):
            self.assertIn(f"code_{i}", df.columns)

    def test_scalar_values_correct(self):
        df = explode_array_to_columns(self._make_scalar_df(), "codes", "code", 3)
        row = df.filter(F.col("id") == 1).first()
        self.assertEqual(row["code_1"], "a")
        self.assertEqual(row["code_2"], "b")
        self.assertEqual(row["code_3"], "c")

    def test_scalar_short_array_padded_with_null(self):
        df = explode_array_to_columns(self._make_scalar_df(), "codes", "code", 3)
        row = df.filter(F.col("id") == 2).first()
        self.assertEqual(row["code_1"], "x")
        self.assertIsNone(row["code_2"])
        self.assertIsNone(row["code_3"])

    def test_null_array_produces_nulls(self):
        df = explode_array_to_columns(self._make_scalar_df(), "codes", "code", 2)
        row = df.filter(F.col("id") == 3).first()
        self.assertIsNone(row["code_1"])
        self.assertIsNone(row["code_2"])

    def test_original_array_col_still_present(self):
        df = explode_array_to_columns(self._make_scalar_df(), "codes", "code", 2)
        self.assertIn("codes", df.columns)

    # ── Struct array tests ────────────────────────────────────────────────

    def test_struct_subfield_extraction(self):
        df = explode_array_to_columns(
            self._make_struct_df(), "teeth", "tooth_id", 3, element_subfield="tooth_id"
        )
        row = df.filter(F.col("id") == 1).first()
        self.assertEqual(row["tooth_id_1"], "11")
        self.assertEqual(row["tooth_id_2"], "21")
        self.assertIsNone(row["tooth_id_3"])

    def test_struct_short_array_null_padded(self):
        df = explode_array_to_columns(
            self._make_struct_df(), "teeth", "tooth_id", 3, element_subfield="tooth_id"
        )
        row = df.filter(F.col("id") == 2).first()
        self.assertEqual(row["tooth_id_1"], "36")
        self.assertIsNone(row["tooth_id_2"])

    # ── Error handling ────────────────────────────────────────────────────

    def test_missing_column_raises(self):
        df = self._make_scalar_df()
        with self.assertRaises(ValueError):
            explode_array_to_columns(df, "nonexistent", "x", 3)

    def test_non_array_column_raises(self):
        df = self._make_scalar_df()
        with self.assertRaises(TypeError):
            explode_array_to_columns(df, "id", "x", 3)

    # ── Exactly 10 positions (business requirement) ───────────────────────

    def test_exactly_10_tooth_positions(self):
        df = explode_array_to_columns(
            self._make_struct_df(), "teeth", "tooth_id", 10, element_subfield="tooth_id"
        )
        created = [c for c in df.columns if c.startswith("tooth_id_")]
        self.assertEqual(len(created), 10)
        self.assertIn("tooth_id_10", df.columns)


class TestPivotRepeatingElements(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spark = _get_spark()

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _make_df(self):
        data = [(1, ["A", "B"], ["X"])]
        schema = StructType(
            [
                StructField("id",    IntegerType(), False),
                StructField("codes", ArrayType(StringType(), True), True),
                StructField("flags", ArrayType(StringType(), True), True),
            ]
        )
        return self.spark.createDataFrame(data, schema)

    def test_multiple_specs_applied(self):
        specs = [
            {"array_col": "codes", "prefix": "code", "n": 3},
            {"array_col": "flags", "prefix": "flag", "n": 2},
        ]
        df = pivot_repeating_elements(self._make_df(), specs)
        for i in range(1, 4):
            self.assertIn(f"code_{i}", df.columns)
        for i in range(1, 3):
            self.assertIn(f"flag_{i}", df.columns)

    def test_drop_source_removes_column(self):
        specs = [{"array_col": "codes", "prefix": "code", "n": 2, "drop_source": True}]
        df = pivot_repeating_elements(self._make_df(), specs)
        self.assertNotIn("codes", df.columns)

    def test_empty_specs_returns_unchanged(self):
        original = self._make_df()
        result = pivot_repeating_elements(original, [])
        self.assertEqual(original.columns, result.columns)


if __name__ == "__main__":
    unittest.main()
