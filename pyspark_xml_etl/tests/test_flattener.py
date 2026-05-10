"""Tests for flattener module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flattener import flatten_struct, select_output_columns

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    IntegerType,
    StringType,
    StructField,
    StructType,
)


def _get_spark():
    return (
        SparkSession.builder.master("local[2]")
        .appName("flattener_tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


class TestFlattenStruct(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spark = _get_spark()

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _make_nested_df(self):
        inner = StructType(
            [
                StructField("city",  StringType(), True),
                StructField("state", StringType(), True),
            ]
        )
        outer = StructType(
            [
                StructField("id",      IntegerType(), False),
                StructField("address", inner,          True),
            ]
        )
        return self.spark.createDataFrame(
            [(1, {"city": "Springfield", "state": "IL"})], outer
        )

    def _make_double_nested_df(self):
        deep = StructType([StructField("mobile", StringType(), True)])
        mid = StructType(
            [
                StructField("email", StringType(), True),
                StructField("phone", deep, True),
            ]
        )
        outer = StructType(
            [
                StructField("id",      IntegerType(), False),
                StructField("contact", mid, True),
            ]
        )
        return self.spark.createDataFrame(
            [(1, {"email": "a@b.com", "phone": {"mobile": "555-1234"}})], outer
        )

    def test_single_level_struct_flattened(self):
        df = flatten_struct(self._make_nested_df())
        self.assertIn("address__city",  df.columns)
        self.assertIn("address__state", df.columns)
        self.assertNotIn("address", df.columns)

    def test_values_preserved_after_flatten(self):
        df = flatten_struct(self._make_nested_df())
        row = df.first()
        self.assertEqual(row["address__city"], "Springfield")
        self.assertEqual(row["address__state"], "IL")

    def test_double_nested_struct_flattened(self):
        df = flatten_struct(self._make_double_nested_df())
        self.assertIn("contact__email",          df.columns)
        self.assertIn("contact__phone__mobile",  df.columns)
        self.assertNotIn("contact", df.columns)

    def test_flat_df_returned_unchanged(self):
        flat_schema = StructType(
            [
                StructField("a", StringType(),  True),
                StructField("b", IntegerType(), True),
            ]
        )
        flat_df = self.spark.createDataFrame([("x", 1)], flat_schema)
        result = flatten_struct(flat_df)
        self.assertEqual(sorted(flat_df.columns), sorted(result.columns))

    def test_primitive_id_col_preserved(self):
        df = flatten_struct(self._make_nested_df())
        self.assertIn("id", df.columns)


class TestSelectOutputColumns(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spark = _get_spark()

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _make_df(self):
        schema = StructType(
            [
                StructField("a", StringType(),  True),
                StructField("b", IntegerType(), True),
                StructField("c", StringType(),  True),
            ]
        )
        return self.spark.createDataFrame([("x", 1, "z")], schema)

    def test_selects_present_columns(self):
        df = select_output_columns(self._make_df(), ["a", "b"])
        self.assertEqual(df.columns, ["a", "b"])

    def test_preserves_order(self):
        df = select_output_columns(self._make_df(), ["c", "a"])
        self.assertEqual(df.columns, ["c", "a"])

    def test_missing_columns_skipped_by_default(self):
        df = select_output_columns(self._make_df(), ["a", "MISSING"])
        self.assertEqual(df.columns, ["a"])

    def test_missing_columns_raise_when_strict(self):
        with self.assertRaises(KeyError):
            select_output_columns(self._make_df(), ["a", "MISSING"], strict=True)

    def test_all_missing_returns_empty_select(self):
        df = select_output_columns(self._make_df(), ["X", "Y"])
        self.assertEqual(df.columns, [])


if __name__ == "__main__":
    unittest.main()
