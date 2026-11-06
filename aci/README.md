# aci/ —— 神通 ACI 客户端库

STPython 是编译扩展，运行期要 `dlopen` 厂商的 `libaci.so`（Windows 上对应 `ACI.dll`）。
镜像里通过 `LD_LIBRARY_PATH=/opt/st-mcp/aci` 指向本目录。

- 本目录的 `libaci.so` 取自官网 aarch64 介质 `ShenTong7.0.8_342.185_arm64`
  解包后的 `drivers/aci/arm64/libaci.so`（2,409,448 字节）。
- **版本必须与目标机上的数据库一致**，否则握手会失败。现场装完库后
  `shentong/install-shentong.sh` 会用 `$SZ_OSCAR_HOME/drivers/aci/arm64/libaci.so`
  覆盖本文件，再构建镜像即匹配。
- 换架构/换 Python 版本时：ACI 与 STPython 必须同为 64 位、版本一致；
  `ldd libaci.so` 只需 libpthread/libstdc++/libdl/libgcc_s/libc/libm，Debian slim 均自带。
