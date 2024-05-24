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

# Remove the existing default.conf
RUN rm /etc/nginx/conf.d/default.conf

# Copy your custom Nginx configuration file
COPY nginx/nginx.conf /etc/nginx/conf.d/

# Copy SSL certificate and key
COPY nginx/cert.crt /etc/ssl/certs/
COPY nginx/cert.key /etc/ssl/private/

EXPOSE 80
EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
