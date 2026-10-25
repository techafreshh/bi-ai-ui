FROM node:22-alpine
WORKDIR /app
# Manifest + lockfile first so the layer caches across code-only changes.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
EXPOSE 5173
CMD ["npm", "run", "dev"]
