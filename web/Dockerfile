# Stage 1: Build React/Vite app
FROM node:20-alpine AS builder

WORKDIR /app

# 依赖安装
COPY web/package*.json ./
RUN npm install

# 复制前端代码并打包
COPY web/ ./
RUN npm run build

# Stage 2: Serve via Nginx
FROM nginx:alpine

# 复制构建好的静态文件到 Nginx 目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 覆盖默认的 Nginx 配置文件 (启用 API 代理和 History API)
COPY web/nginx.conf /etc/nginx/conf.d/default.conf

# 暴露 80 端口
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
