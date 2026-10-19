# SPDX-License-Identifier: Apache-2.0
# Supply both immutable base references from scripts/e2e/pins.json.
# Context: repo/scripts/{pilot,e2e}, inputs/model.gguf, validated engine/ and trcs/.
ARG GO_IMAGE
ARG PYTHON_IMAGE

FROM ${PYTHON_IMAGE} AS cmake-wheel
COPY repo/scripts/pilot/build-requirements.lock /tmp/build-requirements.lock
RUN python -I -m pip --isolated --disable-pip-version-check install \
    --index-url=https://pypi.org/simple --no-cache-dir --only-binary=:all: \
    --require-hashes --no-deps --target=/opt/cmake-wheel -r /tmp/build-requirements.lock

FROM ${GO_IMAGE} AS build
ARG TARGETOS
ARG TARGETARCH
ENV CGO_ENABLED=0 GOENV=off GOWORK=off GOTOOLCHAIN=local GOFLAGS=-mod=readonly \
    GOOS=linux GOARCH=amd64 GOAMD64=v1 GOPROXY=off GOSUMDB=off \
    PATH=/opt/cmake/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY --from=cmake-wheel /opt/cmake-wheel/cmake/data/ /opt/cmake/
RUN --network=none test "$TARGETOS/$TARGETARCH" = linux/amd64 \
    && test "$(uname -m)" = x86_64 \
    && test "$(gcc -dumpmachine)" = x86_64-linux-gnu \
    && test -f "$(g++ -print-file-name=libstdc++.a)" \
    && test -f "$(gcc -print-file-name=libgcc.a)" \
    && cmake --version
COPY engine/ /src/engine/
# Pinned source: https://github.com/ggml-org/llama.cpp/tree/620d2225cc436ab0b001e3200e590e3b286ef9ab
# ggml/CMakeLists.txt and ggml/src/ggml-cpu/CMakeLists.txt: NATIVE=OFF alone
# leaves INS_ENB defaults on. Explicitly disable every optional x86 extension.
# Root CMakeLists.txt and tools/ui/CMakeLists.txt define the no-UI/download flags.
# Static C++/GCC runtimes avoid depending on libstdc++ in Python slim:
# https://gcc.gnu.org/onlinedocs/gcc-12.2.0/gcc/Link-Options.html
# Baseline ISA/tuning: https://gcc.gnu.org/onlinedocs/gcc-12.2.0/gcc/x86-Options.html
RUN --network=none cmake -S /src/engine -B /build/engine \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-march=x86-64 -mtune=generic" \
    -DCMAKE_CXX_FLAGS="-march=x86-64 -mtune=generic" \
    -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_CPU=ON -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=OFF -DGGML_BACKEND_DL=OFF \
    -DGGML_SSE42=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_AVX_VNNI=OFF \
    -DGGML_BMI2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF \
    -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF \
    -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF -DGGML_AMX_BF16=OFF \
    -DGGML_LLAMAFILE=OFF -DGGML_CCACHE=OFF -DGGML_OPENMP=OFF -DGGML_BLAS=OFF \
    -DGGML_METAL=OFF -DGGML_CUDA=OFF \
    -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_APP=OFF \
    -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
    -DLLAMA_OPENSSL=OFF -DLLAMA_SUBPROCESS=OFF -DLLAMA_LLGUIDANCE=OFF \
    && test -s /build/engine/compile_commands.json \
    && ! grep -E -- '-m(avx|fma|f16c|bmi|amx|sse4|arch=native|cpu=native)' /build/engine/compile_commands.json
RUN --network=none cmake --build /build/engine --target llama-server --parallel 2 \
    && install -D -m 0755 /build/engine/bin/llama-server /out/llama-server
COPY trcs/ /src/trcs/
WORKDIR /src/trcs
RUN --network=none go mod verify \
    && go build -trimpath -buildvcs=false -ldflags='-s -w' -o /out/trcs ./cmd/trcs

FROM ${PYTHON_IMAGE} AS pilot
ARG GO_IMAGE
ARG PYTHON_IMAGE
ARG TARGETOS
ARG TARGETARCH
LABEL org.opencontainers.image.title="Hixtube local Linux Qwen pilot" \
    io.hixtube.scope="software-only" io.hixtube.confidential="false" \
    io.hixtube.hardware-validated="false"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/nib-pilot
COPY repo/scripts/e2e/requirements.lock /tmp/requirements.lock
RUN python -I -m pip --isolated --disable-pip-version-check install \
    --index-url=https://pypi.org/simple --no-cache-dir --only-binary=:all: \
    --require-hashes --no-deps -r /tmp/requirements.lock
COPY repo/scripts/pilot/ ./scripts/pilot/
COPY repo/scripts/e2e/ ./scripts/e2e/
COPY --from=build /out/ ./bin/
COPY inputs/model.gguf ./model.gguf
RUN --network=none python -I -c 'import hashlib,json,os,pathlib; \
    p=pathlib.Path("."); b=json.loads((p/"scripts/e2e/pins.json").read_text()); \
    m=json.loads((p/"scripts/pilot/pins.json").read_text())["model"]; \
    assert os.environ["TARGETOS"]+"/"+os.environ["TARGETARCH"]==b["platform"]=="linux/amd64"; \
    assert os.environ["GO_IMAGE"]==b["go_image"] and os.environ["PYTHON_IMAGE"]==b["python_image"]; \
    assert (p/"model.gguf").stat().st_size==m["bytes"]; \
    assert hashlib.file_digest((p/"model.gguf").open("rb"),"sha256").hexdigest()==m["sha256"]; \
    assert hashlib.file_digest((p/"bin/trcs").open("rb"),"sha256").hexdigest()==b["trcs"]["binary_sha256"]' \
    && chmod -R a+rX,go-w /opt/nib-pilot
USER 65532:65532
# Loader/import checks only; no model inference, key generation or listening socket.
RUN --network=none ./bin/llama-server --version \
    && openssl version \
    && trustedrouter-provider-check --help >/dev/null \
    && python -I -c 'import httpx,jsonschema,ssl'
# The host must enforce --network=none and --read-only with private writable /tmp.
# No VOLUME or EXPOSE; credentials, host mounts and published ports are not inputs.
ENTRYPOINT ["python", "-I", "scripts/pilot/run.py", "--prepared", "/opt/nib-pilot"]
