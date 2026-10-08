FROM node:24-alpine
WORKDIR /app
COPY bridge.mjs official-api.mjs package.json ./
COPY node_modules ./node_modules
ENV STATE_DIR=/state
EXPOSE 1238
CMD ["node", "bridge.mjs"]
