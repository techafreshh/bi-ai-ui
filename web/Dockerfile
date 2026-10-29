# Issue #88: multi-stage web image — dev server, build, static prod serve.
# Stage 1: development — Vite HMR on :5173 (previous single-stage behavior).
FROM node:22-alpine AS development

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]

# Stage 2: build — full install then vite build to dist/.
# Vite inlines VITE_* at build time, so the agent URL must arrive as a
# build arg here (there is no runtime env read in the nginx stage).
FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_AGENT_URL=http://localhost:8123/copilotkit
ENV VITE_AGENT_URL=$VITE_AGENT_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 3: production — static-only nginx, no Node/npm/vite.
FROM nginx:1.27-alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# BusyBox wget probe (no new packages): web is up when / serves 200.
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=10s \
  CMD wget -qO- http://localhost/ > /dev/null || exit 1
