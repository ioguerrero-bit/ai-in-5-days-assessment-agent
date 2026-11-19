output "service_url" {
  description = "Cloud Run endpoint hosting the ADK Assessment Agent API/UI."
  value       = google_cloud_run_v2_service.assessment_agent_service.uri
}

output "runtime_service_account_email" {
  description = "Least-privilege service account attached to the agent runtime."
  value       = google_service_account.assessment_agent_sa.email
}

output "artifact_registry_repo" {
  description = "Docker Artifact Registry repository for agent container images."
  value       = google_artifact_registry_repository.agent_repo.name
}

output "secret_manager_ids" {
  description = "Provisioned Google Cloud Secret Manager secret IDs."
  value = [
    google_secret_manager_secret.gemini_api_key.secret_id,
    google_secret_manager_secret.github_readonly_token.secret_id,
  ]
}
