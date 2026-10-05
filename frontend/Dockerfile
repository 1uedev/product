# syntax=docker/dockerfile:1.7
FROM node:24.21.0-alpine3.24 AS base
ENV NEXT_TELEMETRY_DISABLED=1 PNPM_HOME=/pnpm CI=true
ENV PATH=$PNPM_HOME:$PATH
# Optional extra CA bundle for build networks with TLS inspection (EXTRA_CA_BUNDLE=/path/ca.pem docker compose build)
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm install -g pnpm@10.28.0 --no-fund --no-audit
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=secret,id=extra_ca --mount=type=cache,target=/pnpm/store \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

FROM deps AS build
COPY . .
RUN pnpm build

FROM node:24.21.0-alpine3.24 AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
