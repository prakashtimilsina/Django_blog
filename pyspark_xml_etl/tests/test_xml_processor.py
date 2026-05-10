"""
Integration tests for xml_processor.read_xml.

These tests require the spark-xml JAR on the classpath.  Run with:

    spark-submit \\
        --packages com.databricks:spark-xml_2.12:0.17.0 \\
        --py-files src/ \\
        -m pytest tests/test_xml_processor.py -v

If spark-xml is not available the tests are skipped automatically.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    IntegerType,
    StringType,
    StructField,
    StructType,
)

FIXTURE_XML = os.path.join(
    os.path.dirname(__file__), "fixtures", "sample_records.xml"
)

# Minimal schema matching the fixture's top-level fields
MINIMAL_SCHEMA = StructType(
    [
        StructField("record_id",    StringType(),  True),
        StructField("source_system",StringType(),  True),
        StructField("year",         IntegerType(), True),
        StructField("month",        IntegerType(), True),
    ]
)


def _spark_xml_available(spark: SparkSession) -> bool:
    try:
        (
            spark.read.format("com.databricks.spark.xml")
            .option("rowTag", "record")
            .schema(MINIMAL_SCHEMA)
            .load(FIXTURE_XML)
            .limit(1)
            .count()
        )
        return True
    except Exception:
        return False


class TestReadXml(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("xml_processor_tests")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.ui.enabled", "false")
            .getOrCreate()
        )
        cls.spark_xml_ok = _spark_xml_available(cls.spark)

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _skip_if_no_jar(self):
        if not self.spark_xml_ok:
            self.skipTest(
                "spark-xml JAR not on classpath; "
                "run with --packages com.databricks:spark-xml_2.12:0.17.0"
            )

    def test_row_count(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(
            self.spark, FIXTURE_XML, row_tag="record", schema=MINIMAL_SCHEMA
        )
        self.assertEqual(df.count(), 2)

    def test_column_names_present(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(
            self.spark, FIXTURE_XML, row_tag="record", schema=MINIMAL_SCHEMA
        )
        for col in MINIMAL_SCHEMA.fieldNames():
            self.assertIn(col, df.columns)

    def test_record_ids(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(
            self.spark, FIXTURE_XML, row_tag="record", schema=MINIMAL_SCHEMA
        )
        ids = {row["record_id"] for row in df.select("record_id").collect()}
        self.assertSetEqual(ids, {"REC-001", "REC-002"})

    def test_validate_no_corrupt_records(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml, validate_corrupt_records

        schema_with_corrupt = StructType(
            MINIMAL_SCHEMA.fields
            + [StructField("_corrupt_record", StringType(), True)]
        )
        df = read_xml(
            self.spark,
            FIXTURE_XML,
            row_tag="record",
            schema=schema_with_corrupt,
        )
        validate_corrupt_records(df, max_corrupt=0)


if __name__ == "__main__":
    unittest.main()
