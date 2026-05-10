"""
Cloud Composer Airflow DAG — PySpark XML ETL Pipeline
======================================================

Orchestrates the full ETL pipeline on GCP:
  1. Validate source GCS paths
  2. [Ephemeral mode] Create Dataproc cluster
  3. Submit PySpark job to Dataproc (persistent or ephemeral cluster)
  4. [Ephemeral mode] Delete Dataproc cluster (runs even on job failure)

Cluster modes
-------------
persistent : job is submitted to a long-running cluster (faster startup)
ephemeral  : cluster is created fresh, used, then torn down (scales to zero)

Select mode at runtime via DAG Run config JSON:
    {"cluster_mode": "ephemeral", "env": "prod"}

Or set Airflow Variables (used as fallback):
    xml_etl_cluster_mode  →  "persistent" | "ephemeral"
    xml_etl_env           →  "dev" | "qa" | "prod"

Airflow Variables required (per environment, set via Composer UI or gcloud)
---------------------------------------------------------------------------
xml_etl_gcp_project          GCP project ID
xml_etl_dataproc_region      Dataproc region, e.g. us-central1
xml_etl_cluster_name         Persistent cluster name (ignored in ephemeral mode)
xml_etl_artifacts_bucket     GCS bucket holding deployed pipeline artifacts
xml_etl_env                  Active environment: dev | qa | prod
xml_etl_cluster_mode         Default cluster mode: persistent | ephemeral

Artifacts layout expected in GCS (produced by scripts/deploy_to_gcs.sh)
------------------------------------------------------------------------
gs://<artifacts_bucket>/pyspark_xml_etl/
    scripts/
        etl_pipeline.py
        src.zip                  # zip of src/ directory
    config/
        pipeline_config_dev.yaml
        pipeline_config_qa.yaml
        pipeline_config_prod.yaml
        schema_config.json
    jars/
        spark-xml_2.12-0.17.0.jar
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.providers.google.cloud.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocDeleteClusterOperator,
    DataprocSubmitJobOperator,
)
from airflow.utils.trigger_rule import TriggerRule

# ── DAG-level defaults ────────────────────────────────────────────────────────

_DEFAULT_ARGS = {
    "owner": "data-engineering",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": True,
    "email_on_retry": False,
}

# ── Runtime config helpers ────────────────────────────────────────────────────

def _conf(context: dict, key: str, airflow_var: str, default: str = "") -> str:
    """Read a value from DAG run conf first, then Airflow Variable, then default."""
    run_conf = context["dag_run"].conf or {}
    return run_conf.get(key, Variable.get(airflow_var, default_var=default))


def _get_env(context: dict) -> str:
    return _conf(context, "env", "xml_etl_env", "dev")


def _get_cluster_mode(context: dict) -> str:
    return _conf(context, "cluster_mode", "xml_etl_cluster_mode", "persistent")


def _get_project(context: dict) -> str:
    return Variable.get("xml_etl_gcp_project")


def _get_region(context: dict) -> str:
    return Variable.get("xml_etl_dataproc_region")


def _get_artifacts_bucket(context: dict) -> str:
    return Variable.get("xml_etl_artifacts_bucket")


def _get_cluster_name(context: dict) -> str:
    """
    Return the cluster name.
    For ephemeral mode a unique name is generated per DAG run so concurrent
    runs don't collide.  For persistent mode the pre-configured name is used.
    """
    mode = _get_cluster_mode(context)
    env = _get_env(context)
    if mode == "ephemeral":
        run_id = context["dag_run"].run_id.replace("_", "-").replace(":", "-")[:30]
        return f"xml-etl-{env}-{run_id}"
    return Variable.get("xml_etl_cluster_name", default_var=f"xml-etl-{env}")


# ── GCS path builders ─────────────────────────────────────────────────────────

def _artifacts_base(context: dict) -> str:
    return f"gs://{_get_artifacts_bucket(context)}/pyspark_xml_etl"


def _pipeline_config_uri(context: dict) -> str:
    env = _get_env(context)
    return f"{_artifacts_base(context)}/config/pipeline_config_{env}.yaml"


def _main_script_uri(context: dict) -> str:
    return f"{_artifacts_base(context)}/scripts/etl_pipeline.py"


def _src_zip_uri(context: dict) -> str:
    return f"{_artifacts_base(context)}/scripts/src.zip"


def _spark_xml_jar_uri(context: dict) -> str:
    return f"{_artifacts_base(context)}/jars/spark-xml_2.12-0.17.0.jar"


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_gcs_paths(**context) -> None:
    """Check that required GCS artifacts exist before submitting to Dataproc."""
    from google.cloud import storage

    client = storage.Client()
    bucket_name = _get_artifacts_bucket(context)
    bucket = client.bucket(bucket_name)

    required_blobs = [
        f"pyspark_xml_etl/scripts/etl_pipeline.py",
        f"pyspark_xml_etl/scripts/src.zip",
        f"pyspark_xml_etl/jars/spark-xml_2.12-0.17.0.jar",
        f"pyspark_xml_etl/config/pipeline_config_{_get_env(context)}.yaml",
        f"pyspark_xml_etl/config/schema_config.json",
    ]

    missing = [b for b in required_blobs if not bucket.blob(b).exists()]
    if missing:
        raise FileNotFoundError(
            f"Required GCS artifacts missing in gs://{bucket_name}:\n"
            + "\n".join(f"  {b}" for b in missing)
            + "\n\nRun scripts/deploy_to_gcs.sh to upload artifacts."
        )


# ── Cluster mode branching ────────────────────────────────────────────────────

def _branch_start(**context) -> str:
    """Route to cluster creation (ephemeral) or directly to job (persistent)."""
    return (
        "create_dataproc_cluster"
        if _get_cluster_mode(context) == "ephemeral"
        else "join_before_job"
    )


def _branch_after_job(**context) -> str:
    """Route to cluster deletion (ephemeral) or straight to pipeline_complete."""
    return (
        "delete_dataproc_cluster"
        if _get_cluster_mode(context) == "ephemeral"
        else "pipeline_complete"
    )


# ── Ephemeral cluster spec (loaded from YAML at submit time) ──────────────────

def _ephemeral_cluster_config(context: dict) -> dict:
    """
    Build the Dataproc cluster config dict for ephemeral clusters.
    Machine types and worker counts come from the environment config.
    See config/dataproc_cluster.yaml for reference values.
    """
    env = _get_env(context)

    # Worker counts per environment
    worker_counts = {"dev": 2, "qa": 4, "prod": 10}
    secondary_counts = {"dev": 0, "qa": 2, "prod": 5}
    master_type = {"dev": "n1-standard-4", "qa": "n1-standard-8", "prod": "n1-standard-16"}
    worker_type = {"dev": "n1-standard-4", "qa": "n1-standard-8", "prod": "n1-standard-16"}

    artifacts_bucket = _get_artifacts_bucket(context)

    return {
        "master_config": {
            "num_instances": 1,
            "machine_type_uri": master_type.get(env, "n1-standard-8"),
            "disk_config": {"boot_disk_type": "pd-ssd", "boot_disk_size_gb": 100},
        },
        "worker_config": {
            "num_instances": worker_counts.get(env, 2),
            "machine_type_uri": worker_type.get(env, "n1-standard-8"),
            "disk_config": {"boot_disk_type": "pd-ssd", "boot_disk_size_gb": 200},
        },
        "secondary_worker_config": {
            "num_instances": secondary_counts.get(env, 0),
            "is_preemptible": True,  # Preemptible VMs for cost savings
        },
        "software_config": {
            "image_version": "2.1-debian11",  # Spark 3.3, Python 3.10
            "properties": {
                "spark:spark.sql.shuffle.partitions": "800",
                "spark:spark.sql.adaptive.enabled": "true",
                "spark:spark.serializer": "org.apache.spark.serializer.KryoSerializer",
                "spark:spark.sql.debug.maxToStringFields": "3000",
            },
        },
        "gce_cluster_config": {
            "service_account_scopes": [
                "https://www.googleapis.com/auth/cloud-platform"
            ],
        },
        "initialization_actions": [
            {
                # Install spark-xml JAR on all nodes
                "executable_file": (
                    f"gs://{artifacts_bucket}/pyspark_xml_etl/init/install_jars.sh"
                ),
                "execution_timeout": {"seconds": 300},
            }
        ],
    }


# ── Dataproc PySpark job spec ─────────────────────────────────────────────────

def _build_pyspark_job(context: dict) -> dict:
    """Construct the Dataproc PySpark job submission dict."""
    project = _get_project(context)
    cluster_name = _get_cluster_name(context)

    return {
        "reference": {"project_id": project},
        "placement": {"cluster_name": cluster_name},
        "pyspark_job": {
            "main_python_file_uri": _main_script_uri(context),
            "python_file_uris": [_src_zip_uri(context)],
            "jar_file_uris": [_spark_xml_jar_uri(context)],
            "args": [
                "--config", _pipeline_config_uri(context),
            ],
            # Spark properties can be overridden per-run via DAG conf
            "properties": {
                "spark.sql.shuffle.partitions": str(
                    (context["dag_run"].conf or {}).get("shuffle_partitions", "800")
                ),
            },
            "logging_config": {
                # Dataproc driver logs → Cloud Logging
                "driver_log_levels": {"root": "INFO"}
            },
        },
    }


# ── Callable wrappers for operators that need runtime context ─────────────────

def _create_cluster_callable(**context) -> None:
    """Used by PythonOperator to push cluster config into XCom."""
    context["ti"].xcom_push("cluster_name", _get_cluster_name(context))
    context["ti"].xcom_push(
        "cluster_config", _ephemeral_cluster_config(context)
    )


# ── DAG definition ────────────────────────────────────────────────────────────

with DAG(
    dag_id="xml_etl_pipeline",
    description="PySpark XML ETL — GCS source → Dataproc → GCS sink",
    default_args=_DEFAULT_ARGS,
    schedule_interval="0 2 * * *",       # daily at 02:00 UTC
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,                   # prevent overlapping runs
    tags=["etl", "pyspark", "xml", "dataproc"],
    params={
        "env": "dev",
        "cluster_mode": "persistent",
        "shuffle_partitions": "800",
    },
) as dag:

    # ── 1. Validate GCS artifacts ────────────────────────────────────────────
    validate_gcs_paths = PythonOperator(
        task_id="validate_gcs_paths",
        python_callable=_validate_gcs_paths,
    )

    # ── 2. Branch: ephemeral → create cluster | persistent → skip ────────────
    branch_start = BranchPythonOperator(
        task_id="branch_cluster_mode",
        python_callable=_branch_start,
    )

    create_dataproc_cluster = DataprocCreateClusterOperator(
        task_id="create_dataproc_cluster",
        project_id="{{ var.value.xml_etl_gcp_project }}",
        region="{{ var.value.xml_etl_dataproc_region }}",
        cluster_name="{{ task_instance.xcom_pull('create_cluster_config', key='cluster_name') }}",
        cluster_config="{{ task_instance.xcom_pull('create_cluster_config', key='cluster_config') }}",
    )

    # Push cluster name + config to XCom before cluster creation
    prepare_cluster_config = PythonOperator(
        task_id="create_cluster_config",
        python_callable=_create_cluster_callable,
    )

    # Persistent mode skips cluster creation — joins here
    join_before_job = EmptyOperator(
        task_id="join_before_job",
        trigger_rule=TriggerRule.ONE_SUCCESS,
    )

    # ── 3. Submit PySpark job to Dataproc ────────────────────────────────────
    submit_pyspark_etl_job = DataprocSubmitJobOperator(
        task_id="submit_pyspark_etl_job",
        project_id="{{ var.value.xml_etl_gcp_project }}",
        region="{{ var.value.xml_etl_dataproc_region }}",
        job=_build_pyspark_job,         # callable — evaluated at runtime
        trigger_rule=TriggerRule.ONE_SUCCESS,
        asynchronous=False,             # wait for job completion
        timeout=timedelta(hours=4).seconds,
    )

    # ── 4. Branch: ephemeral → delete cluster | persistent → finish ──────────
    branch_after_job = BranchPythonOperator(
        task_id="branch_after_job",
        python_callable=_branch_after_job,
        trigger_rule=TriggerRule.ALL_DONE,  # run even if job failed
    )

    delete_dataproc_cluster = DataprocDeleteClusterOperator(
        task_id="delete_dataproc_cluster",
        project_id="{{ var.value.xml_etl_gcp_project }}",
        region="{{ var.value.xml_etl_dataproc_region }}",
        cluster_name="{{ task_instance.xcom_pull('create_cluster_config', key='cluster_name') }}",
        trigger_rule=TriggerRule.ALL_DONE,  # delete even if job failed
    )

    pipeline_complete = EmptyOperator(
        task_id="pipeline_complete",
        trigger_rule=TriggerRule.ONE_SUCCESS,
    )

    # ── Task dependencies ─────────────────────────────────────────────────────
    #
    # Ephemeral flow:
    #   validate → branch_start → prepare_cluster_config
    #                              → create_dataproc_cluster
    #                                → join_before_job → submit_job
    #                                                    → branch_after_job
    #                                                      → delete_cluster
    #                                                        → pipeline_complete
    #
    # Persistent flow:
    #   validate → branch_start → join_before_job → submit_job
    #                                                → branch_after_job
    #                                                  → pipeline_complete

    validate_gcs_paths >> branch_start

    # Ephemeral path
    branch_start >> prepare_cluster_config >> create_dataproc_cluster >> join_before_job

    # Persistent path (branch_start routes directly to join)
    branch_start >> join_before_job

    join_before_job >> submit_pyspark_etl_job >> branch_after_job

    # Post-job: ephemeral deletes cluster, persistent goes straight to complete
    branch_after_job >> delete_dataproc_cluster >> pipeline_complete
    branch_after_job >> pipeline_complete
