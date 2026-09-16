# base image
FROM node:22.22.1-alpine@sha256:8094c002d08262dba12645a3b4a15cd6cd627d30bc782f53229a2ec13ee22a00 AS base
LABEL maintainer="takatost@gmail.com"

# if you located in China, you can use aliyun mirror to speed up
# RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# Registry is a build argument so a deployment behind a slow or blocked route to
# the public registry can point at a mirror without editing this file, e.g.
#   docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com web
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm config set registry "$NPM_REGISTRY"

RUN apk add --no-cache tzdata
RUN corepack enable
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ARG NEXT_PUBLIC_BASE_PATH=""
ENV NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH"


# install packages
FROM base AS packages

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml /app/
COPY web/package.json /app/web/
COPY e2e/package.json /app/e2e/
COPY sdks/nodejs-client/package.json /app/sdks/nodejs-client/
COPY packages /app/packages

# Use packageManager from package.json
RUN corepack install

# Install workspace dependencies for the Docker build environment.
RUN VITE_GIT_HOOKS=0 pnpm install --no-frozen-lockfile --config.runtime-on-fail=ignore

# build resources
FROM base AS builder
WORKDIR /app
COPY --from=packages /app/ .
COPY . .

WORKDIR /app/web
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV pnpm_config_verify_deps_before_run=false
RUN pnpm build && mkdir -p dist/standalone


# production stage
FROM base AS production

ENV NODE_ENV=production
ENV EDITION=SELF_HOSTED
ENV DEPLOY_ENV=PRODUCTION
ENV CONSOLE_API_URL=http://127.0.0.1:5001
ENV APP_API_URL=http://127.0.0.1:5001
ENV MARKETPLACE_API_URL=https://marketplace.dify.ai
ENV MARKETPLACE_URL=https://marketplace.dify.ai
ENV PORT=3000
ENV EXPERIMENTAL_ENABLE_VINEXT=false
ENV NEXT_TELEMETRY_DISABLED=1

# set timezone
ENV TZ=UTC
RUN ln -s /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone

# Create non-root user
ARG dify_uid=1001
RUN addgroup -S -g ${dify_uid} dify && \
    adduser -S -u ${dify_uid} -G dify -s /bin/ash -h /home/dify dify && \
    mkdir /app && \
    chown -R dify:dify /app


WORKDIR /app

COPY --from=builder --chown=dify:dify /app/web/public ./targets/next/web/public
COPY --from=builder --chown=dify:dify /app/web/.next/standalone ./targets/next/
COPY --from=builder --chown=dify:dify /app/web/.next/static ./targets/next/web/.next/static
COPY --from=builder --chown=dify:dify /app/web/dist/standalone ./targets/vinext

COPY --chown=dify:dify --chmod=755 web/docker/entrypoint.sh ./entrypoint.sh

ARG COMMIT_SHA
ENV COMMIT_SHA=${COMMIT_SHA}

USER dify
EXPOSE 3000
ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]
