# Dedicated least-privilege runtime identity for the Assessment Agent (Zero Ambient Authority).
resource "google_service_account" "assessment_agent_sa" {
  account_id   = "assessment-agent-${var.environment}"
  display_name = "AI in 5 Days Assessment Agent Runtime (${var.environment})"
  depends_on   = [google_project_service.enabled_apis]
}

locals {
  runtime_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/dlp.user",
  ])
}

resource "google_project_iam_member" "runtime_permissions" {
  for_each = local.runtime_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.assessment_agent_sa.email}"
}
