# Build context for the web dev-server image.
FROM node:22-alpine

WORKDIR /app

# Install from the committed lockfile for reproducible deps.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
