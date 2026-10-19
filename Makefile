SHELL := /bin/bash
.DELETE_ON_ERROR:
.PHONY: check test test-e2e lint fmt-check staticcheck shellcheck yamllint docs-check secrets secrets-positive-control build

check: lint test secrets secrets-positive-control

test:
	go test -race -count=1 ./...

build:
	go build -trimpath -o build/nib ./cmd/nib

lint: fmt-check staticcheck shellcheck yamllint docs-check
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

docs-check:
	uv run --project tools --frozen python scripts/check-doc-links.py
	uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_*.py'

secrets:
	@go -C tools build -o ../build/gitleaks github.com/zricethezav/gitleaks/v8
	./build/gitleaks dir --redact --no-banner --exit-code 1 .

secrets-positive-control: secrets
	bash scripts/test-secret-scan.sh

test-e2e:
	@printf '%s\n' 'P1 is not implemented: no GPU-free end-to-end node exists yet.' >&2
	@exit 1
