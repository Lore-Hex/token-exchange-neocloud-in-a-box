# SPDX-License-Identifier: Apache-2.0
# All base references are immutable digests from pins.json; no runtime downloads.
ARG GO_IMAGE
ARG PYTHON_IMAGE
FROM ${GO_IMAGE} AS engine-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=readonly
WORKDIR /src
COPY repo/ ./
RUN go build -trimpath -buildvcs=false -tags nibfake -o /out/nib-engine ./guest/cmd/nib-engine \
    && go build -trimpath -buildvcs=false -o /out/nib-release ./cmd/nib

FROM ${PYTHON_IMAGE} AS python-base
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY repo/scripts/e2e/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.lock
WORKDIR /opt/nib-e2e
COPY repo/scripts/e2e/exercise.py ./exercise.py
COPY repo/guest/testdata/fake/ ./fixtures/guest/
COPY repo/internal/config/testdata/valid/ ./fixtures/schema/
COPY repo/scripts/e2e/pins.json ./pins.json
COPY inputs.json ./inputs.json

FROM python-base AS engine-e2e
COPY --from=engine-build /out/ /opt/nib-e2e/bin/
USER 65532:65532
ENTRYPOINT ["python", "/opt/nib-e2e/exercise.py", "--engine-only"]

FROM engine-build AS node-build
ARG TRCS_ARCHIVE_SHA256
ARG TRCS_BINARY_SHA256
COPY trcs.tar.gz /tmp/trcs.tar.gz
RUN printf '%s  /tmp/trcs.tar.gz\n' "$TRCS_ARCHIVE_SHA256" | sha256sum -c - \
    && mkdir /trcs && tar -xzf /tmp/trcs.tar.gz --strip-components=1 -C /trcs \
    && cd /trcs && go build -trimpath -buildvcs=false -ldflags='-s -w' -o /out/trcs ./cmd/trcs \
    && printf '%s  /out/trcs\n' "$TRCS_BINARY_SHA256" | sha256sum -c -
RUN go build -trimpath -buildvcs=false -tags nibfake -o /out/nib-fake ./cmd/nib \
    && go build -trimpath -buildvcs=false -tags nibfake -o /out/fake-agent ./tests/e2e/fake-agent

FROM python-base AS node-e2e
COPY --from=node-build /out/ /opt/nib-e2e/bin/
USER 65532:65532
ENTRYPOINT ["python", "/opt/nib-e2e/exercise.py"]
