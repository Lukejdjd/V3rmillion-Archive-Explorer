FROM node:20-bookworm-slim

ARG IFRAMELY_COMMIT=7e4b01d4d504c21d1f69f5fa17088b06c29b813e

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /iframely

RUN git init \
    && git remote add origin https://github.com/itteco/iframely.git \
    && git fetch --depth 1 origin "${IFRAMELY_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && rm -rf .git \
    && npm install --omit=dev --no-audit --no-fund

COPY config.local.js /iframely/config.local.js

RUN chown -R node:node /iframely

USER node
ENV NODE_ENV=local HOST=0.0.0.0 PORT=8061
EXPOSE 8061

CMD ["node", "cluster.js"]

