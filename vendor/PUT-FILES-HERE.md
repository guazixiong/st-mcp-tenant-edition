# vendor/ —— 厂商介质投递位

**本包已经把必需的东西都带上了**，正常部署不需要再放任何文件：

| 内容 | 位置 | 状态 |
|---|---|---|
| OSCAR V7.0.8 **aarch64** 服务端介质 | `vendor/ShenTong7.0.8_342.185_arm64.zip`（665 MB，官网 ARM64 包） | 已就位 |
| **STPython** Python 驱动（cp311 / linux_aarch64） | `wheels/STPython-2.0.19-cp311-cp311-linux_aarch64.whl` | 已就位 |
| ACI 客户端共享库 | `aci/libaci.so`（2,409,448 字节，取自介质 `drivers/aci/arm64/`） | 已就位 |
| license 授权 | 介质自带**试用许可**（版本串 `(license invalid after 90 days)`） | 正式授权需替换 |

也就是说：**从装库到 MCP 连库，整条链路不需要外网，也不需要再找厂商要文件。**

## 什么时候才需要往这里放东西

1. **要连的库版本与本包介质不同**（比如现场已有 V7.0.5 或更新的 V7.0.9）。
   Python 驱动的 ABI 只跟 **CPython 版本 + CPU 架构**有关，与库版本无关，所以 `wheels/` 那份通常可继续用；
   但 ACI 的 `libaci.so` 建议换成**被连库自己**的那份，避免协议/符号差异：
   ```sh
   cp $SZ_OSCAR_HOME/drivers/aci/arm64/libaci.so aci/libaci.so
   IMAGE_MODE=build sh install-all.sh
   ```
2. **目标机是 x86_64 而不是 aarch64**。本包所有二进制均为 arm64，换架构必须重新打包
   （驱动取该架构介质里的 `drivers/STPython/Python311/*linux64.whl`，基础镜像不变）。
3. **厂商给了更新的服务端介质**，希望由本包顺带装新版本：把新包放进 `vendor/`，
   并把旧介质移走（脚本按文件名里是否含 `aarch64/arm64/arm` 选取，名字含 `Gauss` 的会被跳过）。

## 驱动版本从哪来（已装库的机器上自查）

```sh
ls /opt/ShenTong/drivers/STPython/            # Python35 … Python311，最高只到 311
ls /opt/ShenTong/drivers/STPython/Python311/  # STPython-2.0.19-cp311-cp311-linux_aarch64.whl
ls /opt/ShenTong/drivers/aci/                 # arm64/（aarch64）、linux64/（x86_64）
```

`shentong/install-shentong.sh` 装完库会自动把这两样收割回 `wheels/` 与 `aci/`（内容一致则跳过）。

## 放好后怎么确认生效

```sh
ls wheels/STPython-*.whl aci/libaci.so     # 两个都在才会被 Dockerfile.offline 打进镜像
sh install-all.sh                          # 默认 IMAGE_MODE=build，自动重建镜像
sh scripts/verify.sh                       # 期望 19 PASS / 0 FAIL，末尾 CONNECT_OK
```
