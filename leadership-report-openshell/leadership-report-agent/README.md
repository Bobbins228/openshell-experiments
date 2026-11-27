# Leadership Report Agent

Self-contained agent that fetches Google Meet/Gemini meeting notes, analyzes them via EnMaaS, generates a structured leadership report, and publishes it to a Google Doc. The Python client uses an OpenAI-compatible API.

## Pipeline

```
Google Drive       LLM (EnMaaS)      Google Docs
 (meeting    --->  (analyze &   --->  (formatted
  notes)            structure)         report)
```

1. **Auth check** — obtains Google credentials before doing any work
2. **Fetch** — searches Google Drive for the latest Gemini notes matching the meeting name, exports as plain text
3. **Analyze** — sends notes to the LLM with a structured prompt, receives JSON report back via Chat Completions API with JSON mode
4. **Push** — inserts the formatted report (date header, summary, bulleted items with bold labels) after the configured marker in the target Google Doc

Transient failures (network, server errors) are retried once after 30 seconds.
For a failed Google Docs write, the agent checks a unique named range and retries only against the original document revision. If it cannot determine the outcome, it stops and asks for the document to be inspected before a manual retry. A separate run is treated as a new report.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_BASE_URL` | Yes | EnMaaS endpoint: `https://api.enmaas.devshift.net/v1` |
| `OPENAI_API_KEY` | Yes for EnMaaS | EnMaaS API key; OpenShell injects a provider placeholder |
| `MODEL_ID` | Yes | Model identifier served by the endpoint |
| `LEADERSHIP_DOC_ID` | Yes | Google Doc ID to push the report to (the alphanumeric string from the doc URL) |
| `GCP_DRIVE_DOCS_ACCESS_TOKEN` | OpenShell | Gateway-injected Drive/Docs access-token placeholder |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` | Standalone only | Raw OAuth refresh credentials for local testing; do not put these in the CronJob |
| `GOOGLE_QUOTA_PROJECT` | Yes (K8s) | GCP project with Drive/Docs APIs enabled |
| `MEETING_NAME` | Yes | Google Meet meeting name to search for Gemini notes |

For local development without raw OAuth variables, run:

```shell
gcloud auth application-default login \
  --client-id-file=/path/to/your-desktop-oauth-client.json \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents
```

Ensure the authenticated account has Drive and Docs access.

## Running Locally

```shell
export OPENAI_BASE_URL="https://api.enmaas.devshift.net/v1"
read -rs OPENAI_API_KEY; echo; export OPENAI_API_KEY  # Paste your EnMaaS key
export MODEL_ID="rits/zai-org/glm-5-3"
export LEADERSHIP_DOC_ID="your-google-doc-id-here"
export MEETING_NAME="Your Meeting Name"

# Install the one dependency
pip install openai

# Run
python3 agent.py
```

## Running as a Container

Build:

```shell
podman build -f Containerfile -t leadership-report-agent .
```

Run:

```shell
podman run --rm \
  -e OPENAI_BASE_URL="https://api.enmaas.devshift.net/v1" \
  -e OPENAI_API_KEY \
  -e MODEL_ID="rits/zai-org/glm-5-3" \
  -e LEADERSHIP_DOC_ID="your-google-doc-id-here" \
  -e GOOGLE_QUOTA_PROJECT="my-gcp-project" \
  -e MEETING_NAME="Your Meeting Name" \
  -e GCP_DRIVE_DOCS_ACCESS_TOKEN="$(gcloud auth application-default print-access-token)" \
  leadership-report-agent
```

## Running on OpenShift (via OpenShell)

See the [parent repo README](../README.md) for full OpenShell deployment instructions. This agent runs as a weekly CronJob inside an OpenShell sandbox with EnMaaS providing inference.

## Making updates
To update the images, push each image tagged with the current date as well as tagged "latest". Before pushing a "latest" image or deploying to the actual agent, [test your changes on OpenShift](#set-up-and-test-on-openshift-for-development).
```shell
podman login quay.io

# from leadership-report-openshell dir
podman build --platform linux/amd64 \
  -f launcher/Containerfile \
  -t quay.io/YOUR-USER/openshell-launcher:YYYY-MM-DD .

podman build --platform linux/amd64 \
  -f leadership-report-agent/Containerfile \
  -t quay.io/YOUR-USER/leadership-report-agent:YYYY-MM-DD \
  leadership-report-agent

podman push quay.io/YOUR-USER/openshell-launcher:YYYY-MM-DD # date
podman push quay.io/YOUR-USER/leadership-report-agent:YYYY-MM-DD # date
# also tag and push as latest
podman tag quay.io/YOUR-USER/openshell-launcher:YYYY-MM-DD \
  quay.io/YOUR-USER/openshell-launcher:latest

podman tag quay.io/YOUR-USER/leadership-report-agent:YYYY-MM-DD \
  quay.io/YOUR-USER/leadership-report-agent:latest 

podman push quay.io/YOUR-USER/openshell-launcher:latest 
podman push quay.io/YOUR-USER/leadership-report-agent:latest
```


## Report Format

The LLM produces JSON matching this schema:

```json
{
  "date": "Jun 18, 2026",
  "summary": "One paragraph summarizing overall team status.",
  "bullets": [
    {"label": "Category:", "text": " Detail text with attribution. (Person)\n"},
    {"label": "Another:", "text": " More details. (Person, Person)\n"}
  ]
}
```

The push script formats this into the Google Doc with a date header, summary paragraph, and bulleted list with bold category labels after the configured insertion marker.

## Exit Codes

| Code | Meaning | Behavior |
|------|---------|----------|
| 0 | Success | Report published |
| 1 | Usage error | Bad args or invalid input |
| 2 | Permanent failure | Auth invalid, doc missing, bad config |
| 3 | Transient failure | Network/server error — auto-retried once |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `AUTH_FAILED: Refresh token expired` | Replace the standalone raw OAuth credentials, or unset `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` before using ADC login |
| `FETCH_FAILED: No Google Doc found` | Gemini notes not enabled for this meeting, or wrong `MEETING_NAME` |
| `FETCH_FAILED: Access denied (403)` | Check `GOOGLE_QUOTA_PROJECT` is set and valid |
| `LLM_FAILED: Cannot reach inference endpoint` | Check `OPENAI_BASE_URL` and network connectivity to EnMaaS |
| `LLM_FAILED: Model returned invalid JSON` | Model may not support JSON mode well — try a larger model |
| `PUSH_FAILED: Could not find insertion point` | The doc's "Purpose" section was removed or changed |

## Standalone Modules

Individual modules can still be run standalone for debugging:

```shell
python3 -m lib.auth                           # Test auth
python3 -m lib.fetch_gdoc /tmp/notes.txt      # Fetch notes only
python3 -m lib.push_to_gdoc /tmp/report.json  # Push a pre-built report
```


## Set up and test on OpenShift (for development)
When making changes, run a test run on OpenShift with temporary credentials and test Docs.

### Prerequisites
- OpenShift 4.19 or newer with Landlock ABI v3 support
- `oc` and Helm 3
- OpenShell CLI `v0.1.8` or newer
- Agent Sandbox installed on the cluster
- `gcloud` with Application Default Credentials for the Google provider setup
- An EnMaaS API key and access to the chosen model

### 1. Prepare Docs (first time set-up)
Create two test Google Docs- one containing meeting notes and one to hold the output. Use an arbitrary name to avoid colliding with real documents. 

Include a paragraph in the output doc containing 'Please add the current date'. This is how the agent finds the position to place the report.

### 2. Authenticate to Google
**First time set-up only:** Set your GCloud project and create a Desktop oauth client. Select 'Use this client for an AI-powered agent'. Save the client JSON in a safe place outside the directory.

Authenticate via CLI:
```shell
gcloud auth application-default login \
  --client-id-file="/path/to/CLIENT_FILE.json" \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents
```

### 3. Build & push images
```shell
QUAY_USER=YOUR-USER

podman login quay.io

podman build --platform linux/amd64 \
  -f launcher/Containerfile \
  -t quay.io/$QUAY_USER/openshell-launcher:test .

podman build --platform linux/amd64 \
  -f leadership-report-agent/Containerfile \
  -t quay.io/$QUAY_USER/leadership-report-agent:test \
  leadership-report-agent

podman push quay.io/$QUAY_USER/openshell-launcher:test
podman push quay.io/$QUAY_USER/leadership-report-agent:test
```

Set the repository to public in Quay, or configure image pull credentials.

### 4. Configure cron-job manifest
For testing purposes, the cron-job should be suspended. Edit `manifests/06-cronjob.yaml`.
```yaml
spec:
  suspend: true
  schedule: "0 18 * * 3"
```

Update the launcher image to use your new image.
```yaml
image: quay.io/YOUR_USER/openshell-launcher:test
```

### 4. Create the namespace and configuration secret
```shell
oc apply -f manifests/01-namespace.yaml

oc create secret generic leadership-report-config -n openshell \
  --from-literal=doc-id=YOUR-DOC-ID \
  --from-literal=model-id=rits/zai-org/glm-5-3 \
  --from-literal=meeting-name='YOUR TEST MEETING NAME' \
  --from-literal=gcp-project=YOUR-GWS-PROJECT \
  --dry-run=client -o yaml | oc apply -f -
```

### 5. Run the installer
Explicitly specify your new agent image & run the script.
```shell
AGENT_IMAGE=quay.io/YOUR-USERNAME/leadership-report-agent:test \
  ./scripts/install.sh
```

Verify that the gateway pod is running and the `Suspend` is `True`.
```shell
oc get pods -n openshell
oc get cronjob leadership-report-agent -n openshell
```

### 6. Configure providers
Create providers for Google credentials and EnMaaS key.

Login with desktop OAuth.
```shell
gcloud auth application-default login \
  --client-id-file=/path/to/CLIENT_FILE.json \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents
```

In another terminal, start a port-forward and leave it running.
```shell
oc port-forward service/openshell 18080:8080 -n openshell
```

Run the providers script. If prompted, paste your EnMaaS key.
```shell
env -u OPENAI_API_KEY ./scripts/configure-providers.sh
```

### 7. Start a test run
Start the job manually.
```shell
oc create job --from=cronjob/leadership-report-agent \
  leadership-report-test -n openshell
```

Follow the logs to completion.
```shell
oc logs -f job/leadership-report-test -n openshell
```

Check the output doc for results.