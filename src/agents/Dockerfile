FROM python:3.14.6-slim@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144 AS base

# Install system dependencies required by LiveKit
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libgobject-2.0-0 \
    && rm -rf /var/lib/apt/lists/*


# ---- Builder image ----
FROM base AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

WORKDIR /app

# Install production dependencies without the project itself (cacheable layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Install the project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# ---- Development image ----
FROM base AS development

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

WORKDIR /app

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "multi_user_transcriber.py", "dev"]


# ---- Production image ----
FROM base AS production

WORKDIR /app

# Copy the pre-built virtualenv and application source
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

# Remove pip to reduce attack surface in production
RUN pip uninstall -y pip

# Un-privileged user running the application
ARG DOCKER_USER
USER ${DOCKER_USER}

CMD ["python", "multi_user_transcriber.py", "start"]
