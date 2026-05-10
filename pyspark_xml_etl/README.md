# PySpark XML ETL Pipeline

A **configuration-driven** PySpark pipeline for processing **200 GB+ deeply nested XML** files into flat, analysis-ready Parquet datasets.  All schema definitions, source/sink paths, pivot rules, and performance parameters are declared in YAML/JSON — no Python edits required to onboard a new XML source.

---

## Features

| Capability | Details |
|---|---|
| **Schema-first** | Fixed `StructType` loaded from JSON at runtime — no costly schema inference on massive files |
| **Pivot repeating elements** | Extracts exactly *N* occurrences of any array column into `prefix_1 … prefix_N` flat columns |
| **Recursive struct flattener** | Promotes every nested struct leaf to the top level (`patient__address__city`) |
| **Performance-tuned** | AQE, Kryo serialisation, configurable partition counts, hash-partitioned writes |
| **Data-quality gate** | Corrupt-record counting with configurable abort threshold |
| **Cloud-agnostic** | Paths support local FS, HDFS, S3 (`s3a://`), GCS (`gs://`), ADLS Gen2 (`abfs://`) |

---

## Project Structure

```
pyspark_xml_etl/
├── src/
│   ├── etl_pipeline.py      # Main entry point (spark-submit target)
│   ├── schema_builder.py    # JSON → StructType loader
│   ├── xml_processor.py     # spark-xml DataFrameReader wrapper
│   ├── pivot_handler.py     # Array → flat numbered columns
│   ├── flattener.py         # Recursive struct flattener
│   └── utils.py             # SparkSession factory, writers, partitioners
├── config/
│   ├── pipeline_config.yaml # All pipeline parameters
│   └── schema_config.json   # StructType schema (supports 1 000–3 000 fields)
├── tests/
│   ├── fixtures/
│   │   └── sample_records.xml
│   ├── test_schema_builder.py
│   ├── test_pivot_handler.py
│   ├── test_flattener.py
│   └── test_xml_processor.py
├── scripts/
│   └── init_repo.sh         # Git init + push automation
├── .gitignore
└── requirements.txt
```

---

## Prerequisites

| Tool | Version |
|---|---|
| Apache Spark | 3.4 + |
| Python | 3.9 + |
| spark-xml | `com.databricks:spark-xml_2.12:0.17.0` |
| Java | 11 or 17 |

---

## Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure the pipeline

Edit `config/pipeline_config.yaml`:

```yaml
source:
  path: "s3a://your-bucket/raw/*.xml"
  row_tag: "record"          # the XML element that = one row

sink:
  format: "parquet"
  path: "s3a://your-bucket/processed/output"
  mode: "overwrite"
```

### 3. Define (or generate) the schema

`config/schema_config.json` accepts **both** formats:

**Custom field-list format** (hand-authored):
```json
{
  "fields": [
    { "name": "patient_id", "type": "string",  "nullable": true },
    { "name": "visit_date", "type": "date",    "nullable": true },
    {
      "name": "teeth",
      "type": "array",
      "elementType": {
        "type": "struct",
        "fields": [{ "name": "id", "type": "string" }]
      }
    }
  ]
}
```

**Native Spark format** (generated from an existing DataFrame):
```python
df.schema.json()   # paste output into config/schema_config.json
```

> **Tip for 1 000–3 000 column schemas:** store each logical group in a separate JSON file and merge them at build time, or generate the schema programmatically from a data dictionary spreadsheet and write it with `schema_to_json(schema, "config/schema_config.json")`.

### 4. Configure pivot rules

Add entries to `pivot_specs` in `pipeline_config.yaml` for each repeating XML element:

```yaml
pivot_specs:
  # Dental: extract exactly 10 tooth positions
  - array_col: teeth
    prefix: tooth_id
    n: 10
    element_subfield: id      # pull the "id" sub-field from each struct
    drop_source: true

  # Scalar array of CDT procedure codes
  - array_col: procedure_codes
    prefix: proc_code
    n: 10
    drop_source: true
```

### 5. Run the pipeline

```bash
spark-submit \
    --master yarn \
    --deploy-mode cluster \
    --num-executors 50 \
    --executor-memory 16g \
    --executor-cores 4 \
    --packages com.databricks:spark-xml_2.12:0.17.0 \
    src/etl_pipeline.py \
    --config config/pipeline_config.yaml
```

---

## Running Tests

Unit tests (no JAR required for schema/pivot/flattener tests):

```bash
cd tests/
python -m pytest test_schema_builder.py test_pivot_handler.py test_flattener.py -v
```

Integration tests (spark-xml JAR required):

```bash
spark-submit \
    --packages com.databricks:spark-xml_2.12:0.17.0 \
    -m pytest tests/ -v
```

---

## Repository Initialisation Script

To set up a **new** GitHub repository from scratch:

```bash
chmod +x scripts/init_repo.sh
./scripts/init_repo.sh https://github.com/your-org/pyspark-xml-etl.git main
```

The script:
1. Runs `git init` (skipped if `.git` already exists)
2. Writes the project `.gitignore` (excludes `*.xml`, `*.parquet`, `*.csv`, Spark artefacts)
3. Stages all tracked files and creates an initial commit
4. Adds the remote and pushes with exponential-backoff retry (up to 4 attempts)

> **Critical:** The `.gitignore` excludes `*.xml` and `*.parquet` globally to prevent accidentally committing 200 GB data files.  The small test fixture (`tests/fixtures/sample_records.xml`) and the schema JSON (`config/schema_config.json`) are explicitly **un-ignored** with negation rules.

---

## Performance Tuning Guide

| Scenario | Recommendation |
|---|---|
| 200 GB uncompressed XML, 50 executors | `initial_partitions: 400`, `output_partitions: 200` |
| Schema > 1 000 columns | Set `spark.sql.debug.maxToStringFields: 3000` |
| Skewed data (one tooth has 10×  more rows) | AQE skew join is enabled by default |
| Writing to S3 with many small files | Increase `output_partitions` and use `snappy` compression |
| OOM on driver during schema load | Keep schema in JSON file; never hard-code 3 000 `StructField` calls inline |

---

## Extending the Pipeline

**Add a new XML source:** copy `config/pipeline_config.yaml` to `config/my_source.yaml`, update `source.path`, `source.row_tag`, `schema.path`, and `pivot_specs`.  No Python changes needed.

**Add a transformation stage:** implement a function `stage_my_transform(df, cfg) → df` in `src/etl_pipeline.py` and insert it between existing stages.

**Export the schema from an existing DataFrame:**
```python
from src.schema_builder import schema_to_json
schema_to_json(df.schema, "config/schema_config.json")
```
