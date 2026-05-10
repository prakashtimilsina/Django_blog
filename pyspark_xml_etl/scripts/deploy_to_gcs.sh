#!/usr/bin/env bash
# =============================================================================
# deploy_to_gcs.sh — Upload pipeline artifacts to GCS
#
# Usage:
#   ./scripts/deploy_to_gcs.sh <env> <artifacts-bucket>
#
# Examples:
#   ./scripts/deploy_to_gcs.sh dev  my-artifacts-bucket-dev
#   ./scripts/deploy_to_gcs.sh qa   my-artifacts-bucket-qa
#   ./scripts/deploy_to_gcs.sh prod my-artifacts-bucket-prod
#
# What this deploys to gs://<bucket>/pyspark_xml_etl/
# ────────────────────────────────────────────────────
#   scripts/etl_pipeline.py           main PySpark entry point
#   scripts/src.zip                   all src/ modules bundled for --py-files
#   config/pipeline_config_<env>.yaml environment config loaded by Spark job
#   config/schema_config.json         StructType schema definition
#   jars/spark-xml_2.12-0.17.0.jar    spark-xml library
#   init/install_jars.sh              Dataproc init action to install the JAR
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login or service account)
#   - gsutil available
#   - zip installed
#   - Maven or wget to download spark-xml JAR (first run only)
# =============================================================================

set -euo pipefail

ENV="${1:-}"
ARTIFACTS_BUCKET="${2:-}"

if [[ -z "$ENV" || -z "$ARTIFACTS_BUCKET" ]]; then
    echo "Usage: $0 <env> <artifacts-bucket>"
    echo "  env              : dev | qa | prod"
    echo "  artifacts-bucket : GCS bucket name (without gs://)"
    exit 1
fi

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
GCS_BASE="gs://${ARTIFACTS_BUCKET}/pyspark_xml_etl"
SPARK_XML_JAR="spark-xml_2.12-0.17.0.jar"
SPARK_XML_URL="https://repo1.maven.org/maven2/com/databricks/spark-xml_2.12/0.17.0/${SPARK_XML_JAR}"

info "Deploying pyspark_xml_etl artifacts to ${GCS_BASE} (env=${ENV})"

# =============================================================================
# Step 1 — Bundle src/ modules into src.zip
# =============================================================================
info "Creating src.zip …"
cd "$PROJECT_ROOT/src"
zip -qr "$PROJECT_ROOT/src.zip" ./*.py
cd "$PROJECT_ROOT"
success "src.zip created ($(du -sh src.zip | cut -f1))"

# =============================================================================
# Step 2 — Download spark-xml JAR (skip if already cached locally)
# =============================================================================
JAR_LOCAL="$PROJECT_ROOT/.cache/${SPARK_XML_JAR}"
mkdir -p "$PROJECT_ROOT/.cache"

if [[ ! -f "$JAR_LOCAL" ]]; then
    info "Downloading spark-xml JAR …"
    if command -v wget &>/dev/null; then
        wget -q -O "$JAR_LOCAL" "$SPARK_XML_URL"
    elif command -v curl &>/dev/null; then
        curl -sSL -o "$JAR_LOCAL" "$SPARK_XML_URL"
    else
        error "Neither wget nor curl found. Download ${SPARK_XML_URL} manually to .cache/${SPARK_XML_JAR}"
    fi
    success "JAR downloaded to .cache/"
else
    info "Using cached JAR at .cache/${SPARK_XML_JAR}"
fi

# =============================================================================
# Step 3 — Create the Dataproc init action script
# =============================================================================
INIT_SCRIPT="$PROJECT_ROOT/.cache/install_jars.sh"
cat > "$INIT_SCRIPT" << INIT
#!/bin/bash
# Dataproc init action: install spark-xml JAR on all cluster nodes
set -e
JARS_DIR="/usr/lib/spark/jars"
JAR_GCS="${GCS_BASE}/jars/${SPARK_XML_JAR}"
echo "Installing \${JAR_GCS} → \${JARS_DIR}/"
gsutil cp "\${JAR_GCS}" "\${JARS_DIR}/${SPARK_XML_JAR}"
echo "spark-xml JAR installed successfully"
INIT
chmod +x "$INIT_SCRIPT"
success "init action script created"

# =============================================================================
# Step 4 — Upload artifacts to GCS
# =============================================================================
info "Uploading scripts …"
gsutil -q cp "$PROJECT_ROOT/src/etl_pipeline.py" "${GCS_BASE}/scripts/etl_pipeline.py"
gsutil -q cp "$PROJECT_ROOT/src.zip"              "${GCS_BASE}/scripts/src.zip"

info "Uploading configs …"
gsutil -q cp "$PROJECT_ROOT/config/env/${ENV}.yaml"   "${GCS_BASE}/config/pipeline_config_${ENV}.yaml"
gsutil -q cp "$PROJECT_ROOT/config/schema_config.json" "${GCS_BASE}/config/schema_config.json"

info "Uploading JAR …"
gsutil -q cp "$JAR_LOCAL"   "${GCS_BASE}/jars/${SPARK_XML_JAR}"

info "Uploading init action …"
gsutil -q cp "$INIT_SCRIPT" "${GCS_BASE}/init/install_jars.sh"

# =============================================================================
# Step 5 — Clean up local temp files
# =============================================================================
rm -f "$PROJECT_ROOT/src.zip"
success "Temp files cleaned up"

# =============================================================================
# Step 6 — Verify uploads
# =============================================================================
info "Verifying GCS contents …"
gsutil ls "${GCS_BASE}/**" | sed "s|gs://|  gs://|"

# =============================================================================
# Done
# =============================================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Deployment complete for env=${ENV}"
echo ""
echo "Set these Airflow Variables in Cloud Composer:"
echo "  xml_etl_artifacts_bucket = ${ARTIFACTS_BUCKET}"
echo "  xml_etl_env              = ${ENV}"
echo ""
echo "Trigger the DAG with:"
echo '  {"env": "'"${ENV}"'", "cluster_mode": "persistent"}'
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
