# étape de build
FROM node:lts-alpine as build-stage
WORKDIR /app
COPY package*.json ./
RUN /usr/local/bin/npm install   # Specify the full path to npm
COPY . .
RUN /usr/local/bin/npm run build   # Specify the full path to npm

# étape de production
FROM nginx:stable-alpine as production-stage
COPY --from=build-stage /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]