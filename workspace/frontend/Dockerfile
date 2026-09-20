# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=https://api.placement-ai.com
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_ENABLE_DMS=false
ARG NEXT_PUBLIC_ENABLE_INBOX=false
ARG NEXT_PUBLIC_ENABLE_TASKS=false
ARG NEXT_PUBLIC_ENABLE_WORKFLOWS=false
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY} \
    NEXT_PUBLIC_ENABLE_DMS=${NEXT_PUBLIC_ENABLE_DMS} \
    NEXT_PUBLIC_ENABLE_INBOX=${NEXT_PUBLIC_ENABLE_INBOX} \
    NEXT_PUBLIC_ENABLE_TASKS=${NEXT_PUBLIC_ENABLE_TASKS} \
    NEXT_PUBLIC_ENABLE_WORKFLOWS=${NEXT_PUBLIC_ENABLE_WORKFLOWS} \
    NEXT_TELEMETRY_DISABLED=1

# webpack remains required because BuildKit/Turbopack has produced stale chunks
# for this repository even when the build reports success.
RUN npx next build --webpack

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
