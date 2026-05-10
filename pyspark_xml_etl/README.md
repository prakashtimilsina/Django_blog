# PySpark XML ETL Pipeline

A **configuration-driven** PySpark pipeline for processing **200 GB+ deeply nested XML** files into flat, analysis-ready Parquet datasets on GCP.  Schema definitions, source/sink paths, pivot rules, and performance parameters are declared in YAML/JSON — no Python edits required to onboard a new XML source.

---

## Features

| Capability | Details |
|---|---|
| **Schema-first** | Fixed `StructType` loaded from JSON at runtime — no costly schema inference on massive files |
| **Namespace-aware** | `ignoreNamespace=true` strips all prefixes; works with any namespace-heavy XML |
| **Multi-document XML** | Handles files with multiple `<?xml?>` declarations and no root wrapper (native spark-xml support) |
| **Pivot repeating elements** | Extracts exactly *N* occurrences of any array column into `prefix_1 … prefix_N` flat columns |
| **Recursive struct flattener** | Promotes every nested struct leaf to the top level (`patient__address__city`) |
| **GCP-native** | Source/sink on `gs://`, schema/config loaded from GCS, runs on Cloud Dataproc |
| **Airflow DAG** | Cloud Composer DAG with ephemeral or persistent Dataproc clusters; ephemeral clusters are always cleaned up even on failure |
| **Data-quality gate** | Corrupt-record counting with configurable per-environment abort threshold |

---

## Project Structure

```
pyspark_xml_etl/
├── src/
│   ├── etl_pipeline.py        # Main entry point (spark-submit target)
│   ├── schema_builder.py      # JSON → StructType loader (local + gs://)
│   ├── xml_processor.py       # spark-xml reader wrapper + normalize_multi_doc_xml
│   ├── pivot_handler.py       # Array → flat numbered columns
│   ├── flattener.py           # Recursive struct flattener
│   └── utils.py               # SparkSession factory, GCS-tuned writers, partitioners
├── config/
│   ├── pipeline_config.yaml   # Local / CI base config
│   ├── schema_config.json     # StructType schema (simple source)
│   ├── schema_deep_ns.json    # StructType schema (8-level deep, 5-namespace source)
│   ├── dataproc_cluster.yaml  # Reference cluster specs per environment
│   └── env/
│       ├── dev.yaml           # GCS paths + small cluster (n1-standard-4 × 2)
│       ├── qa.yaml            # GCS paths + mid cluster (n1-standard-8 × 4)
│       └── prod.yaml          # GCS paths + large cluster (n1-standard-16 × 10)
├── dags/
│   └── xml_etl_dag.py         # Cloud Composer Airflow DAG
├── scripts/
│   ├── deploy_to_gcs.sh       # Bundle + upload all artifacts to GCS
│   ├── generate_schema.py     # Infer schema from sample XML or pretty-print existing schema
│   └── generate_test_data.py  # Synthesize deeply nested XML test fixtures
├── tests/
│   ├── fixtures/
│   │   ├── sample_records.xml        # Plain XML, 2 records
│   │   ├── sample_records_ns.xml     # Namespaced XML, 2 records
│   │   ├── sample_deep_ns.xml        # 8-level deep, 5 namespaces, 2 records
│   │   └── sample_multi_doc.xml      # 4 records, each with own <?xml?> declaration
│   ├── test_schema_builder.py
│   ├── test_pivot_handler.py
│   ├── test_flattener.py
│   └── test_xml_processor.py
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
| GCP SDK | For `gs://` paths and GCP deployment |

---

## Running Locally (Test Mode)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Run unit tests (no JAR or Spark required)

These cover schema loading, struct flattening, array pivoting, and the multi-doc XML normalizer:

```bash
cd pyspark_xml_etl/
python -m pytest tests/test_schema_builder.py tests/test_pivot_handler.py \
    tests/test_flattener.py -v
# The normalizer tests in test_xml_processor.py also run without the JAR:
python -m pytest tests/test_xml_processor.py::TestNormalizeMultiDocXml -v
```

### 3. Run integration tests (spark-xml JAR required)

```bash
spark-submit \
    --packages com.databricks:spark-xml_2.12:0.17.0 \
    --py-files src/ \
    -m pytest tests/ -v
```

Tests against `sample_multi_doc.xml` (multiple `<?xml?>` per file, no root wrapper) are skipped automatically if the JAR is not on the classpath.

### 4. Run the pipeline locally

```bash
spark-submit \
    --packages com.databricks:spark-xml_2.12:0.17.0 \
    src/etl_pipeline.py \
    --config config/pipeline_config.yaml
```

Output lands in `/tmp/xml_etl_local_output` by default (set in `pipeline_config.yaml`).

### 5. Generate synthetic test data

```bash
# 100 records, 8-level deep, 5 namespaces
python scripts/generate_test_data.py \
    --records 100 \
    --max-teeth 10 \
    --max-panels 3 \
    --seed 42 \
    --output /tmp/test_large.xml

# Write directly to GCS
python scripts/generate_test_data.py \
    --records 1000 \
    --output gs://my-bucket/samples/test_1000.xml
```

### 6. Infer schema from a sample XML

Run ONCE on a small representative sample; use the saved JSON in production:

```bash
spark-submit \
    --packages com.databricks:spark-xml_2.12:0.17.0 \
    scripts/generate_schema.py infer \
    --input  tests/fixtures/sample_deep_ns.xml \
    --output config/schema_inferred.json \
    --row-tag record
```

Pretty-print an existing schema (no Spark needed):

```bash
python scripts/generate_schema.py show
```

---

## GCP Deployment

### 1. Deploy artifacts to GCS

```bash
export GCS_BUCKET=my-pipeline-bucket
bash scripts/deploy_to_gcs.sh "$GCS_BUCKET"
```

This bundles `src/` into `src.zip`, downloads the spark-xml JAR, creates a Dataproc init action script, and uploads everything to:

```
gs://$GCS_BUCKET/pyspark_xml_etl/
├── src.zip
├── jars/spark-xml_2.12-0.17.0.jar
├── scripts/etl_pipeline.py
├── scripts/init_dataproc.sh
└── config/
    ├── pipeline_config_dev.yaml
    ├── pipeline_config_qa.yaml
    ├── pipeline_config_prod.yaml
    └── schema_*.json
```

### 2. Environment configs

Each environment has its own YAML under `config/env/`:

| File | Cluster | Initial partitions | Corrupt threshold |
|---|---|---|---|
| `dev.yaml` | n1-standard-4 × 2 workers | 50 | 500 |
| `qa.yaml` | n1-standard-8 × 4 workers | 200 | 100 |
| `prod.yaml` | n1-standard-16 × 10 workers + 5 preemptible | 800 | 0 (zero tolerance) |

Prod paths use `{{ ds_nodash }}` for date-partitioned GCS output.

### 3. Run on Dataproc directly (without Airflow)

```bash
gcloud dataproc jobs submit pyspark \
    gs://$GCS_BUCKET/pyspark_xml_etl/scripts/etl_pipeline.py \
    --cluster=xml-etl-dev \
    --region=us-central1 \
    --jars=gs://$GCS_BUCKET/pyspark_xml_etl/jars/spark-xml_2.12-0.17.0.jar \
    --py-files=gs://$GCS_BUCKET/pyspark_xml_etl/src.zip \
    -- --config gs://$GCS_BUCKET/pyspark_xml_etl/config/pipeline_config_dev.yaml
```

---

## Running via Cloud Composer (Airflow DAG)

The DAG lives in `dags/xml_etl_dag.py` and is deployed to Cloud Composer automatically when the file is placed in the environment's DAGs bucket.

### DAG overview

```
validate_gcs_paths
    └── branch_cluster_mode
            ├── [ephemeral] create_dataproc_cluster ──┐
            └── [persistent] skip directly            │
                                                       ▼
                                               join_before_job
                                                       │
                                               submit_pyspark_etl_job
                                                       │
                                               branch_after_job
                                                       ├── [ephemeral] delete_dataproc_cluster
                                                       └── [persistent] pipeline_complete
```

`delete_dataproc_cluster` uses `TriggerRule.ALL_DONE` — the ephemeral cluster is deleted even if the Spark job fails.

### Trigger the DAG

**Via Airflow UI:** set DAG run config:

```json
{
  "cluster_mode": "ephemeral",
  "env": "prod"
}
```

**Via gcloud CLI:**

```bash
gcloud composer environments run my-composer-env \
    --location us-central1 \
    dags trigger -- xml_etl_pipeline \
    --conf '{"cluster_mode":"ephemeral","env":"prod"}'
```

### Airflow Variables used by the DAG

| Variable | Default | Description |
|---|---|---|
| `xml_etl_gcs_bucket` | *(required)* | GCS bucket that holds all pipeline artifacts |
| `xml_etl_cluster_mode` | `ephemeral` | `ephemeral` or `persistent` |
| `xml_etl_cluster_name` | `xml-etl-<env>` | Persistent cluster name (ignored for ephemeral) |
| `xml_etl_env` | `dev` | Environment: `dev`, `qa`, or `prod` |
| `xml_etl_dataproc_region` | `us-central1` | Dataproc region |

DAG run `conf` values override Airflow Variables, so ad-hoc runs can target a specific env without changing variables.

---

## XML Feature Reference

### Namespaced XML

XML with namespace prefixes (`ehr:record_id`, `dental:tooth`, `xsi:nil`) is handled transparently:

```yaml
source:
  row_tag: "record"           # LOCAL name only — never "ehr:record"
  reader_options:
    ignoreNamespace: "true"   # already in safe defaults; shown for clarity
```

`xsi:nil="true"` on any element produces a `null` column value (not a corrupt record).

Use `read_xml_namespaced()` from `xml_processor.py` to also suppress `xmlns:*` attribute noise columns.

### Multi-document XML (no root wrapper)

A file containing multiple `<?xml?>` declarations and no shared root element is handled natively by spark-xml's byte-level row scanner:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<record>...</record>

<?xml version="1.0" encoding="UTF-8"?>
<record>...</record>
```

Enable the config flag to activate a guard that rejects accidental `wholeFile=true` and logs a confirmation:

```yaml
source:
  row_tag: "record"
  multi_document: true        # guards against wholeFile=true; no pre-processing needed
```

**Never** set `wholeFile: true` for multi-document files — it loads the entire file as one XML document and reads only the first record.

If you need to pass such a file to a strict single-document XML tool outside Spark, use the normalizer:

```python
from xml_processor import normalize_multi_doc_xml

clean_path = normalize_multi_doc_xml("input_multi.xml")   # strips duplicate <?xml?> declarations
# ... use clean_path with your tool ...
import os; os.unlink(clean_path)                           # caller cleans up the temp file
```

---

## Schema Reference

Both formats are supported in any `*.json` schema file:

**Native Spark format** (output of `df.schema.jsonValue()`):

```json
{
  "type": "struct",
  "fields": [
    { "name": "record_id", "type": "string",  "nullable": false },
    { "name": "year",      "type": "integer", "nullable": true  }
  ]
}
```

**Custom field-list format** (hand-authored shorthand):

```json
{
  "fields": [
    { "name": "record_id", "type": "string",  "nullable": false },
    { "name": "year",      "type": "integer", "nullable": true  }
  ]
}
```

Nested structs, arrays of structs, and arrays of primitives are all supported.  See `config/schema_deep_ns.json` for a full 8-level-deep example with 5 namespaces.

---

## Performance Tuning

| Scenario | Recommendation |
|---|---|
| 200 GB uncompressed XML, 50 executors | `initial_partitions: 400`, `output_partitions: 200` |
| Schema > 1 000 columns | `spark.sql.debug.maxToStringFields: "3000"` (already in base config) |
| Skewed data | AQE skew join is enabled by default |
| GCS sink with many small files | Increase `output_partitions`; use `snappy` compression |
| OOM on driver during schema load | Keep schema in JSON file; never inline 3 000 `StructField()` calls |
| Running on Dataproc | GCS connector tuning (`fs.gs.block.size`, `fs.gs.io.buffersize`, etc.) is applied automatically when `DATAPROC_IMAGE_VERSION` env var is detected |

---

## Extending the Pipeline

**New XML source:** copy `config/pipeline_config.yaml` → `config/my_source.yaml`, update `source.path`, `source.row_tag`, `schema.path`, and `pivot_specs`.  No Python changes needed.

**New transformation stage:** implement `stage_my_transform(df, cfg) → df` in `src/etl_pipeline.py` and insert it between existing stages in `run()`.

**Export schema from an existing DataFrame:**

```python
from src.schema_builder import schema_to_json
schema_to_json(df.schema, "config/schema_config.json")
```
