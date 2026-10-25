FROM node:22-alpine
WORKDIR /app
# Playwright is a devDependency for docs/screenshot capture on the host; the
# runtime image must not pull browser binaries (keeps the layer lean).
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# Manifest + lockfile first so the layer caches across code-only changes.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
EXPOSE 5173
CMD ["npm", "run", "dev"]
