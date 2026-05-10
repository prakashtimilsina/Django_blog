"""Tests for schema_builder module."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema_builder import _build_struct_type, load_schema, schema_to_json

from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)


class TestBuildStructType(unittest.TestCase):

    def test_primitive_fields(self):
        fields = [
            {"name": "id",    "type": "string",  "nullable": False},
            {"name": "count", "type": "integer", "nullable": True},
            {"name": "score", "type": "double",  "nullable": True},
        ]
        schema = _build_struct_type(fields)
        self.assertIsInstance(schema, StructType)
        self.assertEqual(len(schema.fields), 3)
        self.assertEqual(schema["id"].dataType, StringType())
        self.assertEqual(schema["count"].dataType, IntegerType())
        self.assertFalse(schema["id"].nullable)

    def test_nested_struct(self):
        fields = [
            {
                "name": "address",
                "type": "struct",
                "fields": [
                    {"name": "city",  "type": "string"},
                    {"name": "state", "type": "string"},
                ],
            }
        ]
        schema = _build_struct_type(fields)
        addr_type = schema["address"].dataType
        self.assertIsInstance(addr_type, StructType)
        self.assertIn("city", [f.name for f in addr_type.fields])

    def test_array_of_strings(self):
        fields = [
            {
                "name": "codes",
                "type": "array",
                "elementType": {"type": "string"},
                "containsNull": True,
            }
        ]
        schema = _build_struct_type(fields)
        codes_type = schema["codes"].dataType
        self.assertIsInstance(codes_type, ArrayType)
        self.assertEqual(codes_type.elementType, StringType())

    def test_array_of_structs(self):
        fields = [
            {
                "name": "items",
                "type": "array",
                "elementType": {
                    "type": "struct",
                    "fields": [{"name": "val", "type": "long"}],
                },
            }
        ]
        schema = _build_struct_type(fields)
        items_type = schema["items"].dataType
        self.assertIsInstance(items_type, ArrayType)
        self.assertIsInstance(items_type.elementType, StructType)

    def test_type_aliases(self):
        """int → IntegerType, bigint → LongType, bool → BooleanType."""
        fields = [
            {"name": "a", "type": "int"},
            {"name": "b", "type": "bigint"},
        ]
        schema = _build_struct_type(fields)
        self.assertEqual(schema["a"].dataType, IntegerType())
        self.assertEqual(schema["b"].dataType, LongType())

    def test_unknown_type_defaults_to_string(self):
        fields = [{"name": "x", "type": "blob"}]
        schema = _build_struct_type(fields)
        self.assertEqual(schema["x"].dataType, StringType())


class TestLoadSchema(unittest.TestCase):

    def _write_json(self, data: dict) -> str:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def tearDown(self):
        for attr in ("_tmp_path",):
            path = getattr(self, attr, None)
            if path and os.path.exists(path):
                os.unlink(path)

    def test_load_custom_format(self):
        self._tmp_path = self._write_json(
            {"fields": [{"name": "col_a", "type": "string"}]}
        )
        schema = load_schema(self._tmp_path)
        self.assertIsInstance(schema, StructType)
        self.assertEqual(schema.fieldNames(), ["col_a"])

    def test_load_native_spark_format(self):
        native = {
            "type": "struct",
            "fields": [
                {
                    "name": "patient_id",
                    "type": "string",
                    "nullable": True,
                    "metadata": {},
                }
            ],
        }
        self._tmp_path = self._write_json(native)
        schema = load_schema(self._tmp_path)
        self.assertIsInstance(schema, StructType)
        self.assertEqual(schema.fieldNames(), ["patient_id"])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_schema("/nonexistent/path/schema.json")


class TestSchemaToJson(unittest.TestCase):

    def test_roundtrip(self):
        original = StructType(
            [
                StructField("id",    StringType(),  False),
                StructField("value", DoubleType(),  True),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out", "schema.json")
            schema_to_json(original, path)
            self.assertTrue(os.path.exists(path))
            reloaded = load_schema(path)
        self.assertEqual(original.fieldNames(), reloaded.fieldNames())
        self.assertEqual(original["id"].dataType, reloaded["id"].dataType)


if __name__ == "__main__":
    unittest.main()
