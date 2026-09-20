# Dev image. Production uses a separate multi-stage build in phase 4.
FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
# npm ci runs as root and compose seeds the anonymous node_modules volume from
# this layer, so a non-root DOCKER_UID could not write node_modules/.vite and
# every dependency request 504'd as "Outdated Optimize Dep". a+rwX rather than
# a fixed chown so it holds for any uid; in the same RUN so it doesn't
# duplicate the whole layer.
RUN npm ci && chmod -R a+rwX /app/node_modules

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
