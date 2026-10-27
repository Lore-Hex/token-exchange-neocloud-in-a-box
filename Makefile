SHELL := /bin/bash
.DELETE_ON_ERROR:
.PHONY: check test test-e2e test-evidence lint fmt-check staticcheck shellcheck yamllint schema-lint docs-check terraform-check secrets secrets-positive-control build

check: lint terraform-check test test-evidence secrets secrets-positive-control

test:
	go test -race -p=1 -count=1 ./...
	go test -race -p=1 -tags nibfake -count=1 ./...

test-evidence: build
	sh scripts/test-scrub-secrets.sh

build:
	go build -trimpath -o build/nib ./cmd/nib

lint: fmt-check staticcheck shellcheck yamllint schema-lint docs-check
	go vet ./...

fmt-check:
	@files="$$(gofmt -l $$(git ls-files --cached --others --exclude-standard '*.go'))"; test -z "$$files" || { printf 'Run gofmt on:\n%s\n' "$$files"; exit 1; }

staticcheck:
	@go -C tools build -o ../build/staticcheck honnef.co/go/tools/cmd/staticcheck
	./build/staticcheck ./...

shellcheck:
	@find scripts -type f -name '*.sh' -print0 | xargs -0 shellcheck

yamllint:
	uv run --project tools --frozen yamllint --strict .github .yamllint.yaml
	@find schemas models profiles -type f \( -name '*.yaml' -o -name '*.yml' \) ! -path '*/testdata/*' ! -path '*/tests/*' -print0 | xargs -0 -r uv run --project tools --frozen yamllint --strict

schema-lint:
	go run ./cmd/nib lint

docs-check:
	uv run --project tools --frozen python scripts/check-doc-links.py
	uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_*.py'

terraform-check:
	uv run --project tools --frozen python scripts/check_gcp_terraform.py

secrets:
	@go -C tools build -o ../build/gitleaks github.com/zricethezav/gitleaks/v8
	./build/gitleaks dir --redact --no-banner --exit-code 1 .
	./build/gitleaks git --redact --no-banner --exit-code 1 --log-opts=--all .

secrets-positive-control: secrets
	bash scripts/test-secret-scan.sh

test-e2e:
	python3 scripts/e2e/run.py
