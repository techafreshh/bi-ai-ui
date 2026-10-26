# The SPA ships as a prebuilt bundle (web/dist is committed) so that a fresh
# `docker compose up` needs no npm/node install at all — the container only
# pulls nginx:alpine and serves static files. Rebuild dist after editing the
# SPA: `cd web && npm run build` (see README).
FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist/ /usr/share/nginx/html

EXPOSE 5173
