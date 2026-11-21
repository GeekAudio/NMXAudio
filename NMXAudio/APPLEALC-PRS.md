# AppleALC PR 纳入清单

NMXAudio 1.1.1 的成品包含这份清单对应的 AppleALC 资源。通过 `alcid=` 或板载控制器的 `layout-id` 选择布局。 [下载成品](https://github.com/GeekAudio/NMXAudio/releases/tag/v1.1.1) · [安装与回退](DEPLOYMENT.md)

本次导入基于 AppleALC `ff01db21749271916d44605503499ceaa61d94b2`，纳入所记录的 15 个 PR。每个 PR 的原始提交都保留在 Git 历史中。完整哈希、受影响路径和验证记录见 [AppleALC-IMPORT.json](AppleALC-IMPORT.json)。

| AppleALC PR | 内容 | 原作者 | 原始提交 | 本仓库合并提交 |
| --- | --- | --- | --- | --- |
| [#966](https://github.com/acidanthera/AppleALC/pull/966) | feat: Bring Conexant CX8071 for Axioo Hype 7 AMD X7-2 | kodeaqua | `e40d02232bb7` | `7500be2dd17b` |
| [#969](https://github.com/acidanthera/AppleALC/pull/969) | Added CX11970 layout 15 for VAIO VJFE59 | raphaelallain | `328ad6cd31ce` | `66c6539c9da2` |
| [#971](https://github.com/acidanthera/AppleALC/pull/971) | Add ALCS1200A layout 81 for MSI MAG B460 TORPEDO | musatDev | `5c84be919de3` | `32ec4c5282b0` |
| [#972](https://github.com/acidanthera/AppleALC/pull/972) | Add ALC897 layout-id 14 for Asus TUF Gaming Z690-Plus WiFi D4 | GeekAudio | `db96b40dec4f` | `8d365858da13` |
| [#973](https://github.com/acidanthera/AppleALC/pull/973) | Add ALC230 layout-id 21 for Lenovo B330-15IKBR by maiconjs | maiconjs | `36f0e25c73c6` | `500091a36c29` |
| [#974](https://github.com/acidanthera/AppleALC/pull/974) | add ALCS1220A layout-id=12 for ROG MAXIMUS XI GENE by Xezun | Xezun | `814018a9ef9f` | `7574c28e751f` |
| [#975](https://github.com/acidanthera/AppleALC/pull/975) | ALC287: add layout 99 for Lenovo Legion IAX10H shared-DAC speakers | Ersiter | `a9b406e7322c` | `61d8cd83dd2d` |
| [#976](https://github.com/acidanthera/AppleALC/pull/976) | fang - Realtek ALC256 for Xiaomi RedmiBook 13 (TIMI TM1921) | jxnu2000fang | `deee3c33093c` | `6cd1d64f950f` |
| [#977](https://github.com/acidanthera/AppleALC/pull/977) | Add support for Conexant SN6140 for Lenovo Ideapad Slim 5 14AHP10 | Ardaninho | `68cd628576c8` | `3105dd72d67f` |
| [#978](https://github.com/acidanthera/AppleALC/pull/978) | ALC294: add layout 29 for ASUS ROG STRIX G731GT-H7114 | bygadd | `9a87eb50b433` | `c452d3c12484` |
| [#979](https://github.com/acidanthera/AppleALC/pull/979) | ALC285: Layout ID 16 for ASUS ROG Zephyrus G16 2024 (GU605MI) | pranshoe | `62cfe06253f4` | `a1237b8628ae` |
| [#980](https://github.com/acidanthera/AppleALC/pull/980) | Add custom layoutid=19 for Lenovo Thinkstation P340 | hgsyantai | `9615ec65d551` | `9da741579a2c` |
| [#981](https://github.com/acidanthera/AppleALC/pull/981) | ALC283: add layout 99 for Acer Aspire E5-572G | qianzhoushi | `06cd260af07d` | `0338e9543b6b` |
| [#982](https://github.com/acidanthera/AppleALC/pull/982) | ALC269: add layout-id 269 for Clevo NH50RA (Hasee Z7M-CT5NA) | Tang1124 | `5ecae55f2bba` | `cc3eb3148cfc` |
| [#983](https://github.com/acidanthera/AppleALC/pull/983) | ALC235: add CTIA combo-jack COEF and wake verbs for Intel NUC 8 (layout-id 8) | bnqzzdf | `436853cf4913` | `adad4b25f231` |

## 合并处理

- #969、#971、#978、#982 共改了 PinConfigs 表。按 `(CodecID, LayoutID, FuncGroup)` 合并各自新增记录，并检查原有记录。
- #973 在 `Changelog.md` 有合并重叠，已保留对应布局及 PinConfigs。
- #980 的 ALC623 layout 19 原先有文件放在仓库根目录。提交 `2aa6cb629fc2` 将 Lenovo P340 的 layout 和 platform 归入 `Resources/ALC623/`，将原作者的 PinConfigs 项登记一次。
- #972 对应本机实测的 ALC897 layout 14。#983 的 COEF 和唤醒 verbs 随 AppleALC 资源纳入，适用设备仍需分别进行实机验证。

资源检查覆盖 XML、plist、引用路径及 677 条 PinConfigs。Tahoe 主机的 AppleALC 路径读取原格式资源。原生 HDA 后端只应用自己支持的路由和初始化数据。新增布局继续使用 [AppleALC 资源贡献方式](../CONTRIBUTING.md)。
