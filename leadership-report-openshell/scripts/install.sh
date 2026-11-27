#!/usr/bin/env bash
set -euo pipefail

# End-to-end OpenShell + Leadership Report Agent installation on OpenShift.
# Run from the repo root: ./scripts/install.sh
#
# Prerequisites:
#   - oc CLI authenticated to your OpenShift cluster
#   - helm 3.x installed
#   - Secrets created before running (see below)
#
# Required secrets (create before running):
#   oc create secret generic leadership-report-config -n openshell \
#     --from-literal=doc-id=YOUR_GOOGLE_DOC_ID \
#     --from-literal=model-id=rits/zai-org/glm-5-3 \
#     --from-literal=meeting-name="Your Meeting Name" \
#     --from-literal=gcp-project=your-gcp-project
#
# After installation, run scripts/configure-providers.sh from a workstation
# with gcloud ADC for the Google provider.
#
# Environment variables (optional overrides):
#   OPENSHELL_CHART_VERSION  Helm chart version (default: 0.1.8)
#   AGENT_IMAGE              Agent container image ref

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFESTS="${REPO_DIR}/manifests"

CHART_VERSION="${OPENSHELL_CHART_VERSION:-0.1.8}"
AGENT_SANDBOX_VERSION="${AGENT_SANDBOX_VERSION:-v1.0.11}"
NAMESPACE="openshell"
AGENT_IMAGE="${AGENT_IMAGE:-quay.io/rh-ee-evhoward/leadership-report-agent:latest}"
TMP_DIR="$(mktemp -d)"

trap 'rm -rf "${TMP_DIR}"' EXIT

case "${AGENT_IMAGE}" in
  ''|*[!A-Za-z0-9._:/@-]*)
    echo "Invalid AGENT_IMAGE: ${AGENT_IMAGE}" >&2
    exit 1
    ;;
esac

echo "=== OpenShell on OpenShift Installer ==="
echo "Chart version: ${CHART_VERSION}"
echo ""

# Step 1: Agent Sandbox CRDs
echo "[1/7] Installing Agent Sandbox CRDs..."
oc apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/sandbox.yaml"
echo ""

# Step 2: Namespace + SCC + RBAC
echo "[2/7] Creating namespace and RBAC..."
oc apply -f "${MANIFESTS}/01-namespace.yaml"
oc apply -f "${MANIFESTS}/05-rbac.yaml"
echo ""

# Step 3: Helm install
echo "[3/7] Installing OpenShell via Helm..."
helm upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version "${CHART_VERSION}" \
  -n "${NAMESPACE}" \
  -f "${MANIFESTS}/03-helm-values.yaml"
echo ""

# Step 4: Wait for readiness + NetworkPolicy
echo "[4/7] Waiting for gateway to be ready..."
oc wait --for=condition=Ready pod/openshell-0 -n "${NAMESPACE}" --timeout=120s
oc apply -f "${MANIFESTS}/05-network-policy.yaml"
echo ""

# Step 5: Verify secrets exist
echo "[5/7] Verifying secrets..."
if oc get secret leadership-report-config -n "${NAMESPACE}" &>/dev/null; then
  missing_keys=()
  for key in doc-id model-id meeting-name gcp-project; do
    value="$(oc get secret leadership-report-config -n "${NAMESPACE}" -o "jsonpath={.data.${key}}")"
    if [[ -z "${value}" ]]; then
      missing_keys+=("${key}")
    fi
  done
  if [[ "${#missing_keys[@]}" -eq 0 ]]; then
    echo "  leadership-report-config: OK"
  else
    echo "  leadership-report-config: MISSING KEYS: ${missing_keys[*]}" >&2
    exit 1
  fi
else
  echo "  leadership-report-config: MISSING"
  echo "  Create it with:"
  echo "    oc create secret generic leadership-report-config -n ${NAMESPACE} \\"
  echo "      --from-literal=doc-id=YOUR_DOC_ID \\"
  echo "      --from-literal=model-id=rits/zai-org/glm-5-3 \\"
  echo "      --from-literal=meeting-name=\"Your Meeting Name\" \\"
  echo "      --from-literal=gcp-project=your-gcp-project"
  echo ""
  exit 1
fi

echo ""

# Step 6: Deploy the CronJob
echo "[6/7] Deploying weekly CronJob..."
sed "s|value: \"quay.io/rh-ee-evhoward/leadership-report-agent:latest\"|value: \"${AGENT_IMAGE}\"|g" \
  "${MANIFESTS}/06-cronjob.yaml" > "${TMP_DIR}/06-cronjob.yaml"
oc apply -f "${TMP_DIR}/06-cronjob.yaml"
echo ""

# Step 7: Verify NetworkPolicy
echo "[7/7] Verifying NetworkPolicy..."
oc get networkpolicy -n "${NAMESPACE}"
echo ""

echo "=== Installation complete ==="
echo ""
echo "Gateway pod:"
oc get pods -n "${NAMESPACE}"
echo ""
echo "CronJob:"
oc get cronjob -n "${NAMESPACE}"
echo ""
echo "Schedule: Every Wednesday at 18:00 UTC"
echo ""
echo "Next step: configure providers with scripts/configure-providers.sh"
echo ""
echo "Test with a manual run:"
echo "  oc create job --from=cronjob/leadership-report-agent test-run -n ${NAMESPACE}"
echo "  oc logs -f job/test-run -n ${NAMESPACE}"
