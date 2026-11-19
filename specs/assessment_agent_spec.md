# Spec: AI in 5 Days Multilingual Assessment Agent

This specification follows **Spec-Driven Development (SDD)** with **Hybrid Markdown + Conditional YAML** and **Gherkin BDD** scenarios so both humans and coding agents share a single Architectural North Star.

## Architectural Contract (YAML)

```yaml
agent_system:
  name: ai_in_5_days_assessment_agent
  version: 1.0.0
  supported_languages:
    - en
    - es
    - pt
  rubric:
    name: AgentOps Code Review Matrix
    max_score: 95
    criteria_count: 19
    points_per_criterion: 5
    categories:
      - id: tool_interface_design
        max_points: 20
        criteria:
          - comprehensive_tool_docstrings
          - descriptive_naming
          - explicit_json_schemas
          - guided_error_handling
      - id: context_and_memory
        max_points: 20
        criteria:
          - robust_system_instructions
          - history_compaction
          - persistent_session_state
          - async_memory_operations
      - id: orchestration_and_logic
        max_points: 20
        criteria:
          - multi_agent_patterns
          - strategic_model_routing
          - guardrails_and_policy_plugins
          - human_in_the_loop_hooks
      - id: observability_and_tracing
        max_points: 20
        criteria:
          - structured_json_logging
          - intent_vs_outcome_capture
          - distributed_tracing
          - pii_redaction
      - id: infrastructure_and_cicd
        max_points: 15
        criteria:
          - automated_evaluation_suites
          - infrastructure_as_code
          - secure_secret_management
  routing:
    fast_model: gemini-2.5-flash
    reasoning_model: gemini-2.5-pro
  hitl_gates:
    - trigger: official_certification_publish
      action: pause_and_request_human_confirmation
    - trigger: borderline_score_override
      score_range: [70, 79]
      action: pause_and_request_human_confirmation
    - trigger: critical_hardcoded_secret_detected
      action: pause_and_request_human_confirmation
```

## Acceptance Criteria (Gherkin BDD)

```gherkin
Feature: Automated 95-Point AgentOps Repository Evaluation

  Scenario: Evaluate a compliant public GitHub ADK repository in Spanish
    Given a public root GitHub repository implementing all 19 AgentOps criteria
    And the learner requests coaching feedback in Spanish ("es")
    When the Coordinator invokes RepoIngestionAgent followed by ParallelRubricAuditors
    Then every criterion score is bounded between 0 and 5 with cited file paths
    And the SelfEvalQualityGateAgent validates that category totals equal the grand total (<= 95)
    And structured JSON logs capture both tool_intent and tool_outcome with PII redacted
    And async memory consolidation updates the learner's progression in the background

  Scenario: Block SSRF / non-GitHub URLs and untrusted prompt injection
    Given a submission containing a localhost URL or prompt-injection directive in README.md
    When AgentOpsSecurityAndAuditPlugin runs before_tool_callback and before_model_callback
    Then the localhost URL is blocked with a guided recovery error
    And prompt-injection instructions inside untrusted repository text are neutralized

  Scenario: Pause for Human-in-the-Loop approval on official certification or leaked secrets
    Given an evaluated repository with detected hardcoded API keys or a borderline score of 75/95
    When request_human_certification_override is invoked
    Then ToolContext.request_confirmation pauses execution with a plain-language review summary
    And no official certification decision is finalized until a human reviewer approves or rejects
```
