#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
GATEWAY_ENDPOINT="${OPENSHELL_GATEWAY_ENDPOINT:-https://localhost:18080}"
CONFIG_DIR="$(mktemp -d)"

trap 'rm -rf "${CONFIG_DIR}"' EXIT

TLS_DIR="${CONFIG_DIR}/openshell/gateways/openshift/mtls"
mkdir -p "${TLS_DIR}"
oc extract "secret/openshell-client-tls" -n "${NAMESPACE}" --to="${TLS_DIR}" --confirm

export XDG_CONFIG_HOME="${CONFIG_DIR}"
export OPENSHELL_GATEWAY="openshift"
export OPENSHELL_GATEWAY_ENDPOINT="${GATEWAY_ENDPOINT}"
export OPENSHELL_WORKSPACE=default

configure_profile() {
  local profile_id="$1"
  local definition="${REPO_DIR}/leadership-report-agent/providers/$2"
  local update_file="${CONFIG_DIR}/${profile_id}.yaml"
  local installed version

  if ! openshell provider profile describe "${profile_id}" >/dev/null 2>&1; then
    openshell provider profile import --file "${definition}"
  elif [[ "${UPDATE_PROVIDER_PROFILES:-0}" == "1" ]]; then
    installed="$(openshell provider profile export "${profile_id}" -o yaml)"
    version="$(awk '/^resource_version:[[:space:]]/ && NF == 2 { print $2 }' <<< "${installed}")"
    if [[ ! "${version}" =~ ^[0-9]+$ ]]; then
      echo "Could not read resource_version for ${profile_id}." >&2
      return 1
    fi
    {
      printf 'resource_version: %s\n' "${version}"
      cat "${definition}"
    } > "${update_file}"
    openshell provider profile update "${profile_id}" --file "${update_file}"
  else
    echo "Using existing ${profile_id} profile."
  fi
}

configure_profile google-drive-docs google-drive-docs.yaml
configure_profile leadership-report-enmaas enmaas.yaml

if ! openshell provider get google-drive-docs >/dev/null 2>&1; then
  openshell provider create \
    --name google-drive-docs \
    --type google-drive-docs \
    --from-gcloud-adc
fi

if ! openshell provider get leadership-report-enmaas >/dev/null 2>&1; then
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    if [[ ! -t 0 ]]; then
      echo "Set OPENAI_API_KEY to your EnMaaS API key before running this script." >&2
      exit 1
    fi
    read -r -s -p "EnMaaS API key: " OPENAI_API_KEY
    echo
  fi
  if [[ -z "${OPENAI_API_KEY}" ]]; then
    echo "EnMaaS API key must not be empty." >&2
    exit 1
  fi
  export OPENAI_API_KEY
  openshell provider create \
    --name leadership-report-enmaas \
    --type leadership-report-enmaas \
    --credential OPENAI_API_KEY
fi

openshell provider list
