# Leadership Report Agent on OpenShell

This project deploys the Leadership Report Agent as a weekly OpenShell sandbox CronJob on OpenShift. The agent fetches Gemini meeting notes from Google Drive, sends them to EnMaaS, validates the returned JSON, and publishes a formatted report to Google Docs.

## Architecture

```text
CronJob launcher
  ├─ authenticates to OpenShell with chart-generated mTLS credentials
  ├─ creates a no-keep sandbox
  │   ├─ attaches the google-drive-docs provider
  │   ├─ attaches the leadership-report-enmaas provider
  │   └─ applies endpoint-scoped policy
  └─ runs python3 agent.py

Sandbox agent
  ├─ reads Google Drive meeting notes
  ├─ calls the hosted EnMaaS endpoint through the OpenShell proxy
  └─ inserts the report into the target Google Doc
```

Google OAuth material and the EnMaaS API key are stored by the OpenShell gateway and resolved at request time. The launcher and CronJob pod do not receive the raw credentials. Model inference runs on EnMaaS, so no model server is needed on the cluster.

## Requirements

- OpenShift 4.19 or newer with Landlock ABI v3 support
- `oc` and Helm 3
- OpenShell CLI `v0.1.8` or newer
- Agent Sandbox `v1.0.11` or newer
- `gcloud` with Application Default Credentials for the Google provider setup
- An EnMaaS API key and access to the chosen model
- Network access from the OpenShell proxy to `api.enmaas.devshift.net:443`

## Deployment

Create the namespace and application configuration (the API key is configured separately):

```bash
oc apply -f manifests/01-namespace.yaml
oc create secret generic leadership-report-config -n openshell \
  --from-literal=doc-id=YOUR_GOOGLE_DOC_ID \
  --from-literal=model-id=rits/zai-org/glm-5-3 \
  --from-literal=meeting-name='Your Meeting Name' \
  --from-literal=gcp-project=YOUR_GCP_PROJECT
```

EnMaaS currently has three available models:
- rits/zai-org/glm-5-3
- rits/deepseek-ai/DeepSeek-V4.1-Flash-1017
- rits/meta-models/Muse-Spark-Open-816B-A42B-NVFP4

GLM 5.3 was chosen after comparing the output of all three. To use a different model, substitute `model-id` for another exact model ID string, or patch an existing Secret:
```shell
oc patch secret leadership-report-config -n openshell \
  --type=merge \
  -p '{"stringData":{"model-id":"rits/deepseek-ai/DeepSeek-V4.1-Flash-1017"}}'
```
Choose an EnMaaS model available to your key. The endpoint is `https://api.enmaas.devshift.net/v1`.

Build and push the images described below first, and set the launcher image in the CronJob manifest. Install or cleanly redeploy the platform with your agent image:

```bash
AGENT_IMAGE=quay.io/YOUR_NS/leadership-report-agent:latest ./scripts/install.sh
```

The installer:

1. Installs Agent Sandbox `v1.0.11`.
2. Creates the namespace and launcher RBAC.
3. Installs OpenShell Helm chart `0.1.8` with TLS, mTLS, and authenticated users enabled.
4. Applies the gateway NetworkPolicy.
5. Deploys the weekly CronJob.

OpenShell `0.0.x` sandboxes cannot be upgraded in place. Roll forward by uninstalling the old gateway/runtime and deploying this configuration cleanly, retaining external state and secrets as needed.

## Configure providers

Run this from a workstation with `oc` access and gcloud ADC that includes the required scopes:

```bash
gcloud auth application-default login \
  --client-id-file=/path/to/your-desktop-oauth-client.json \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents
```

```bash
oc port-forward service/openshell 18080:8080 -n openshell
```

Leave the port-forward running in one terminal. In another, run:

```bash
env -u OPENAI_API_KEY ./scripts/configure-providers.sh
```

When prompted, paste your EnMaaS key; it will not be displayed. The helper:

1. Extracts the chart-generated OpenShell client TLS material to a temporary XDG config directory.
2. Imports the scoped Google and EnMaaS provider profiles.
3. Creates or reuses `google-drive-docs` from gcloud ADC with Drive read-only and Documents scopes.
4. Creates or reuses `leadership-report-enmaas`, with the API key stored by the gateway.

Existing profiles and providers are reused, so this helper does not update their definitions or rotate credentials. The default gateway address is `https://localhost:18080`; set `OPENSHELL_GATEWAY_ENDPOINT` if using another reachable gateway address. Application Default Credentials login replaces any existing local ADC credentials.

If these profiles were imported from an earlier version, update them before deploying the new CronJob policy; otherwise their existing broad grants remain active. With the port-forward running, update both profiles using the helper (this replaces their definitions with the checked-in versions):

```bash
UPDATE_PROVIDER_PROFILES=1 env -u OPENAI_API_KEY ./scripts/configure-providers.sh
```

Deploy the updated CronJob and use newly created sandboxes. The Google profile is endpointless; the sandbox policy explicitly binds its credential to the permitted Drive and Docs endpoints.

Job-level retries are disabled: a fresh run cannot safely reconcile a write whose result was lost. Inspect the document before manually rerunning a failed job.

If migrating from the test's `leadership-report-vllm` provider, run this helper to create the new `leadership-report-enmaas` provider with your key. The CronJob uses the new name; the old provider is not automatically removed. Existing configuration secrets may keep their old vLLM keys, but they are no longer needed.

## Build images

```bash
podman build -f launcher/Containerfile -t quay.io/YOUR_NS/openshell-launcher:v0.1.8 .
podman build -f leadership-report-agent/Containerfile \
  -t quay.io/YOUR_NS/leadership-report-agent:latest leadership-report-agent
```

Push the images to a registry accessible to the cluster, update the launcher image in `manifests/06-cronjob.yaml`, and set `AGENT_IMAGE` when running the installer. The checked-in image references are examples; use images built from your changes.

## Run and verify

```bash
oc create job --from=cronjob/leadership-report-agent \
  leadership-report-agent-migration-test -n openshell
oc logs -f job/leadership-report-agent-migration-test -n openshell
```

Required outcomes:

- Google authentication succeeds without raw OAuth variables in the CronJob.
- The latest matching meeting-notes document is non-empty.
- The EnMaaS request reaches the hosted endpoint and produces a report.
- Report validation succeeds.
- The report is inserted at the expected point in the test document.
- The sandbox is deleted after completion.

Before production, test against a dedicated Google Doc and confirm that unrelated Google APIs and inference hosts are denied by sandbox policy. Each successful run inserts another report. Suspend the CronJob during testing if you do not want scheduled runs.

## Files

| Path | Purpose |
|---|---|
| `manifests/03-helm-values.yaml` | Secure schema-v2 OpenShell chart values |
| `manifests/05-rbac.yaml` | Launcher RBAC without secret-read access |
| `manifests/06-cronjob.yaml` | CronJob, mTLS launcher setup, sandbox policy, and provider attachment |
| `leadership-report-agent/providers/enmaas.yaml` | Endpoint-scoped EnMaaS provider profile |
| `scripts/install.sh` | Clean OpenShell and Agent Sandbox installation |
| `scripts/configure-providers.sh` | Gateway provider setup |

## Security posture

- Gateway TLS, mTLS, and authenticated users use current chart defaults.
- Landlock compatibility is `hard_requirement`; unsupported nodes fail closed.
- No privileged SCC binding is used.
- Google credentials and the EnMaaS API key are provider-backed.
- Google network policy is limited to Drive search/export and the configured Docs document.
- EnMaaS policy is limited to `api.enmaas.devshift.net:443` and `POST /v1/chat/completions`.

## Teardown

```bash
oc delete cronjob leadership-report-agent -n openshell
oc delete configmap leadership-report-policy -n openshell
helm uninstall openshell -n openshell
```

Agent Sandbox is cluster-scoped; remove it separately only if no other workload uses it.
