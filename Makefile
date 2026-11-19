.PHONY: install test eval lint run-web tf-validate deploy-agents-cli

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m unittest discover -s tests -p "test_*.py" -v

eval:
	python3 evals/run_eval.py

lint:
	ruff check assessment_agent tests evals

run-web:
	adk web .

tf-validate:
	cd terraform && terraform init -backend=false && terraform validate

deploy-agents-cli:
	uvx google-agents-cli setup
	adk deploy agent_engine \
		--project=$${GOOGLE_CLOUD_PROJECT} \
		--region=$${GOOGLE_CLOUD_LOCATION:-us-central1} \
		--display_name="ai-in-5-days-assessment-agent" \
		assessment_agent
