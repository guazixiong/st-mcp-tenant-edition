# wheels/ —— Python 依赖轮子投递位

本仓库**不含任何 `.whl`**（二进制不进 git）。离线部署前先把轮子备齐：

| 需要的文件 | 从哪来 |
|---|---|
| `requirements.txt` 里全部开源轮子（cp311 / aarch64） | 联网机器上跑 `sh tools/fetch-offline-deps.sh`，产物拷到本目录 |
| `st_mcp-0.1.0-py3-none-any.whl` | 本仓库自己构建：`scripts/build-image.sh` 会调用 `pip wheel`；或 `python -m build` 在 `src/` 下 |
| `STPython-2.0.19-cp311-cp311-linux_aarch64.whl` | **厂商闭源驱动，仓库不提供**，见 `vendor/PUT-FILES-HERE.md` |

构建镜像时 `docker/Dockerfile.offline` 整目录 `COPY wheels/`，所以缺哪个就在装出来的镜像里缺哪个。
`scripts/build-image.sh` 会在构建前逐项检查并明确警告缺 STPython / 缺 libaci.so。

`IMAGE_MODE=build`（默认）用本目录离线装。轮子不全也能起服务和工具列表，
但 `execute_sql` 会报驱动缺失——`scripts/build-image.sh` 会提前警告，别等运行期才发现。
