"""
Integration tests for xml_processor.read_xml and read_xml_namespaced.

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

FIXTURE_NS_XML = os.path.join(
    os.path.dirname(__file__), "fixtures", "sample_records_ns.xml"
)

# Minimal schema using LOCAL names only — no namespace prefixes
MINIMAL_SCHEMA = StructType(
    [
        StructField("record_id",     StringType(),  True),
        StructField("source_system", StringType(),  True),
        StructField("year",          IntegerType(), True),
        StructField("month",         IntegerType(), True),
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
    """Tests against the plain (non-namespaced) fixture."""

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
                "spark-xml JAR not on classpath — "
                "run with --packages com.databricks:spark-xml_2.12:0.17.0"
            )

    def test_row_count(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_XML, "record", MINIMAL_SCHEMA)
        self.assertEqual(df.count(), 2)

    def test_column_names_present(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_XML, "record", MINIMAL_SCHEMA)
        for col in MINIMAL_SCHEMA.fieldNames():
            self.assertIn(col, df.columns)

    def test_record_ids(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_XML, "record", MINIMAL_SCHEMA)
        ids = {row["record_id"] for row in df.select("record_id").collect()}
        self.assertSetEqual(ids, {"REC-001", "REC-002"})

    def test_validate_no_corrupt_records(self):
        self._skip_if_no_jar()
        from xml_processor import read_xml, validate_corrupt_records

        schema_with_corrupt = StructType(
            MINIMAL_SCHEMA.fields
            + [StructField("_corrupt_record", StringType(), True)]
        )
        df = read_xml(self.spark, FIXTURE_XML, "record", schema_with_corrupt)
        validate_corrupt_records(df, max_corrupt=0)

    def test_prefixed_row_tag_raises(self):
        """read_xml must reject 'ns:tag' row_tag values immediately."""
        from xml_processor import read_xml

        with self.assertRaises(ValueError):
            read_xml(self.spark, FIXTURE_XML, "ehr:record", MINIMAL_SCHEMA)


class TestReadXmlNamespaced(unittest.TestCase):
    """
    Tests against the namespaced fixture (sample_records_ns.xml).

    Key assertion: the same MINIMAL_SCHEMA (using local names only) works
    unchanged even though the XML source uses ehr:record_id, ehr:year, etc.
    This proves that ignoreNamespace=true strips prefixes transparently.
    """

    @classmethod
    def setUpClass(cls):
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("xml_ns_tests")
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
                "spark-xml JAR not on classpath — "
                "run with --packages com.databricks:spark-xml_2.12:0.17.0"
            )

    def test_namespaced_xml_reads_with_local_name_schema(self):
        """
        ehr:record_id maps to column 'record_id' — schema unchanged.

        This is the core guarantee: when ignoreNamespace=true, the schema
        always uses local names regardless of namespace prefixes in the XML.
        """
        self._skip_if_no_jar()
        from xml_processor import read_xml_namespaced

        df = read_xml_namespaced(
            self.spark, FIXTURE_NS_XML, "record", MINIMAL_SCHEMA
        )
        self.assertEqual(df.count(), 2)
        for col in MINIMAL_SCHEMA.fieldNames():
            self.assertIn(col, df.columns)

    def test_namespaced_record_ids(self):
        """Values are read correctly despite namespace prefixes."""
        self._skip_if_no_jar()
        from xml_processor import read_xml_namespaced

        df = read_xml_namespaced(
            self.spark, FIXTURE_NS_XML, "record", MINIMAL_SCHEMA
        )
        ids = {row["record_id"] for row in df.select("record_id").collect()}
        self.assertSetEqual(ids, {"REC-NS-001", "REC-NS-002"})

    def test_mixed_namespace_prefixes_on_same_record(self):
        """
        Elements using different prefixes (ehr:, dental:, billing:) inside
        the same <ehr:record> all resolve to their local names correctly.
        """
        self._skip_if_no_jar()
        from xml_processor import read_xml_namespaced
        from pyspark.sql.types import StructType, StructField, StringType

        schema = StructType([
            StructField("record_id", StringType(), True),
        ])
        df = read_xml_namespaced(
            self.spark, FIXTURE_NS_XML, "record", schema
        )
        self.assertEqual(df.count(), 2)

    def test_plain_read_xml_also_works_on_namespaced_file(self):
        """
        read_xml (not the _namespaced variant) also works because
        ignoreNamespace=true is in the shared defaults.
        The _namespaced variant just adds excludeAttribute for cleaner output.
        """
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(
            self.spark, FIXTURE_NS_XML, "record", MINIMAL_SCHEMA
        )
        self.assertEqual(df.count(), 2)

    def test_xsi_nil_element_reads_as_null(self):
        """
        <ehr:diagnoses xsi:nil="true"/> must produce a null diagnoses column,
        not a corrupt record.  REC-NS-001 has this pattern.
        """
        self._skip_if_no_jar()
        from xml_processor import read_xml_namespaced
        from pyspark.sql import functions as F
        from pyspark.sql.types import ArrayType, StringType, StructField, StructType

        inner = StructType([StructField("icd_code", StringType(), True)])
        schema = StructType([
            StructField("record_id", StringType(), True),
            StructField("diagnoses", ArrayType(inner, True), True),
        ])
        df = read_xml_namespaced(
            self.spark, FIXTURE_NS_XML, "record", schema
        )
        row = df.filter(F.col("record_id") == "REC-NS-001").first()
        # xsi:nil="true" on the element → the column value must be null
        self.assertIsNone(row["diagnoses"])


if __name__ == "__main__":
    unittest.main()
