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

FIXTURE_MULTI_DOC_XML = os.path.join(
    os.path.dirname(__file__), "fixtures", "sample_multi_doc.xml"
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


class TestMultiDocXml(unittest.TestCase):
    """
    Tests for XML files containing multiple documents in a single file, each
    preceded by its own <?xml version="..."?> declaration and with no shared
    root wrapper element.

    spark-xml's XmlInputFormat scans bytes for <rowTag> / </rowTag> boundaries
    and skips <?xml?> declarations between records, so the pipeline handles
    this format natively.

    The fixture (sample_multi_doc.xml) contains 4 <record> elements, each
    with its own <?xml?> preamble and no enclosing root element.
    """

    @classmethod
    def setUpClass(cls):
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("xml_multidoc_tests")
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

    def test_all_records_read(self):
        """spark-xml must read all 4 records despite 4 separate <?xml?> preambles."""
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_MULTI_DOC_XML, "record", MINIMAL_SCHEMA)
        self.assertEqual(df.count(), 4)

    def test_record_ids_correct(self):
        """Record IDs from all 4 documents must be present and correct."""
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_MULTI_DOC_XML, "record", MINIMAL_SCHEMA)
        ids = {row["record_id"] for row in df.select("record_id").collect()}
        self.assertSetEqual(
            ids,
            {"REC-MULTI-001", "REC-MULTI-002", "REC-MULTI-003", "REC-MULTI-004"},
        )

    def test_month_values_across_documents(self):
        """Month values (1-4) from all four documents must all be present."""
        self._skip_if_no_jar()
        from xml_processor import read_xml

        df = read_xml(self.spark, FIXTURE_MULTI_DOC_XML, "record", MINIMAL_SCHEMA)
        months = {row["month"] for row in df.select("month").collect()}
        self.assertSetEqual(months, {1, 2, 3, 4})

    def test_no_corrupt_records(self):
        """None of the 4 records should land in _corrupt_record."""
        self._skip_if_no_jar()
        from xml_processor import read_xml, validate_corrupt_records

        schema_with_corrupt = StructType(
            MINIMAL_SCHEMA.fields
            + [StructField("_corrupt_record", StringType(), True)]
        )
        df = read_xml(self.spark, FIXTURE_MULTI_DOC_XML, "record", schema_with_corrupt)
        validate_corrupt_records(df, max_corrupt=0)


class TestNormalizeMultiDocXml(unittest.TestCase):
    """
    Unit tests for normalize_multi_doc_xml().

    These tests do NOT require spark-xml and run without a SparkSession.
    They verify that the normalizer strips duplicate <?xml?> declarations
    while keeping all record content intact.
    """

    def test_strips_duplicate_declarations(self):
        """Output file must contain exactly one <?xml?> declaration."""
        from xml_processor import normalize_multi_doc_xml

        out = normalize_multi_doc_xml(FIXTURE_MULTI_DOC_XML)
        try:
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            # Count occurrences of the XML declaration
            count = content.count("<?xml")
            self.assertEqual(count, 1, f"Expected 1 <?xml?> declaration, found {count}")
        finally:
            os.unlink(out)

    def test_all_records_present_after_normalization(self):
        """All 4 <record> elements must survive the normalization step."""
        from xml_processor import normalize_multi_doc_xml

        out = normalize_multi_doc_xml(FIXTURE_MULTI_DOC_XML)
        try:
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(content.count("<record>"), 4)
            self.assertEqual(content.count("</record>"), 4)
        finally:
            os.unlink(out)

    def test_record_ids_in_output(self):
        """All four record IDs must appear in the normalized file."""
        from xml_processor import normalize_multi_doc_xml

        out = normalize_multi_doc_xml(FIXTURE_MULTI_DOC_XML)
        try:
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            for rec_id in ("REC-MULTI-001", "REC-MULTI-002", "REC-MULTI-003", "REC-MULTI-004"):
                self.assertIn(rec_id, content)
        finally:
            os.unlink(out)

    def test_custom_output_path(self):
        """normalize_multi_doc_xml must write to the caller-specified path."""
        import tempfile
        from xml_processor import normalize_multi_doc_xml

        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            custom_path = tmp.name

        try:
            result = normalize_multi_doc_xml(FIXTURE_MULTI_DOC_XML, output_path=custom_path)
            self.assertEqual(result, custom_path)
            self.assertTrue(os.path.exists(custom_path))
        finally:
            if os.path.exists(custom_path):
                os.unlink(custom_path)

    def test_already_single_declaration_unchanged_count(self):
        """A file with a single <?xml?> declaration must still produce one in output."""
        import tempfile
        from xml_processor import normalize_multi_doc_xml

        single_doc = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<root><record><record_id>R1</record_id></record></root>\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(single_doc)
            src = tmp.name

        try:
            out = normalize_multi_doc_xml(src)
            try:
                with open(out, encoding="utf-8") as fh:
                    content = fh.read()
                self.assertEqual(content.count("<?xml"), 1)
                self.assertIn("R1", content)
            finally:
                os.unlink(out)
        finally:
            os.unlink(src)


if __name__ == "__main__":
    unittest.main()
