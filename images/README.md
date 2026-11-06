# images/ —— 基础镜像归档位（离线部署用）

本仓库不含镜像 tar。两种方式拿到 `01-python-3.11-slim.tar.gz`：

**A. 目标机能联网 / 已 pull 过基础镜像**：不放文件即可。
`scripts/build-image.sh` 检测到没有归档就直接用本机 `python:3.11-slim`。

**B. 目标机完全断网**（内网/涉网隔离环境）：在联网机器上导出后拷过来。

```sh
docker pull --platform linux/arm64 python:3.11-slim
docker save python:3.11-slim | gzip -1 > images/01-python-3.11-slim.tar.gz
```

文件名必须是 `01-python-3.11-slim.tar.gz`（`load_image_archive` 按这个名字找）。
约 48 MB，`.gitignore` 已排除，不要提交进仓库。

装好镜像后想打包成"下次直接 load"的形态：

```sh
sh scripts/build-image.sh --save      # 产物 images/02-st-mcp-<tag>-arm64.tar.gz
# 之后 deploy.conf 里 IMAGE_MODE=load 即可跳过构建
```
