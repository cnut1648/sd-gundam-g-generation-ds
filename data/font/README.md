# 12×12 中文字库来源

当前构建的中文字符使用
[Fusion Pixel Font（缝合像素字体）](https://github.com/TakWolf/fusion-pixel-font)
`12px proportional zh_hans`。它是一套开源泛中日韩像素字体，提供原生 12px
简体中文字形；项目仍沿用游戏原版的 12px 固定 advance，并在字形右、下、右下
补一像素的第二色阴影。

## 固定版本与复现

- 上游版本：[2026.05.07](https://github.com/TakWolf/fusion-pixel-font/releases/tag/2026.05.07)
- 文件：`fusion-pixel-12px-proportional-zh_hans.ttf`
- SHA-256：`7dda18bac79c841a9a545c45b3c2d9d00f1cbbca3217fd8d291dd27298932bbb`
- 在线预览：<https://fusion-pixel-font.takwolf.com/>
- 字体许可：SIL Open Font License 1.1，全文见 `FUSION_PIXEL_OFL.txt`

字体文件较大，不收入仓库；`atlas12.bin` 是构建直接读取、已固定哈希的字模源。
下载上述官方版本后，可复现或校验所有中文字形：

```bash
python build/regenerate_fusion_atlas.py --font /path/to/fusion-pixel-12px-proportional-zh_hans.ttf
python build/regenerate_fusion_atlas.py --font /path/to/fusion-pixel-12px-proportional-zh_hans.ttf --check
```

## 覆盖与适用性

`two_byte_zh` 登记的 2,085 个 CJK 字符中，Fusion 直接覆盖 2,084 个；唯一缺字
`赝` 保留此前的 WQY 12px 字模。65 个非 CJK 特殊字符也全部保留原字模，不随
本脚本重绘。

这套字体适合本项目，主要因为它是原生小字号像素字体、提供简体中文变体，且
OFL 1.1 允许修改、嵌入与再分发。限制也很明确：游戏渲染器不使用 proportional
字体的字宽信息，所有中文字仍按 12px 等宽排列；极密笔画和语言特定字形仍需
结合实机截图复核，缺字也必须保留可审计的 fallback。
