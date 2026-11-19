variable "project_id" {
  description = "Google Cloud Project ID hosting the Assessment Agent."
  type        = string
}

variable "region" {
  description = "Google Cloud region for Cloud Run, Artifact Registry, and Vertex AI."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "container_image" {
  description = "Artifact Registry image URI built by Cloud Build / Agents CLI."
  type        = string
  default     = "us-central1-docker.pkg.dev/PROJECT_ID/assessment-agent/service:latest"
}

variable "enable_cloud_dlp" {
  description = "Enable Google Cloud DLP de-identification in addition to regex PII redaction."
  type        = bool
  default     = true
}

variable "default_report_language" {
  description = "Default coaching report language (en, es, or pt)."
  type        = string
  default     = "es"
}
