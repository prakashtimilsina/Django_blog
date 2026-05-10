#!/usr/bin/env bash
# =============================================================================
# init_repo.sh — Initialise a local Git repository and push to GitHub
#
# Usage:
#   chmod +x scripts/init_repo.sh
#   ./scripts/init_repo.sh <github-remote-url> [branch-name]
#
# Examples:
#   ./scripts/init_repo.sh https://github.com/your-org/pyspark-xml-etl.git
#   ./scripts/init_repo.sh git@github.com:your-org/pyspark-xml-etl.git main
#
# Prerequisites:
#   - git installed and configured (user.name / user.email)
#   - Network access to GitHub (SSH key or HTTPS token configured)
#
# IMPORTANT — read before running:
#   The .gitignore created by this script intentionally excludes *.xml, *.csv,
#   *.parquet, and large binary files so that 200 GB data files are NEVER
#   committed to Git.  Verify the .gitignore before your first commit.
# =============================================================================

set -euo pipefail

# ── Argument handling ─────────────────────────────────────────────────────────
REMOTE_URL="${1:-}"
BRANCH="${2:-main}"

if [[ -z "$REMOTE_URL" ]]; then
  echo "ERROR: GitHub remote URL is required."
  echo "Usage: $0 <github-remote-url> [branch-name]"
  exit 1
fi

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Work from the script's parent directory (project root) ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
info "Working directory: $PROJECT_ROOT"

# =============================================================================
# Step 1 — Initialise Git repository
# =============================================================================
if [[ -d .git ]]; then
  warn ".git already exists — skipping git init"
else
  info "Initialising Git repository …"
  git init
  git checkout -b "$BRANCH" 2>/dev/null || true
  success "Repository initialised on branch '$BRANCH'"
fi

# =============================================================================
# Step 2 — Write .gitignore
# =============================================================================
info "Writing .gitignore …"
cat > .gitignore << 'GITIGNORE'
# =============================================================================
# PySpark / Python — .gitignore
# =============================================================================

# ── Large data files (NEVER commit these) ────────────────────────────────────
*.xml
*.csv
*.tsv
*.json.gz
*.parquet
*.avro
*.orc

# Allow the schema config JSON and fixture XML (small, version-controlled)
!config/schema_config.json
!tests/fixtures/*.xml

# ── Spark runtime artefacts ───────────────────────────────────────────────────
derby.log
metastore_db/
checkpoint/
spark-warehouse/
.spark-history/

# ── Python artefacts ─────────────────────────────────────────────────────────
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg
*.egg-info/
dist/
build/
.eggs/
.Python

# ── Virtual environments ──────────────────────────────────────────────────────
venv/
.venv/
env/
.env/
ENV/

# ── Jupyter ───────────────────────────────────────────────────────────────────
.ipynb_checkpoints/
*.ipynb

# ── IDE / editor ──────────────────────────────────────────────────────────────
.idea/
.vscode/
*.swp
*.swo
.DS_Store
Thumbs.db

# ── Logs ──────────────────────────────────────────────────────────────────────
*.log
logs/

# ── Test / coverage artefacts ─────────────────────────────────────────────────
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
GITIGNORE

success ".gitignore written"

# =============================================================================
# Step 3 — Sanity-check: warn if any large files would be staged
# =============================================================================
info "Checking for files that should not be committed …"
LARGE_FILES=$(find . -not -path './.git/*' -size +50M 2>/dev/null || true)
if [[ -n "$LARGE_FILES" ]]; then
  warn "Large files detected (>50 MB) — confirm they are excluded by .gitignore:"
  echo "$LARGE_FILES"
fi

# =============================================================================
# Step 4 — Initial commit
# =============================================================================
info "Staging tracked files …"
git add .

# Show what will be committed so the user can verify before pushing
echo ""
echo "───────────────────────────────────────────────────────────"
echo "Files staged for commit:"
git status --short
echo "───────────────────────────────────────────────────────────"
echo ""

info "Creating initial commit …"
git commit -m "feat: add PySpark XML ETL pipeline

- Configuration-driven pipeline (YAML + JSON schema)
- Handles 200 GB+ deeply nested XML via com.databricks.spark.xml
- Fixed StructType schema loaded at runtime (no inference)
- Pivot repeating XML elements into exactly N flat columns
- Recursive struct flattener for deeply nested schemas
- Adaptive Query Execution and Kryo serialisation enabled
- Full unit-test suite (schema, pivot, flattener, xml reader)
- .gitignore excludes all data/log/artefact files"

success "Initial commit created"

# =============================================================================
# Step 5 — Add remote and push
# =============================================================================
if git remote get-url origin &>/dev/null; then
  warn "Remote 'origin' already exists — updating URL"
  git remote set-url origin "$REMOTE_URL"
else
  info "Adding remote 'origin' → $REMOTE_URL"
  git remote add origin "$REMOTE_URL"
fi

info "Pushing to origin/$BRANCH …"

# Retry with exponential back-off (network blips are common in CI)
MAX_RETRIES=4
WAIT=2
for attempt in $(seq 1 $MAX_RETRIES); do
  if git push -u origin "$BRANCH"; then
    success "Push succeeded on attempt $attempt"
    break
  fi

  if [[ $attempt -eq $MAX_RETRIES ]]; then
    error "Push failed after $MAX_RETRIES attempts. Check your credentials and network."
  fi

  warn "Push failed (attempt $attempt/$MAX_RETRIES). Retrying in ${WAIT}s …"
  sleep "$WAIT"
  WAIT=$(( WAIT * 2 ))
done

# =============================================================================
# Done
# =============================================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Repository initialised and pushed to $REMOTE_URL (branch: $BRANCH)"
echo ""
echo "Next steps:"
echo "  1. Update config/pipeline_config.yaml with your source/sink paths"
echo "  2. Update config/schema_config.json to match your XML structure"
echo "  3. Run the pipeline:"
echo "       spark-submit \\"
echo "         --packages com.databricks:spark-xml_2.12:0.17.0 \\"
echo "         src/etl_pipeline.py \\"
echo "         --config config/pipeline_config.yaml"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
