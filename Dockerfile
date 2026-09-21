# Fetch wheels separately; avoid native HTTP decompression in the Conda pip.
FROM python:3.11-slim@sha256:5501a4fe605abe24de87c2f3d6cf9fd760354416a0cad0296cf284fddcdca9e2 AS runtime
FROM runtime AS download-wheels
COPY environment/download-requirements.txt /tmp/download-requirements.txt
RUN python -m pip download --disable-pip-version-check --no-cache-dir \
    --only-binary=:all: --no-deps --dest /wheels \
    --requirement /tmp/download-requirements.txt

FROM mambaorg/micromamba:2.9.0@sha256:e0a99b0f17a759e14c2f967dc0ca2d3a3c1ca3c62955f4d20bba770eaaf0184d

COPY --chown=$MAMBA_USER:$MAMBA_USER \
    environment/linux-64.explicit.txt /tmp/environment.explicit.txt

RUN micromamba install --yes --name base \
        --file /tmp/environment.explicit.txt \
    && micromamba clean --all --yes

ARG MAMBA_DOCKERFILE_ACTIVATE=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG ISCE2_COMMIT=b7702cfbd571681a133ebc5c3b23a10c78d52706
ARG BUILD_JOBS=2

RUN mkdir -p /opt/conda/src \
    && git init /opt/conda/src/isce2 \
    && git -C /opt/conda/src/isce2 remote add origin \
        https://github.com/isce-framework/isce2.git \
    && git -C /opt/conda/src/isce2 fetch --depth 1 origin "${ISCE2_COMMIT}" \
    && git -C /opt/conda/src/isce2 checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/conda/src/isce2 rev-parse HEAD)" = "${ISCE2_COMMIT}"

RUN cmake \
        -S /opt/conda/src/isce2 \
        -B /opt/conda/src/isce2/build \
        -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_STANDARD=17 \
        -DCMAKE_C_STANDARD_REQUIRED=ON \
        -DCMAKE_C_EXTENSIONS=ON \
        -DCMAKE_INSTALL_PREFIX=/opt/conda \
        -DCMAKE_PREFIX_PATH=/opt/conda \
        -DPYTHON_MODULE_DIR=packages \
        -DPython_EXECUTABLE=/opt/conda/bin/python \
        -DCMAKE_AR=/opt/conda/bin/x86_64-conda-linux-gnu-ar \
        -DCMAKE_RANLIB=/opt/conda/bin/x86_64-conda-linux-gnu-ranlib \
        -DCMAKE_Fortran_FLAGS="-fallow-argument-mismatch" \
    && cmake --build /opt/conda/src/isce2/build \
        --parallel "${BUILD_JOBS}" \
    && cmake --install /opt/conda/src/isce2/build \
    && mkdir -p /opt/conda/share/isce2 \
    && cp -a /opt/conda/src/isce2/contrib/stack \
        /opt/conda/share/isce2/stack \
    && printf '%s\n' "${ISCE2_COMMIT}" \
        > /opt/conda/share/isce2/source-commit.txt \
    && rm -rf /opt/conda/src/isce2

ENV ISCE_HOME=/opt/conda/packages/isce2
ENV PYTHONPATH=/opt/conda/packages:/opt/conda/share/isce2/stack
ENV PATH=/opt/conda/share/isce2/stack/topsStack:/opt/conda/packages/isce2/applications:${PATH}

COPY --chown=$MAMBA_USER:$MAMBA_USER \
    environment/download-requirements.txt /tmp/download-requirements.txt

COPY --chown=$MAMBA_USER:$MAMBA_USER \
    scripts/install_download_dependencies.py /tmp/install_download_dependencies.py
COPY --from=download-wheels --chown=$MAMBA_USER:$MAMBA_USER /wheels /tmp/download-wheels
RUN python /tmp/install_download_dependencies.py /tmp/download-requirements.txt \
    && rm -rf /tmp/download-wheels

COPY --chown=$MAMBA_USER:$MAMBA_USER src/ /opt/conda/share/sentinel-1-stack/
ENV PYTHONPATH=/opt/conda/share/sentinel-1-stack:/opt/conda/packages:/opt/conda/share/isce2/stack

RUN python -m pip check

WORKDIR /work

# Use the validated official interpreter for every env-python entry point.
# Keep Conda activation for native-library data paths, then select the runtime Python.
COPY --from=runtime /usr/local/ /usr/local/
ENV LD_LIBRARY_PATH=/usr/local/lib:/opt/conda/lib
ENV PYTHONPATH=/opt/conda/share/sentinel-1-stack:/opt/conda/packages:/opt/conda/share/isce2/stack:/opt/conda/lib/python3.11/site-packages
ENV PATH=/usr/local/bin:/opt/conda/share/isce2/stack/topsStack:/opt/conda/packages/isce2/applications:/opt/conda/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
COPY --chmod=755 environment/runtime-entrypoint.sh /usr/local/bin/sentinel-runtime
ENTRYPOINT ["/usr/local/bin/_entrypoint.sh", "/usr/local/bin/sentinel-runtime"]
CMD ["python", "--version"]
