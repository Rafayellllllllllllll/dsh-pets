# 千夏像素宠物

粉丝向像素宠物。4×4 动作图由 AI 辅助生成，再适配成 DSH Go 的像素宠物包。这里不把画面说成全部原创，也不为角色声明开源许可。

## 状态

本地格式检查已通过。这不能代替在 DSH Go App 里导入。

安装包是 [`packages/qianxia-pixel-1.zip`](packages/qianxia-pixel-1.zip)。

| 项 | 值 |
| --- | --- |
| 显示名 | 千夏像素宠物 |
| 包内 id | `qianxia-pixel` |
| 大小 | 25760 字节 |
| sha256 | `9e7df3c180dce1580e3e9b62bb06624f7c27ce10789edf35d046bbf116292604` |

zip 顶层只有 `pack.json` 和静态 RGBA PNG `qianxia.png`。`preview/` 里的图只用于查看，不在安装包里。本仓库不代为投稿。

## 画面

源图 [`source/qianxia-4x4.jpg`](source/qianxia-4x4.jpg) 是 1024×1024 的基线 JPEG，306567 字节，sha256 `3f4b27a673b00243f0c9da16a5e1d3770a85467fa8854fd4eae05b7fa20684f9`。

图集为 4 列，单格 67×58，`pixelArt: true`，`scale: 1`。16 帧的身体底边都在第 55 行，`anchor.baseline` 是 56。站立、书写、举手、害羞和趴睡都来自这张源图。没有走路循环，所以 `roam.anim` 用 `idle`，`roam.faces` 为 `front`。

## 动作

| 名称 | 用途 |
| --- | --- |
| `idle` | `states.idle` |
| `thinking` | `states.thinking`，也给 `sweeping` |
| `working` | `states.working`，也给 `juggling` |
| `notification` | `states.notification` |
| `attention` | `states.attention` |
| `error` | `states.error` |
| `sleeping` | `states.sleeping` |
| `waking` | `states.waking`，不循环 |
| `humming` | `idlePool` |
| `wave` | `tapLeft`、`tapRight`，不循环 |
| `shy` | `tickle`，不循环 |
| `floppy` | `drag` |

`carrying` 按作者指南当前不会触发，没有单独动画。

## 重新生成

在仓库根目录执行：

```sh
cd pets/qianxia
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_pet.py
```

`.venv` 不会提交。脚本读取 `source/qianxia-4x4.jpg`，写出 `pack/`、`preview/` 和 `packages/qianxia-pixel-1.zip`。

## 检查

在仓库根目录执行：

```sh
python3 scripts/validate_pet_pack.py pets/qianxia/pack pets/qianxia/packages/qianxia-pixel-1.zip --display-name 千夏像素宠物
```

脚本先对照公开测试向量 [`scripts/testdata/pet-pack-local-v1.json`](../../scripts/testdata/pet-pack-local-v1.json)（rulesRevision 3），再检查这份包的字段、PNG 和 zip。通过只表示本地格式检查通过。

手机上的导入入口是：设置 → 宠物 → 选择器最后一格「导入」。

格式说明：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/pets/README.md>

使用说明：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/guide/zh/06-pets.md>
