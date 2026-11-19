# Secret Manager containers (secret payload versions are uploaded out-of-band, never in Git).
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "assessment-agent-gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret" "github_readonly_token" {
  secret_id = "assessment-agent-github-readonly-token"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret_iam_member" "gemini_key_accessor" {
  secret_id = google_secret_manager_secret.gemini_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.assessment_agent_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "github_token_accessor" {
  secret_id = google_secret_manager_secret.github_readonly_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.assessment_agent_sa.email}"
}
