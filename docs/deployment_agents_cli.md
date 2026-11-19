# Deployment Guide: Agents CLI, Vertex AI Agent Engine & Terraform

This guide walks through provisioning Google Cloud resources via Terraform and deploying the **AI in 5 Days Multilingual Assessment Agent** with **Google Agents CLI** and **ADK 2.0**.

## 1. Prerequisites

- Google Cloud Project with billing enabled
- `gcloud`, `terraform >= 1.6`, `uv` / `uvx`, and `python >= 3.11`
- Never commit `.env` files or API keys to Git.

## 2. Provision Infrastructure with Terraform (IaC)

```bash
cd terraform
terraform init
terraform plan -var="project_id=${GOOGLE_CLOUD_PROJECT}" -var="region=us-central1"
terraform apply -var="project_id=${GOOGLE_CLOUD_PROJECT}" -var="region=us-central1"
```

Terraform provisions:
- Required APIs (`aiplatform`, `run`, `secretmanager`, `cloudtrace`, `dlp`, `logging`, `artifactregistry`, `cloudbuild`)
- Dedicated least-privilege Service Account (`assessment-agent-<env>`)
- Google Cloud Secret Manager shells (`assessment-agent-gemini-api-key`, `assessment-agent-github-readonly-token`)
- Artifact Registry repository + Cloud Run v2 service with Cloud Trace and Cloud DLP enabled

## 3. Populate Secrets Securely in Google Cloud Secret Manager

```bash
printf '%s' "${MY_GEMINI_API_KEY}" | gcloud secrets versions add assessment-agent-gemini-api-key --data-file=-
printf '%s' "${MY_GITHUB_READONLY_TOKEN}" | gcloud secrets versions add assessment-agent-github-readonly-token --data-file=-
```

## 4. Deploy with Agents CLI & ADK Agent Engine

Install the 7 bundled lifecycle skills from Google Agents CLI (`workflow`, `adk-code`, `scaffold`, `eval`, `deploy`, `publish`, `observability`):

```bash
uvx google-agents-cli setup
```

Run pre-deployment evaluations and deploy to Vertex AI / GEAP Agent Runtime:

```bash
# 1. Run unit tests & golden dataset regression evals
make test
make eval

# 2. Deploy to Vertex AI Agent Engine with Cloud Trace enabled
adk deploy agent_engine \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="${GOOGLE_CLOUD_LOCATION:-us-central1}" \
  --display_name="ai-in-5-days-assessment-agent" \
  assessment_agent
```

## 5. Local Interactive Testing (`adk web`)

```bash
cp .env.example .env
pip install -e ".[dev]"
adk web .
```

Inspect live OpenTelemetry spans in **Google Cloud Trace** and structured JSON Intent/Outcome events in **Cloud Logging**.
