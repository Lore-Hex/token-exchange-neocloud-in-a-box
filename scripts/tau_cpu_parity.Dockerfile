# SPDX-License-Identifier: Apache-2.0
# Exact existing pilot bases, CMake wheel and authenticated inputs only.
ARG PYTHON_IMAGE
ARG GO_IMAGE
FROM ${PYTHON_IMAGE} AS python
FROM ${GO_IMAGE}
COPY --from=python /usr/local/ /usr/local/
COPY cmake/ /wheelhouse/
COPY repo/scripts/pilot/build-requirements.lock /wheelhouse/requirements.lock
RUN --network=none ldconfig \
    && python3 -I -m pip --isolated --disable-pip-version-check install \
       --no-index --find-links=/wheelhouse --no-deps --require-hashes \
       --target=/opt/cmake-wheel -r /wheelhouse/requirements.lock \
    && test "$(uname -m)" = x86_64 \
    && test "$(gcc -dumpmachine)" = x86_64-linux-gnu \
    && python3 -I -S -B -c 'import hashlib,ssl; import sys; assert sys.version_info[:2] == (3,12)' \
    && /opt/cmake-wheel/cmake/data/bin/cmake --version
ENV PATH=/opt/cmake-wheel/cmake/data/bin:/usr/local/bin:/usr/bin:/bin
COPY repo/ /opt/nib/
COPY inputs/ /inputs/
RUN --network=none chmod -R a+rX,go-w /opt/nib /inputs
WORKDIR /opt/nib
USER 65532:65532
ENTRYPOINT ["python3", "-I", "-S", "-B", "scripts/record-tau-cpu-parity.py", "--worker"]
