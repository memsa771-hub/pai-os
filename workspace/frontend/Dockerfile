FROM node:20-alpine

WORKDIR /app

ARG NEXT_PUBLIC_API_URL=https://api.placement-ai.com
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ARG NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL}
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY}

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy application code
COPY . .

# --webpack, not `npm run build` (Turbopack, Next 16's default): under
# BuildKit's RUN specifically, Turbopack has reproducibly emitted a build that
# "succeeds" but silently omits recent source changes from some client chunks
# — confirmed by diffing this exact source built via BuildKit vs. via a plain
# `docker run`/local `next build`, both Turbopack, same source, different
# output. Running the build twice in the same RUN did not fix it either, so
# this isn't a cold-cache issue — something about Turbopack's worker
# processes under BuildKit's execution environment. webpack does not
# reproduce it and is the same bundler `next dev`/local builds already work
# with elsewhere in this repo.
RUN npx next build --webpack

# Run
EXPOSE 3000
CMD ["npm", "start"]
