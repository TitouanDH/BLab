# étape de build
FROM node:lts-alpine as build-stage
WORKDIR /app
COPY package*.json ./
RUN /usr/local/bin/npm install   # Specify the full path to npm
COPY . .
# Which deployment this is (production / preprod) and its commit, shown by the pre-prod banner
ARG BLAB_STAGE=production
ARG GIT_COMMIT=unknown
ENV VITE_BLAB_STAGE=$BLAB_STAGE VITE_GIT_COMMIT=$GIT_COMMIT
RUN /usr/local/bin/npm run build   # Specify the full path to npm

# étape de production
FROM nginx:stable-alpine as production-stage
COPY --from=build-stage /app/dist /usr/share/nginx/html

# Remove the existing default.conf
RUN rm /etc/nginx/conf.d/default.conf

# Copy your custom Nginx configuration file
COPY nginx/nginx.conf /etc/nginx/conf.d/

# The TLS certificate and key are mounted at runtime from TLS_DIR (docker-compose.yml)

EXPOSE 80
EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
