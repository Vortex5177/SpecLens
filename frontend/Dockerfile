# 前端镜像：多阶段构建
# 阶段 1：node 构建产物
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npm run build

# 阶段 2：nginx 托管静态文件，并反向代理 /api 到后端
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
