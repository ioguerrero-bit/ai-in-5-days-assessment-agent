resource "google_artifact_registry_repository" "agent_repo" {
  location      = var.region
  repository_id = "assessment-agent"
  format        = "DOCKER"
  description   = "Container images for the AI in 5 Days Multilingual Assessment Agent."

  depends_on = [google_project_service.enabled_apis]
}

resource "google_cloud_run_v2_service" "assessment_agent_service" {
  name     = "ai-in-5-days-assessment-agent-${var.environment}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.assessment_agent_sa.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }

      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "TRUE"
      }

      env {
        name  = "ENABLE_CLOUD_DLP"
        value = var.enable_cloud_dlp ? "TRUE" : "FALSE"
      }

      env {
        name  = "DEFAULT_REPORT_LANGUAGE"
        value = var.default_report_language
      }

      env {
        name  = "GEMINI_API_KEY_SECRET_NAME"
        value = google_secret_manager_secret.gemini_api_key.secret_id
      }

      env {
        name  = "GITHUB_TOKEN_SECRET_NAME"
        value = google_secret_manager_secret.github_readonly_token.secret_id
      }
    }
  }

  depends_on = [
    google_project_service.enabled_apis,
    google_project_iam_member.runtime_permissions,
  ]
}
